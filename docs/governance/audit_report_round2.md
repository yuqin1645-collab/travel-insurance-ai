# Python 工程审计报告 — 第二轮

> 审计日期: 2026-04-27
> 审计范围: 第一轮待验证项 + 补充审计（supplementary/handler.py, circuit_breaker.py, status_manager.py, config.py, claim_ai_reviewer.py 死代码确认）

---

## 一、第一轮待验证项确认结果

| 待验证项 | 文件 | 验证结果 |
|---------|------|---------|
| `pipeline_log.py` 是否存在 | `app/engine/pipeline_log.py` | 已确认存在，21行，import 可达 |
| `baggage_damage/handlers.py` 调用 async 方法 | `app/modules/baggage_damage/handlers.py` | 已确认正确调用 stages.py 中的 async 函数，early_return 机制使用合理 |
| `material_extractor.py` 核心逻辑 | `app/engine/material_extractor.py` | 存在线程安全和硬编码路径问题（见下文） |
| `task_scheduler.py` async 闭环 | `app/scheduler/task_scheduler.py` | orphans_sweep 违反单一职责（见下文） |
| `frontend_pusher.py` 异常处理 | `app/output/frontend_pusher.py` | 硬编码 URL + payload 来源不明（见下文） |
| `review_scheduler.py` 调度器 | `app/scheduler/review_scheduler.py` | 硬编码 URL + O(n) 查找（见下文） |
| `supplementary/handler.py` 补件处理器 | `app/supplementary/handler.py` | 多个 TODO 未实现（见下文） |
| `circuit_breaker.py` 熔断器 | `app/engine/circuit_breaker.py` | 实现合理，无重大问题 |
| `status_manager.py` 状态管理器 | `app/state/status_manager.py` | TODO 空壳方法 + 事务语义不完整（见下文） |
| `flight_delay/pipeline.py` 完整流程 | `app/modules/flight_delay/pipeline.py` | 结构复杂但与 baggage_delay 模式一致，无新增问题 |

---

## 二、A级问题（确认存在）

### 1. `_material_gate` 重复定义（baggage_delay/pipeline.py）

**文件**: `app/modules/baggage_delay/pipeline.py`
**行号**: 第252-272行（废弃） / 第411-414行（有效）
**风险等级**: 高

**确认**: 第252-272行定义了一个完整的关键词映射 `_material_gate`，第411-414行又定义了同名函数委托给 `_rules_material_gate`。Python 按最后定义为准，第一个定义被完全覆盖但保留了 20 行无用代码。

**修复建议**: 删除第252-272行废弃定义。

---

### 2. `claim_ai_reviewer.py` 死代码确认

**文件**: `app/claim_ai_reviewer.py`

#### 2a. `_extract_section` return 后死代码（第1240-1247行）

第1238行 `return extract_section(...)` 后，第1240-1247行的 try/except 块永远不会执行。

#### 2b. 第一个 `main()` 函数内嵌套定义7个方法（第1250-1589行）

第1250-1289行定义了第一个 `main()` 函数，其函数体内嵌套定义了：
- `_extract_section`（第1292行）
- `_fallback_check_coverage`（第1303行）
- `_fallback_check_materials`（第1332行）
- `_ai_judge_accident`（第1344行，含 TODO + 模拟返回值）
- `_ai_calculate_compensation`（含 TODO + 模拟返回值）
- `_ai_final_summary`
- `_fallback_final_summary`

这些函数永远不会被外部调用。第1593行又定义了第二个 `main()`，覆盖了第一个。

#### 2c. `_ai_check_materials_async` 中 return 后死代码（第1009-1046行）

第997行 `return result` 后，第1009-1046行构建了旧的 OCR 文本模式 prompt，永远不会执行。

**影响**: 文件膨胀约 350 行，严重误导阅读。

**修复建议**: 删除所有死代码块。

---

### 3. `frontend_pusher.py` 中 `claim_info` 来源不明

**文件**: `app/output/frontend_pusher.py`
**行号**: 第283-291行
**风险等级**: 高

**问题描述**: `build_api_payload()` 方法中补件逻辑使用 `data.get('claim_info')`，但调用方传入的 `data` 字典中可能不包含 `claim_info` 键。当 `claim_info` 为 None 时，后续访问 `claim_info.get(...)` 会抛出 `AttributeError`，导致推送静默失败。

**修复建议**: 添加 None 检查，或从数据库/文件系统加载 `claim_info` 作为兜底。

---

### 4. `supplementary/handler.py` 核心逻辑未实现

**文件**: `app/supplementary/handler.py`
**风险等级**: 高

**问题描述**:
- 第378行: `_check_new_materials()` 始终返回 `False`（TODO 未实现）
- 第306-314行: `_notify_frontend_supplementary()` 空壳（TODO 未实现）
- 第326-333行: `_send_reminder()` 空壳（TODO 未实现）
- 第360-378行: 补件接收检查的核心逻辑完全空白

**影响**: 补件流程的关键路径（检测新材料、通知前端、发送提醒）均未实现。当审核结果返回"需补件"时，补件记录会创建到数据库，但不会通知任何人，也不会自动检测补件是否到位。

**修复建议**: 根据业务需求实现这些方法，或在文档中明确标注"补件功能待开发"。

---

## 三、B级问题（高概率存在）

### 5. `status_manager.py` 中的 TODO 空壳方法

**文件**: `app/state/status_manager.py`
**行号**: 第490-498行、第510-512行
**风险等级**: 中

**问题描述**:
- `get_claim_statistics()`（第490-498行）: 返回全零占位数据，无实际统计查询
- `cleanup_expired_claims()`（第510-512行）: 返回 `(0, "清理功能待实现")`，无实际清理逻辑

**影响**: 定时任务调用这些方法时不会产生预期效果，但也不会报错。

---

### 6. `status_manager.py` 事务语义不完整

**文件**: `app/state/status_manager.py`
**行号**: 第593-620行
**风险等级**: 中

**问题描述**: `status_transaction()` 上下文管理器声称提供"事务"能力，但只是在 `with` 块结束后调用 `update_claim_status()`。它没有使用数据库事务（`begin/commit/rollback`），也没有在异常时回退在 `with` 块内执行的数据库操作。命名误导性强。

**修复建议**: 重命名为 `status_update_context()` 反映实际行为，或在底层使用数据库事务实现真正的事务语义。

---

### 7. `task_scheduler.py` orphan_sweep 违反单一职责

**文件**: `app/scheduler/task_scheduler.py`
**行号**: 第247-383行
**风险等级**: 中

**问题描述**: `_run_orphan_sweep_review()` 内联了完整的审核流程（案件查找 → 材料下载 → AI审核 → 结果保存 → 前端推送），137行代码嵌入定时任务方法体内。这使得：
- 审核逻辑与调度逻辑高度耦合
- 无法在不修改 scheduler 的情况下调整审核流程
- 与正常审核流程（`review_scheduler.py`）代码重复

**修复建议**: 将审核流程抽取为独立服务方法，`task_scheduler` 仅负责调用。

---

### 8. `review_scheduler.py` O(n) 全文件遍历查找

**文件**: `app/scheduler/review_scheduler.py`
**行号**: 第289-318行
**风险等级**: 中

**问题描述**: `_find_claim_folder()` 对 `claims_data/` 目录下的所有子目录进行 O(n) 遍历查找指定 forceid 的案件。当案件数量增长到数千级别时，每次审核都需要遍历整个目录树。

**修复建议**: 建立 `forceid -> folder_path` 的索引文件（JSON/SQLite），或使用数据库查询替代文件系统遍历。

---

### 9. `config.validate()` 检查逻辑不完整

**文件**: `app/config.py`
**行号**: 第143-149行
**风险等级**: 中

**确认**: `validate()` 只检查 `OPENROUTER_API_KEY`，不检查 `DASHSCOPE_API_KEY`。但第21行 `DASHSCOPE_API_KEY` 有 fallback 到 `OPENROUTER_API_KEY`，且第31行 `USE_QWEN_VISION` 默认为 `true`。当只设置 `DASHSCOPE_API_KEY` 时，`validate()` 会错误地返回失败。

**修复建议**: `validate()` 应检查 `DASHSCOPE_API_KEY` 或 `OPENROUTER_API_KEY` 至少有一个非空。

---

### 10. `material_extractor.py` 线程安全问题

**文件**: `app/engine/material_extractor.py`
**行号**: 第267行
**风险等级**: 中

**问题描述**: 通过 `setattr(config, 'VISION_MAX_ATTACHMENTS', new_value)` 临时修改全局配置。虽然使用了 `try/finally` 恢复，但在并发场景下：
- 线程A修改为5 → 线程B修改为3 → 线程A使用3（被B覆盖）
- 如果线程A的 `finally` 在线程B之后执行，配置会被恢复为错误的值

**修复建议**: 将 `VISION_MAX_ATTACHMENTS` 作为方法参数传递，而非修改全局配置。

---

### 11. `material_extractor.py` 硬编码 Tesseract 路径

**文件**: `app/engine/material_extractor.py`
**行号**: 第460-527行
**风险等级**: 低

**问题描述**: `_run_tesseract_on_attachments()` 方法中硬编码了 Tesseract 可执行文件路径，不遵循配置层的 `config.TESSERACT_PATH`。

**修复建议**: 使用 `config.TESSERACT_PATH`。

---

### 12. `download_scheduler.py` 同步 requests 在 async 中使用

**文件**: `app/scheduler/download_scheduler.py`
**行号**: 第549行
**风险等级**: 中

**问题描述**: `_download_file()` 使用同步的 `requests.get()` 在 `async` 方法中执行。当网络请求较慢时，会阻塞整个 event loop，影响其他协程的执行。

**修复建议**: 使用 `aiohttp` 或 `httpx.AsyncClient` 替代 `requests`，或在 `asyncio.to_thread()` 中执行同步请求。

---

### 13. `output/coordinator.py` 空壳方法

**文件**: `app/output/coordinator.py`
**行号**: 第427行
**风险等级**: 低

**问题描述**: `retry_failed_outputs()` 方法是 TODO 空壳，没有任何实现。

**修复建议**: 实现或标记为 `NotImplementedError` 并文档化。

---

### 14. 多处硬编码 Salesforce API URL

**文件**: 多处
**风险等级**: 中

| 文件 | 行号 | 硬编码内容 |
|------|------|-----------|
| `app/production/main_workflow.py` | 883 | `RESULT_API_URL = "https://nanyan.sites.sfcrmpps.cn/..."` |
| `app/output/frontend_pusher.py` | 20 | Salesforce API URL |
| `app/scheduler/review_scheduler.py` | 217 | Salesforce API URL |

**修复建议**: 统一提取到 `app/config.py` 作为 `SALESFORCE_API_URL` 配置项。

---

## 四、C级问题（低风险/风格问题）

### 15. `_classify_aviation_failure` 默认返回 evidence_gap

**文件**: `app/modules/baggage_delay/pipeline.py`
**行号**: 第203行
**风险等级**: 低

**问题描述**: 未预期的失败类型被归类为"证据缺口"，可能导致应该转人工的问题被当作补件处理。

**修复建议**: 默认返回 `"system_error"`（更安全的降级），并添加未分类错误的日志记录。

---

### 16. 缺少 `__init__.py` 的隐式命名空间包

**文件**: 多个子目录
**风险等级**: 低

**涉及目录**: `app/engine/`, `app/monitoring/`, `app/output/`, `app/production/`, `app/scheduler/`, `app/state/`, `app/supplementary/`, `app/db/`, `app/modules/`

**修复建议**: 为所有包目录添加空的 `__init__.py`。

---

### 17. `circuit_breaker.py` 评估

**文件**: `app/engine/circuit_breaker.py`
**评估结果**: 实现质量高，无重大问题

- 三态转换逻辑清晰（CLOSED → OPEN → HALF_OPEN → CLOSED）
- 使用 `asyncio.Lock` 保护状态变更
- 全局单例按服务名隔离，设计合理
- `_should_attempt()` 在 OPEN 状态下正确检查超时
- HALF_OPEN 状态正确限制探测请求数量
- 异常处理中正确区分了 `CircuitBreakerOpen`（透传）和其他异常（计数）

**小建议**: `get_circuit_breaker()` 不是线程安全的（字典检查+创建不是原子操作），但在 asyncio 单线程事件循环下不是问题。如果未来迁移到多线程环境，需要加锁。

---

## 五、优化建议汇总

| 编号 | 建议 | 优先级 | 对应问题 |
|------|------|-------|---------|
| OPT-1 | 清理 `claim_ai_reviewer.py` 中 ~350 行死代码 | 高 | #2 |
| OPT-2 | 实现 `supplementary/handler.py` 的 TODO 方法 | 高 | #4 |
| OPT-3 | 修复 `frontend_pusher.py` 中 `claim_info` 来源 | 高 | #3 |
| OPT-4 | 删除 `baggage_delay/pipeline.py` 重复的 `_material_gate` | 高 | #1 |
| OPT-5 | 统一硬编码 Salesforce API URL 到配置层 | 中 | #14 |
| OPT-6 | 修复 `config.validate()` 的 API Key 检查 | 中 | #9 |
| OPT-7 | 将 `material_extractor.py` 的全局 config 修改改为参数传递 | 中 | #10 |
| OPT-8 | 用 aiohttp/httpx 替换 `download_scheduler.py` 中的同步 requests | 中 | #12 |
| OPT-9 | 将 orphan_sweep 审核流程抽取为独立服务 | 中 | #7 |
| OPT-10 | 建立 forceid → folder_path 索引替代 O(n) 查找 | 中 | #8 |
| OPT-11 | 实现 `status_manager.py` 的统计和清理方法 | 低 | #5 |
| OPT-12 | 重命名 `status_transaction` 反映实际行为 | 低 | #6 |
| OPT-13 | `_classify_aviation_failure` 默认返回 system_error | 低 | #15 |
| OPT-14 | 为所有包目录添加 `__init__.py` | 低 | #16 |
| OPT-15 | 使用 `config.TESSERACT_PATH` 替代硬编码路径 | 低 | #11 |

---

## 六、第二轮相比第一轮的新增发现

1. **补件功能大面积未实现**（#4）: `supplementary/handler.py` 的核心方法（检测新材料、通知前端、发送提醒）均为 TODO 空壳
2. **前端推送 payload 来源不明**（#3）: `claim_info` 可能为 None，导致推送静默失败
3. **多处硬编码 Salesforce API URL**（#14）: 分布在 3 个文件中，配置管理不统一
4. **同步 HTTP 客户端在 async 中阻塞**（#12）: `download_scheduler.py` 使用 `requests.get` 可能阻塞 event loop
5. **`status_manager` 事务名不副实**（#6）: 名为 transaction 但无真正的事务语义

---

*注: 本文档由代码审计系统自动生成，仅供内部参考。*
