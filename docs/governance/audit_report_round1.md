# Python 工程审计报告 — 第一轮

> 审计日期: 2026-04-27
> 审计范围: 核心业务文件（claim_ai_reviewer.py, database.py, main_workflow.py, baggage_delay/pipeline.py, audit_pipeline.py, workflow.py, openrouter_client.py 等）

---

## 一、A级问题（确认存在）

### 1. claim_ai_reviewer.py 大量死代码 + 嵌套函数定义

**文件**: `app/claim_ai_reviewer.py`
**风险等级**: 高
**证据等级**: A级

**问题描述**:
- 第1240-1247行：`_extract_section` 方法中 `return` 后面有永远不会执行的 try/except 死代码块
- 第1250-1289行：定义了第一个 `main()` 函数
- 第1292-1589行：在第一个 `main()` 函数体内嵌套定义了7个方法，这些函数永远不会被调用：
  - `_extract_section`（重复定义）
  - `_fallback_check_coverage`
  - `_fallback_check_materials`
  - `_ai_judge_accident`（含 TODO + 模拟返回值）
  - `_ai_calculate_compensation`（含 TODO + 模拟返回值）
  - `_ai_final_summary`
  - `_fallback_final_summary`
- 第1593行又定义了第二个 `main()`，覆盖了第一个

**影响**: 文件膨胀约 300 行，严重误导阅读者认为这些方法在起作用。

**修复建议**: 删除第1240-1589行所有死代码，保留第1593行后的 `main()` 和 `main_async()` 作为有效入口。

---

### 2. `_ai_check_materials_async` 中大面积死代码

**文件**: `app/claim_ai_reviewer.py` 第1009-1046行
**风险等级**: 中
**证据等级**: A级

**问题描述**: 第997行 `return result` 后，第1009-1046行构建了 prompt、调用 API、异常处理 — 永远不会执行。这是旧的 OCR 文本模式实现残余。

**修复建议**: 删除第1009-1046行死代码块。

---

### 3. 同步方法含 TODO 和模拟返回值

**文件**: `app/claim_ai_reviewer.py`
**风险等级**: 中
**证据等级**: A级

**问题描述**:
- `_ai_judge_accident`（第460-528行）：第517行有 `# TODO: 调用大模型API`，返回硬编码模拟结果
- `_ai_calculate_compensation`（第530-610行）：第592行有 `# TODO: 调用大模型API`，返回硬编码计算结果

**注意**: 这两个方法的 async 版本（`_ai_judge_accident_async` 和 `_ai_calculate_compensation_async`）是真实实现，会被 baggage_damage pipeline 通过 handlers 调用。同步版本仅被 `review_claim_with_ai` 旧入口使用。

**修复建议**: 确认 `review_claim_with_ai` 同步入口是否仍在使用。若已废弃，删除整个类方法及相关同步方法。

---

### 4. `_material_gate` 重复定义

**文件**: `app/modules/baggage_delay/pipeline.py`
**风险等级**: 低
**证据等级**: A级

**问题描述**: 第252-272行定义了一次 `_material_gate`，第411-414行又重新定义了同名函数（委托 rules.common.material_gate）。第一个定义被完全覆盖，从未执行。

**修复建议**: 删除第252-272行的废弃定义。

---

### 5. `_sync_review_results_to_db` 事务安全性不足

**文件**: `app/production/main_workflow.py` 第244-297行
**风险等级**: 中
**证据等级**: A级

**问题描述**: 
- 遍历所有 JSON 文件执行 INSERT/UPDATE，第295行才 `conn.commit()`
- 第293行的 `except` 只记录警告并继续循环（不抛出异常），所以 commit 最终会执行
- 但如果主表写入成功、子表写入失败（第270-290行的 if/elif 分支），数据处于不一致状态
- 硬编码数据库连接参数从 `os.getenv` 直接读取，绕过了 `app/config.py`

**修复建议**: 每个 forceid 独立 commit，或使用逐条 autocommit。子表写入失败时也应记录到错误列表并通知运维。

---

### 6. `_sync_manual_status` 硬编码接口地址

**文件**: `app/production/main_workflow.py` 第883行
**风险等级**: 低
**证据等级**: A级

**问题描述**: `RESULT_API_URL = "https://nanyan.sites.sfcrmapps.cn/..."` 硬编码在方法体内，不遵循配置层规范。

**修复建议**: 提取到 `app/config.py` 作为配置项 `MANUAL_STATUS_API_URL`。

---

## 二、B级问题（高概率存在）

### 7. audit_pipeline.py 运行时猴子补丁

**文件**: `app/engine/audit_pipeline.py` 第327-384行
**风险等级**: 中
**证据等级**: B级

**问题描述**:
- 模块加载时自动将 `AuditPipeline.execute` 替换为 `_patched_execute`
- 补丁代码与原 `execute` 方法逻辑高度耦合，若原方法被修改，补丁可能静默失效
- `_call_handler` 静态方法（第260-266行）定义后从未被使用，实际执行完全依赖 `_make_stage_fn` 闭包

**修复建议**: 去掉猴子补丁，直接在 `execute` 方法中使用正确的调用方式，或删除原 `execute` 只保留 `_patched_execute` 并重命名。

---

### 8. 配置验证与真实 provider 不一致

**文件**: `app/config.py` 第143-149行
**风险等级**: 低
**证据等级**: B级

**问题描述**: `validate()` 只检查 `OPENROUTER_API_KEY`，但项目已迁移到 DashScope/Qwen 为主要 provider。当只设置 `DASHSCOPE_API_KEY` 时，`validate()` 会错误地返回失败。错误消息"未设置OPENROUTER_API_KEY"具有误导性。

**修复建议**: `validate()` 应同时检查 `DASHSCOPE_API_KEY` 和 `OPENROUTER_API_KEY` 至少有一个。

---

### 9. aiomysql 连接池 autocommit=True 无事务保护

**文件**: `app/db/database.py` 第53行
**风险等级**: 中
**证据等级**: B级

**问题描述**: 连接池设置 `autocommit=True`，所有 DAO 操作自动提交，没有事务保护。对于需要多表原子操作的场景（如同时写入主表+子表），无法保证一致性。

**修复建议**: 在需要事务的场景中，使用 `await conn.begin()` 显式开启事务，或在连接池层面提供事务上下文管理器。

---

### 10. 两套独立的数据库连接管理

**文件**: `app/production/main_workflow.py` vs `app/db/database.py`
**风险等级**: 中
**证据等级**: B级

**问题描述**:
- 主 DAO 层使用 `aiomysql`（异步连接池，在 `database.py` 中管理）
- `_sync_review_results_to_db` 和 `_sync_manual_status` 使用独立的 `pymysql`（同步）连接，直接从 `os.getenv` 读取参数
- 两套连接池管理策略不一致，配置来源不统一

**修复建议**: 同步同步方法应复用 `DatabaseConnection` 实例，或通过 `config` 类获取数据库参数。

---

## 三、C级问题（可疑，需进一步验证）

### 11. `_classify_aviation_failure` 默认返回 evidence_gap

**文件**: `app/modules/baggage_delay/pipeline.py` 第203行
**风险等级**: 低
**证据等级**: C级

**问题描述**: 当 `aviation_lookup` 非空、`success` 为 False、且不匹配任何 system_markers 或 evidence_markers 时，返回 `"evidence_gap"`。未预期的失败类型被归类为"证据缺口"，可能导致应该转人工的问题被当作补件处理。

**修复建议**: 添加日志记录未分类的错误，或默认返回 `"system_error"`（更安全的降级）。

---

### 12. 缺少 `__init__.py` 的隐式命名空间包

**文件**: 多个子目录
**风险等级**: 低
**证据等级**: C级

**问题描述**: `app/engine/`、`app/monitoring/`、`app/output/`、`app/production/`、`app/scheduler/`、`app/state/`、`app/supplementary/`、`app/db/`、`app/modules/` 等子目录均缺少 `__init__.py`。Python 3.3+ 支持隐式命名空间包，但在特定环境配置下行为可能不一致。

**修复建议**: 为所有包目录添加空的 `__init__.py`，确保导入行为确定性。

---

## 四、待验证项（需要读取更多文件）

| 待验证项 | 目标文件 | 优先级 |
|---------|---------|-------|
| `app/engine/pipeline_log.py` 是否存在（audit_pipeline.py 第52行导入） | `app/engine/pipeline_log.py` | 高 |
| `app/modules/baggage_damage/handlers.py` 阶段处理器是否真正调用 async 方法 | `app/modules/baggage_damage/handlers.py` | 高 |
| `app/engine/material_extractor.py` 材料提取核心逻辑 | `app/engine/material_extractor.py` | 高 |
| `app/scheduler/task_scheduler.py` 生产调度器 async 闭环 | `app/scheduler/task_scheduler.py` | 中 |
| `app/output/frontend_pusher.py` 前端推送异常处理 | `app/output/frontend_pusher.py` | 中 |
| `app/scheduler/review_scheduler.py` 审核调度器 | `app/scheduler/review_scheduler.py` | 中 |
| `app/supplementary/handler.py` 补件处理器 | `app/supplementary/handler.py` | 中 |
| `app/state/status_manager.py` 状态管理器 | `app/state/status_manager.py` | 低 |
| `app/engine/circuit_breaker.py` 熔断器实现 | `app/engine/circuit_breaker.py` | 低 |
| `app/modules/flight_delay/pipeline.py` 完整航班延误流程 | `app/modules/flight_delay/pipeline.py` | 高 |

---

## 五、优化建议汇总

| 编号 | 建议 | 优先级 |
|-----|------|-------|
| OPT-1 | 清理 claim_ai_reviewer.py 中的 ~300 行死代码 | 高 |
| OPT-2 | 统一数据库连接管理，消除 aiomysql/pymysql 双轨制 | 中 |
| OPT-3 | 修复 audit_pipeline.py 的猴子补丁，改用显式调用 | 中 |
| OPT-4 | 为所有包目录添加 `__init__.py` | 低 |
| OPT-5 | 修正 `config.validate()` 的 API Key 检查逻辑 | 低 |
| OPT-6 | 将硬编码的 Salesforce API URL 提取到配置层 | 低 |
| OPT-7 | 需要事务的场景添加显式事务控制 | 中 |
| OPT-8 | 拆分 claim_ai_reviewer.py（>500行强制拆分规则） | 中 |

---

*注: 本文档由代码审计系统自动生成，仅供内部参考。*
