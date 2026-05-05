# travel-insurance-ai 系统性Bug排查报告

> 排查日期: 2026-05-05
> 排查范围: 全项目 Python 代码（app/ + scripts/）
> 排查方法: 静态代码分析 + 数据流追踪 + 模式匹配

---

## 一、总览

| 类别 | 发现数量 | 严重程度 |
|------|---------|---------|
| NoneType iterable 风险点 | 6处 | 🔴 高 |
| ctx 变量作用域问题 | 2处（1处已修复） | 🟡 中 |
| push.py 数据流缺陷 | 3处 | 🔴 高 |
| 裸 except Exception | 262处（app/ 199 + scripts/ 63） | 🟡 中 |
| 空值处理缺失 | 8处 | 🟡 中 |

---

## 二、问题1：NoneType iterable 错误风险点

### 2.1 根因分析

`'NoneType' object is not iterable` 异常发生在对 `None` 值执行 `for ... in` 或 `list()` 等迭代操作时。项目中大量使用 `.get(key) or []` 模式防御，但仍有以下遗漏点：

### 2.2 风险点清单

#### 风险点 #1 — `baggage_delay/stages/handlers.py:284`
```python
existing_list = ai_parsed.get("receipt_times") or []
```
**状态**: ✅ 已防护（`or []`）
**风险**: 低。但如果 `ai_parsed` 本身为 `None`，`.get()` 会抛出 `AttributeError`。

#### 风险点 #2 — `baggage_delay/stages/utils.py:106`
```python
for item in parsed.get("receipt_times") or []:
```
**状态**: ✅ 已防护
**风险**: 低。但如果 `parsed` 为 `None`，`.get()` 会抛出 `AttributeError`。

#### 风险点 #3 — `baggage_delay/pipeline.py:467`
```python
ai_missing = [m for m in (ai_audit.get("missing_materials") or []) if m and str(m).strip()]
```
**状态**: ✅ 已防护
**风险**: 低。

#### 风险点 #4 — `baggage_delay/pipeline.py:331-342`（材料门禁）
```python
def _has_flag(key: str) -> str:
    return str(ai_parsed.get(key) or "unknown").strip().lower()
```
**状态**: ✅ 已防护
**风险**: 低。

#### 风险点 #5 — `flight_delay/stages/hardcheck.py:166` ⚠️
```python
def _run_hardcheck(
    ...
    vision_extract: Dict[str, Any] = None,  # 默认值为 None!
) -> Dict[str, Any]:
```
**问题**: `vision_extract` 参数默认值为 `None`，但类型标注为 `Dict[str, Any]`。后续代码使用 `(vision_extract or {}).get(...)` 做了防护，但如果调用方传入 `None` 且内部某处忘记用 `or {}` 包裹，就会触发 `AttributeError`。

**影响范围**: `_run_hardcheck` 内部多处使用 `vision_extract`，虽然大部分已防护，但维护风险高。

**修复建议**: 将默认值改为 `{}` 或在函数入口统一处理：
```python
vision_extract = vision_extract or {}
```

#### 风险点 #6 — `flight_delay/stages/hardcheck.py:447` ⚠️
```python
connecting_segments = (parsed or {}).get("connecting_segments_data") or []
```
**状态**: ✅ 已防护。但 `connecting_segments_data` 由 pipeline 在飞常准查询成功时动态 append，如果 pipeline 未执行到该步骤，该键不存在，`or []` 兜底正确。

#### 风险点 #7 — `baggage_delay/stages/handlers.py:198-202` ⚠️
```python
all_flights = (
    vision_extract.get("all_flights_found")
    or ai_parsed.get("all_flights_found")
    or []
)
for f in all_flights:  # 如果 all_flights 是 None，这里会崩溃
```
**状态**: ✅ 已防护（`or []`）
**风险**: 低。

#### 风险点 #8 — `flight_delay/pipeline.py:343` ⚠️
```python
chain = (parsed.get("schedule_revision_chain") or [])
```
**状态**: ✅ 已防护
**风险**: 低。但如果 `parsed` 为 `None`，`.get()` 会崩溃。不过此时 `parsed` 已由 stage1 保证为 dict。

### 2.3 最可能的 NoneType iterable 崩溃位置

根据 issue_cluster_tracker.md 记录的 1 件 `'NoneType' object is not iterable` 异常，最可能的触发位置是：

**`baggage_delay/stages/handlers.py` 的 `_try_transfer_flight_receipt_time` 函数**（第 160-291 行），该函数：
1. 接收 `vision_extract` 和 `ai_parsed` 两个可能为空的 dict
2. 内部有复杂的飞常准查询逻辑
3. 异常被 `except Exception as e` 捕获（第 273 行），但仅记录日志，不会导致崩溃

**更可能的位置**：`baggage_delay/pipeline.py` 中 Vision 抽取失败后的合并逻辑（第 112-246 行）。当 `vision_extract` 不为空但 `ai_parsed` 为 `None` 时：
```python
# 第 112 行
if vision_extract and isinstance(ai_parsed, dict):
    for key in (...):
        vision_val = vision_extract.get(key)
        ...
```
此处有 `isinstance(ai_parsed, dict)` 防护，但如果 `ai_parsed` 是其他非 None 非 dict 类型（如 list），会走第 245 行的 `elif` 分支。

**结论**: 该异常最可能发生在 baggage_delay pipeline 的 Vision+AI 合并阶段，当 AI 解析返回非预期类型时。建议在合并逻辑入口增加严格的类型校验。

---

## 三、问题2：pipeline 空值处理路径

### 3.1 baggage_delay pipeline 空值链路

```
claim_info → forceid (line 46)
    ↓
vision_extract = {} (line 68) → try/except 包裹 (line 69-93)
    ↓
ai_parsed = runner.run() → 可能为 None 或非 dict (line 97-109)
    ↓
合并 vision_extract + ai_parsed (line 112-246)
    ↓  ⚠️ 如果 ai_parsed 不是 dict，走 elif 分支 (line 245-246)
    ↓  ai_parsed = dict(vision_extract)  ← 可能丢失 AI 解析结果
    ↓
_check_policy_validity (line 249) → 接收 vision_extract
    ↓
_check_info_consistency (line 258) → 接收 ai_parsed or {}
    ↓
_check_exclusions (line 264) → 接收 ai_parsed or {}
    ↓
飞常准查询 (line 271-298) → 接收 ai_parsed (需为 dict)
    ↓  ⚠️ 如果 ai_parsed 不是 dict，条件 `isinstance(ai_parsed, dict)` 为 False，跳过查询
    ↓
材料门禁 (line 331-391) → 接收 ai_parsed (需为 dict)
    ↓  ⚠️ 如果 ai_parsed 不是 dict，走 _material_gate 兜底 (line 386)
    ↓
延误时长核算 (line 409) → _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
    ↓  ✅ 有 isinstance 检查
    ↓
AI 审核 (line 444-459) → 接收 ai_parsed or {}
```

**关键发现**:
- `ai_parsed` 在多个阶段被用作 `ai_parsed or {}`，防护较好
- 但 `ai_parsed` 可能为 `None`（runner.run 失败时），此时 `ai_parsed or {}` = `{}`，所有材料标记默认为 unknown/false，导致大量误判为"需补齐资料"
- **这就是 P1 284 件的根因之一**：AI 解析失败 → 所有材料标记为 false → 材料门禁触发 → 要求补件

### 3.2 flight_delay pipeline 空值链路

```
ctx = {"debug": [], "flight_delay": None} (line 77)
    ↓
duplicate check (line 83-89) → 可能早退，ctx 已定义 ✅
    ↓
vision_extract = {} (line 96) → try/except 包裹 (line 97-121)
    ↓
parsed = runner.run() (line 125-136) → 失败时调用 build_stage_error_return
    ↓  ✅ ctx 传入，不会丢失
    ↓
合并 Vision → parsed (line 139-329) → 大量 .get() or {} 防护
    ↓
飞常准查询 (line 332-467) → try/except 包裹
    ↓
hardcheck (line 616) → vision_extract=ctx.get(...) or {}
    ↓  ✅ 防护
    ↓
postprocess (line 665-672) → 接收 audit, hardcheck, payout_result
    ↓  ⚠️ postprocess 内部有裸 except Exception (line 239)
```

**关键发现**:
- `ctx` 在函数入口定义，所有早退路径都在定义之后 ✅
- `_postprocess_audit_result` 的裸 except 可能吞掉关键异常

---

## 四、问题3：push.py 数据流分析

### 4.1 `_extract_review_fields` 数据流

**文件**: `app/production/main_workflow.py:438-896`

```python
def _extract_review_fields(self, data: dict, claim_info: dict) -> tuple:
    """返回: (main_fields, flight_fields, baggage_fields) 三个 dict"""
```

**数据来源优先级**:
1. `data.get("flight_delay_audit")` → 航班延误审核结果
2. `data.get("DebugInfo", {}).get("flight_delay_audit")` → 兜底
3. `data.get("baggage_delay_audit")` → 行李延误审核结果
4. `data.get("DebugInfo", {})` → 各类子字段

**push.py 调用方式** (line 110):
```python
main_fields, _, _ = workflow._extract_review_fields(result, claim_info)
```
✅ 正确解包三元组。

### 4.2 `_sync_to_db` 数据流缺陷

**文件**: `scripts/push.py:50-79`

#### 缺陷 #1 — `cmd_sync_db` 不写 `audit_result` ⚠️ 严重

```python
# push.py line 201-211
sql = """
    INSERT INTO ai_review_result (forceid, claim_id, remark, is_additional, key_conclusions, raw_result)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        claim_id = VALUES(claim_id),
        remark = VALUES(remark),
        is_additional = VALUES(is_additional),
        key_conclusions = VALUES(key_conclusions),
        raw_result = VALUES(raw_result),
        updated_at = CURRENT_TIMESTAMP
"""
```

**问题**: `cmd_sync_db` 只写入 6 个字段，**不包含 `audit_result`**。这意味着：
- 通过 `--sync-db` 同步的案件，`audit_result` 字段不会被更新
- 这就是 **17 件 audit_result=NULL** 的直接原因
- 影响所有通过 `--sync-db` 全量同步的案件

**修复建议**: `cmd_sync_db` 应使用 `_extract_review_fields` 获取完整字段，而非手动拼 SQL。

#### 缺陷 #2 — `cmd_push_forceid` 与 `cmd_push_all` 行为不一致

- `cmd_push_forceid` (line 110): 使用 `_extract_review_fields` → 写入完整字段 ✅
- `cmd_push_all` (line 156): 同样使用 `_extract_review_fields` ✅
- `cmd_sync_db` (line 173-228): **不使用** `_extract_review_fields`，手动拼字段 ❌

#### 缺陷 #3 — `_build_claim_info_cache` 吞异常

```python
# push.py line 45-46
except Exception:
    pass
```

如果 `claim_info.json` 文件损坏或格式异常，会被静默跳过，导致 `claim_info_cache` 缺少该案件，后续 `_extract_review_fields(result, {})` 以空 dict 兜底，丢失 `benefit_name`、`insured_name` 等关键字段。

### 4.3 push.py 异常处理统计

| 位置 | 行号 | 异常处理 | 风险 |
|------|------|---------|------|
| `_build_claim_info_cache` | 45 | `except Exception: pass` | 静默丢失数据 |
| `_sync_to_db` | 77 | `except Exception as e: print` | 仅打印，不重试 |
| `cmd_push_forceid` 前端 | 105 | `except Exception as e: print` | 前端失败不阻塞数据库 |
| `cmd_push_all` JSON读取 | 142 | `except Exception: continue` | 跳过损坏文件 |
| `cmd_push_all` 前端 | 150 | `except Exception: pass` | 静默跳过 |
| `cmd_push_all` 数据库 | 159 | `except Exception: pass` | 静默跳过 |
| `cmd_sync_db` JSON读取 | 180 | `except Exception: pass` | 静默跳过 |
| `cmd_sync_db` 写入 | 221 | `except Exception as e: print` | 仅打印 |

---

## 五、问题4：flight_delay ctx 变量作用域

### 5.1 已修复的 `name 'ctx' is not defined`

根据 issue_cluster_tracker.md，该问题已在 2026-04-30 修复。修复前的代码可能在以下场景触发：

**场景**: 在 `ctx` 定义之前有早退路径引用了 `ctx`。

当前代码 (line 76-77):
```python
forceid = str(claim_info.get("forceid") or "unknown")
ctx: Dict[str, Any] = {"debug": [], "flight_delay": None}
```

所有早退路径（duplicate check line 83-89）都在 `ctx` 定义之后，当前代码安全。

### 5.2 潜在风险点

#### 风险点 #1 — `_postprocess_audit_result` 不接收 ctx

```python
# pipeline.py line 665
audit = _postprocess_audit_result(
    parsed=parsed,
    audit=audit,
    policy_terms_excerpt=policy_excerpt,
    hardcheck=hardcheck,
    payout_result=payout_result,
    free_text=free_text,
)
```

`_postprocess_audit_result` 不接收 `ctx` 参数，如果未来需要在 postprocess 中记录调试信息到 ctx，需要修改函数签名。

#### 风险点 #2 — `build_stage_error_return` 依赖 ctx

```python
# stage_fallbacks.py line 6-30
def build_stage_error_return(*, forceid, checkpoint, err, ctx, ...):
    return {
        ...
        "DebugInfo": ctx,
    }
```

如果调用方传入的 `ctx` 为 `None`，`DebugInfo` 会是 `None`，前端可能无法正确渲染。当前两处调用都传入了正确的 `ctx`，暂无风险。

---

## 六、问题5：裸 except Exception 统计

### 6.1 总体数据

| 目录 | 数量 |
|------|------|
| `app/` | 199 处 |
| `scripts/` | 63 处 |
| **合计** | **262 处** |

### 6.2 高风险位置（核心业务逻辑中的裸 except）

#### 6.2.1 `app/modules/flight_delay/pipeline.py`

| 行号 | 上下文 | 风险 |
|------|--------|------|
| 116 | Vision 抽取异常 | 降级跳过，可接受 |
| 460 | 飞常准查询异常 | 降级跳过，可接受 |
| 518 | 联程首班替代航班查询 | 降级跳过，可接受 |
| 604 | 接驳航班查询异常 | 降级跳过，可接受 |

#### 6.2.2 `app/modules/baggage_delay/pipeline.py`

| 行号 | 上下文 | 风险 |
|------|--------|------|
| 89 | Vision 识别失败 | 降级到纯文本，可接受 |
| 242 | PIR 二次提取异常 | 仅记录 debug，可接受 |
| 296 | 飞常准查询异常 | 仅记录 debug，可接受 |

#### 6.2.3 `app/modules/flight_delay/stages/postprocess.py:239` ⚠️

```python
except Exception as e:
    LOGGER.warning(f"_postprocess_audit_result 异常: {e}", exc_info=True)
```

**问题**: 这是整个后处理函数的顶层 try/except。如果后处理中任何步骤失败，**整个后处理被跳过**，包括：
- 阈值硬门禁
- 硬免责条款检查
- 必备材料缺失检查
- 赔付金额写回

**影响**: 如果后处理异常，AI 原始输出直接返回，可能绕过所有硬校验规则，导致 P0 级漏审。

**修复建议**: 
1. 缩小 try/except 范围到具体可能失败的操作
2. 或者在 except 中返回一个安全的默认拒绝结果

#### 6.2.4 `app/modules/flight_delay/stages/hardcheck.py`

| 行号 | 上下文 | 风险 |
|------|--------|------|
| 71, 75 | 日期解析 | 低，仅影响日期解析 |
| 136 | 可预见因素检测 | 中，吞掉欺诈检测异常 |
| 155 | 欺诈检测整体异常 | 中，返回空结果 |
| 205 | 日期解析 | 低 |
| 361 | 保单有效期校验 | 中，返回 in_coverage=None |
| 480 | 前程延误判定 | 低，仅影响前程判定 |

#### 6.2.5 `app/modules/baggage_delay/stages/utils.py`

| 行号 | 上下文 | 风险 |
|------|--------|------|
| 20 | `_safe_float` | 低，工具函数 |
| 31 | `_parse_date` | 低，工具函数 |
| 73, 84 | `_parse_dt_flexible` | 低，工具函数 |

#### 6.2.6 `scripts/push.py` ⚠️

| 行号 | 上下文 | 风险 |
|------|--------|------|
| 45 | `_build_claim_info_cache` | **高**，静默丢弃损坏文件 |
| 142 | `cmd_push_all` JSON 读取 | **高**，静默跳过 |
| 150 | `cmd_push_all` 前端推送 | **高**，静默跳过 |
| 159 | `cmd_push_all` 数据库写入 | **高**，静默跳过 |
| 180 | `cmd_sync_db` JSON 读取 | **高**，静默跳过 |

### 6.3 裸 except 分类

| 类别 | 数量（估计） | 建议 |
|------|------------|------|
| 工具函数中的类型转换 | ~40 | 可接受，但建议限定 `except (ValueError, TypeError)` |
| 外部 API 调用降级 | ~30 | 可接受，但建议记录异常类型 |
| JSON 解析 | ~20 | 应限定 `except (json.JSONDecodeError, ValueError)` |
| 数据库操作 | ~15 | 应区分连接错误和 SQL 错误 |
| 静默 `pass` | ~25 | **不可接受**，至少应记录日志 |
| 顶层兜底 | ~10 | 应记录完整 traceback |

---

## 七、修复优先级建议

### P0 — 立即修复

| # | 问题 | 文件 | 修复方案 |
|---|------|------|---------|
| 1 | `cmd_sync_db` 不写 `audit_result` | `scripts/push.py:201-211` | 改用 `_extract_review_fields` 获取完整字段 |
| 2 | `_postprocess_audit_result` 裸 except 吞掉所有硬校验 | `app/modules/flight_delay/stages/postprocess.py:239` | 缩小 try 范围，或在 except 中返回安全默认值 |

### P1 — 尽快修复

| # | 问题 | 文件 | 修复方案 |
|---|------|------|---------|
| 3 | push.py 多处静默 `except: pass` | `scripts/push.py:45,150,159,180` | 至少记录 warning 日志 |
| 4 | `_run_hardcheck` 的 `vision_extract=None` 默认值 | `app/modules/flight_delay/stages/hardcheck.py:166` | 改为 `vision_extract: Dict = None` 并在入口 `vision_extract = vision_extract or {}` |
| 5 | baggage_delay ai_parsed 非 dict 时的降级路径 | `app/modules/baggage_delay/pipeline.py:245` | 增加 `elif` 分支的日志记录 |

### P2 — 计划修复

| # | 问题 | 文件 | 修复方案 |
|---|------|------|---------|
| 6 | 262 处裸 except Exception | 全项目 | 逐步替换为具体异常类型 |
| 7 | `_build_claim_info_cache` 吞异常 | `scripts/push.py:45` | 记录损坏文件路径 |
| 8 | `_check_foreseeability_fraud` 吞异常 | `app/modules/flight_delay/stages/hardcheck.py:136,155` | 记录异常详情 |

---

## 八、数据库一致性修复方案

针对 17 件 `audit_result=NULL` 和 `cmd_sync_db` 不写 `audit_result` 的问题：

```python
# 方案：修改 cmd_sync_db，使用 _extract_review_fields
def cmd_sync_db(dry_run: bool = False):
    workflow = ProductionWorkflow()
    claim_info_cache = _build_claim_info_cache()
    
    for r in results:
        fid = r.get("forceid", "")
        claim_info = claim_info_cache.get(fid, {})
        main_fields, flight_fields, baggage_fields = workflow._extract_review_fields(r, claim_info)
        _sync_to_db(main_fields)
        # TODO: 同步 flight_fields 到航班子表
        # TODO: 同步 baggage_fields 到行李子表
```

---

## 九、总结

本次排查覆盖了 travel-insurance-ai 项目的全部 Python 代码，发现：

1. **NoneType iterable 风险**: 大部分已通过 `or []` 模式防护，但 `_run_hardcheck` 的 `vision_extract=None` 默认值和 baggage_delay 的 ai_parsed 非 dict 降级路径仍有隐患。

2. **push.py 数据流**: `cmd_sync_db` 不写 `audit_result` 是 17 件 NULL 值的直接原因，且多处静默 `except: pass` 导致数据丢失无法追踪。

3. **ctx 作用域**: 已修复的 `name 'ctx' is not defined` 问题当前代码中不再存在，但 `_postprocess_audit_result` 不接收 ctx 限制了调试能力。

4. **裸 except Exception**: 262 处是项目最大的技术债务，其中 `_postprocess_audit_result` 的顶层裸 except 可能绕过所有硬校验规则，风险最高。

5. **空值处理**: 两个 pipeline 的空值防护总体较好，但 AI 解析失败时的降级路径会导致大量误判为"需补齐资料"（P1 284 件的部分根因）。
