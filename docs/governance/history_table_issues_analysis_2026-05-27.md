# 历史审核结果表问题分析报告

> 生成时间: 2026-05-27
> 目的: 分析 ai_review_history 表中发现的数据质量问题

---

## 问题一：manual 记录 benefit_name 全为 NULL

### 现象

`ai_review_history` 表中所有 `review_type='manual'` 的记录，`benefit_name` 字段全部为 NULL。导致 manual 历史记录无法按险种分类。

### 根因分析

**调用链路**:

```
main_workflow.py (人工状态同步)
  ↓
  调用 write_manual_history_if_changed(
      conn, forceid, manual_status, manual_conclusion,
      old_values=old_manual_row   ← 只传了旧值，没传 benefit_name
  )
  ↓
  history_helpers.py:189
  history = {
      'benefit_name': benefit_name,  ← 参数值
  }
  ↓
  调用处 main_workflow.py:1024
  write_manual_history_if_changed(conn, forceid, manual_status, manual_conclusion,
                                   old_values=old_manual_row)
                                   ← 没有传入 benefit_name 参数！
```

**代码位置**: [`app/production/main_workflow.py:1024`](app/production/main_workflow.py#L1024)

```python
# 当前代码（第1024行）—— 缺少 benefit_name 参数
write_manual_history_if_changed(conn, forceid, manual_status, manual_conclusion,
                                old_values=old_manual_row)
```

`write_manual_history_if_changed` 函数签名（[`app/db/history_helpers.py:147`](app/db/history_helpers.py#L147)）：
```python
def write_manual_history_if_changed(conn, forceid: str,
                                     new_manual_status: str,
                                     new_manual_conclusion: Optional[str] = None,
                                     benefit_name: Optional[str] = None,  ← 可选参数
                                     old_values: Optional[Dict[str, Any]] = None):
```

**结论**: `benefit_name` 参数已定义且可接受，但调用方从未传入。

### 修复方案

在 `main_workflow.py` 调用处补充 `benefit_name` 参数。此时 `benefit_name` 可以从当前行的 `manual_status` UPDATE 之前的主表中读取：

```python
# 在 UPDATE 前已读取 old_manual_row，同时读取 benefit_name
cur_pre.execute(
    "SELECT benefit_name, manual_status, manual_conclusion FROM ai_review_result WHERE forceid=%s",
    (forceid,)
)
old_row = cur_pre.fetchone()
benefit_name = old_row.get('benefit_name') if old_row else None

write_manual_history_if_changed(conn, forceid, manual_status, manual_conclusion,
                                benefit_name=benefit_name,
                                old_values=old_row)
```

**注意**: 需要在 `main_workflow.py` 第 1008 行的 SELECT 中增加 `benefit_name` 字段。

### 历史数据修复

已有 220 条 manual 记录 benefit_name 为 NULL。可通过关联 `ai_review_result` 表回填：

```sql
UPDATE ai_review_history h
JOIN ai_review_result r ON h.forceid = r.forceid
SET h.benefit_name = r.benefit_name
WHERE h.review_type = 'manual' AND h.benefit_name IS NULL;
```

---

## 问题二：3个版本字段 100% 为空

### 现象

| 字段 | 缺失率 | 说明 |
|------|--------|------|
| `ai_model_version` | 100% (1,474/1,474) | AI 模型版本号 |
| `pipeline_version` | 100% (1,474/1,474) | Pipeline 版本号 |
| `rule_ids_hit` | 100% (1,474/1,474) | 命中的规则ID |

### 根因分析

**数据流追踪**:

1. **定义**: 这3个字段在 [`app/db/database.py:255`](app/db/database.py#L255) 和 [`app/db/models.py:319-321`](app/db/models.py#L319-L321) 中定义

2. **写入主表**: [`main_workflow.py:877`](app/production/main_workflow.py#L877) 将其包含在 `main_keys` 中：
   ```python
   main_keys = {
       ...
       "ai_model_version", "pipeline_version", "rule_ids_hit", ...
   }
   ```

3. **从 `_extract_review_fields` 提取**: [`main_workflow.py:448`](app/production/main_workflow.py#L448) `_extract_review_fields()` 方法从 AI 返回的 JSON 中提取字段

4. **问题**: `_extract_review_fields` 方法**从未给这3个字段赋值**。AI 审核结果 JSON 中不包含这些字段，代码也没有从其他地方获取。

**代码验证**: 搜索整个 `app/production/main_workflow.py`，没有任何地方执行 `fields['ai_model_version'] = ...`、`fields['pipeline_version'] = ...` 或 `fields['rule_ids_hit'] = ...`。

**结论**: 这3个字段是**预留字段**，设计时预留了但从未在代码中赋值。

### 修复方案

#### ai_model_version
在 AI 调用层（[`app/modules/flight_delay/stages/ai_calls.py`](app/modules/flight_delay/stages/ai_calls.py)）获取模型版本：
```python
# 从 AI 客户端获取当前模型名
ai_model_version = client.model_name  # 如 'claude-sonnet-4-6'
```
或在 pipeline 中硬编码版本号（更简单）：
```python
main_fields['ai_model_version'] = 'v1.0'  # 每次发布更新版本号
```

#### pipeline_version
建议从代码版本自动生成，或在 pipeline 入口处设置：
```python
main_fields['pipeline_version'] = '2026-05-27'  # 或从 git commit 提取
```

#### rule_ids_hit
需要追踪哪些规则被命中。在 hardcheck 和 validators 执行后，收集命中的规则ID：
```python
# hardcheck 执行后
rule_ids = []
if hardcheck.get('transit_check', {}).get('is_domestic_cn'):
    rule_ids.append('HC_TRANSIT_CN')
if hardcheck.get('missed_connection_check', {}).get('is_missed_connection'):
    rule_ids.append('HC_MISSED_CONN')
# ... 收集所有命中的规则
main_fields['rule_ids_hit'] = ','.join(rule_ids) if rule_ids else ''
```

### 建议

如果这3个字段短期内不需要，可以从 `ai_review_history` 表中删除，减少噪音。

如果未来需要用于模型迭代评估，则应该现在开始写入。最简单的方案：

| 字段 | 写入方式 | 写入频率 |
|------|---------|---------|
| ai_model_version | 在 pipeline 入口处硬编码 | 每次发布更新 |
| pipeline_version | git short SHA 或日期戳 | 每次部署 |
| rule_ids_hit | 在 hardcheck 执行后收集 | 每次审核 |
