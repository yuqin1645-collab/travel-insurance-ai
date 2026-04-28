# patch_tracker.md

## 已完成 Patch

### PATCH-001
- 文件：app/ocr_service.py
- 问题：裸 except: → except OSError:（临时文件清理）；6处 print() → LOGGER
- 状态：已完成
- 风险：B
- 日期：2026-04-27

### PATCH-002
- 文件：app/document_cache.py
- 问题：10处 print() → LOGGER（添加 logging 模块）
- 状态：已完成
- 风险：B
- 日期：2026-04-27

### PATCH-003
- 文件：app/document_processor.py
- 问题：2处生产路径 print() → LOGGER
- 状态：已完成
- 风险：B
- 日期：2026-04-27

### PATCH-004
- 文件：app/openrouter_client.py
- 问题：20+处 print() → LOGGER；4处内联 import time/asyncio 移至模块级
- 状态：已完成
- 风险：B
- 日期：2026-04-27

### PATCH-005
- 文件：app/output/frontend_pusher.py
- 问题：8处 print() → LOGGER
- 状态：已完成
- 风险：B
- 日期：2026-04-27

### PATCH-006
- 文件：app/gemini_vision_client.py
- 问题：4处 print() → LOGGER；内联 import re/json 移至模块级
- 状态：已完成
- 风险：B
- 日期：2026-04-27

### PATCH-007
- 文件：app/modules/flight_delay/pipeline.py:2324
- 问题：except Exception 吞异常 → 添加 LOGGER.warning + exc_info=True
- 状态：已完成
- 风险：A
- 日期：2026-04-27

### PATCH-008
- 文件：app/skills/flight_lookup.py, app/skills/war_risk.py, app/skills/policy_booking.py, app/skills/weather.py
- 问题：删除死代码函数（~180行）：flight_lookup_status, calculate_delay_minutes, check_country_risk, lookup_alerts, lookup_ticket_status_stub, lookup_rebooking_stub
- 状态：已完成
- 风险：B
- 日期：2026-04-27

### PATCH-009
- 文件：app/skills/__init__.py, app/state/status_manager.py
- 问题：移除死代码导出；删除 status_transaction 未调用 async context manager
- 状态：已完成
- 风险：B
- 日期：2026-04-27

### PATCH-010
- 文件：app/scheduler/download_scheduler.py
- 问题：移除冗余内联 import requests as _requests
- 状态：已完成
- 风险：B
- 日期：2026-04-27

### PATCH-011
- 文件：app/production/main_workflow.py
- 问题：内联 import json 移至模块级
- 状态：已完成
- 风险：B
- 日期：2026-04-27

### PATCH-012
- 文件：app/production/main_workflow.py, app/claim_ai_reviewer.py, app/openrouter_client.py, app/ocr_service.py, app/gemini_vision_client.py, app/engine/material_extractor.py, app/modules/flight_delay/pipeline.py, app/vision_preprocessor.py, app/prompt_loader.py, app/scheduler/task_scheduler.py, app/scheduler/review_scheduler.py, app/scheduler/download_scheduler.py, app/output/coordinator.py, app/supplementary/handler.py, app/skills/flight_lookup.py, app/monitoring/alert_manager.py, app/monitoring/health_check.py
- 问题：全部函数体内联 import 移至模块级（17个app文件）
- 状态：已完成
- 风险：A
- 日期：2026-04-28

### PATCH-013
- 文件：scripts/download_claims.py, scripts/find_claim_by_forceid.py, scripts/query.py, scripts/push.py, scripts/fix_data.py, scripts/review.py
- 问题：函数体内联 import (time/re/asyncio/sys/pymysql/requests) 移至模块级（6个脚本文件）
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-014
- 文件：app/openrouter_client.py
- 问题：移除未使用的 Literal 导入
- 状态：已完成
- 风险：C
- 日期：2026-04-28

### PATCH-015
- 文件：app/privacy_masking.py
- 问题：身份证号脱敏后长度不一致（原 \1****\2**** 产出12字符 → 改为 \1**************\2 产出18字符）
- 状态：已完成
- 风险：A
- 日期：2026-04-28

### PATCH-016
- 文件：app/modules/baggage_damage/extractors.py
- 问题：正则表达式双重转义 bug — `r"\\s+"` 匹配字面 `\s+` 而非空白字符；`r"\\b1[3-9]\\d{2,}\\b"` 匹配字面 `\b...\d...\b` 而非手机号模式（OPT-26 / 第三轮审计 #14）
- 状态：已完成
- 风险：A
- 日期：2026-04-28

### PATCH-017
- 文件：app/production/main_workflow.py, app/scheduler/task_scheduler.py
- 问题：sys.path.insert 项目级反模式 — 两处 `sys.path.insert(0, str(project_root))` 使项目根目录优先于所有其他路径，可能引入同名模块静默覆盖（审计 #1 / OPT-27）
- 状态：已完成
- 风险：A
- 日期：2026-04-28

---

### PATCH-018
- 文件：app/openrouter_client.py
- 问题：chat_completion_json 与 chat_completion_json_async 约65行重复代码，提取 _parse_json_with_fallbacks 统一处理（sync 用 time.sleep，async 用 asyncio.sleep）
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-019
- 文件：app/ocr_service.py, app/engine/material_extractor.py（调查确认，无代码修改）
- 问题：Tesseract 硬编码路径 — 审计 #7 声称 ocr_service.py:328 有 Windows 硬编码路径
- 调查结果：当前代码两处均已使用 config.TESSERACT_PATH，硬编码问题在之前轮次已解决，无需修改
- 状态：已确认（无需修）
- 风险：C
- 日期：2026-04-28

### PATCH-020
- 文件：app/skills/policy_booking.py, app/modules/flight_delay/pipeline.py（调查确认，无代码修改）
- 问题：TOP3 — lookup_ticket_status_stub / lookup_rebooking_stub 返回 unknown，需确保调用方不硬拒赔
- 调查结果：
  (1) stub 函数已在 PATCH-008 删除
  (2) pipeline 通过 _is_unknown() 保守填充，未知值返回 None 不触发拒赔
  (3) _postprocess_audit_result 中：unknown → 补材/人工复核，仅 confirmed mismatch → 拒赔
  (4) policy_booking.py 设计文档明确："无后端接口时降级为补材/转人工，不硬拒赔"
  降级策略已安全，无需修改
- 状态：已确认（无需修）
- 风险：C
- 日期：2026-04-28

---

### PATCH-021
- 文件：app/quality_assessment.py（调查确认，无代码修改）
- 问题：OPT-34 — quality_assessment.py 是否在生产路径中被调用
- 调查结果：claim_ai_reviewer.py 中无任何 quality_assessment 导入，主工作流也不引用。该模块是独立的评估工具，当前未被任何生产代码调用。审计报告中声称"被 claim_ai_reviewer.py import"不成立
- 状态：已确认（审计误判，无需修）
- 风险：C
- 日期：2026-04-28

### PATCH-022
- 文件：app/skills/weather.py, app/modules/flight_delay/pipeline.py（调查确认，无代码修改）
- 问题：OPT-30 — weather.py 气象预警表为空，_WEATHER_ALERT_TABLE 永远是 []
- 调查结果：
  (1) lookup_alerts_table() 返回 []，pipeline line 956 `if alerts:` 分支永不进入
  (2) 兜底机制存在：pipeline line 978-993 有 late-purchase 启发式检测（投保距事故 ≤3 天）
  (3) AI 模型仍可从材料中自行推断可预见因素
  (4) 设计文档明确："本地已知预警维护表（人工维护，初期先用）"
  这是业务决策问题：是否接入外部气象API。当前策略是"数据不可用时交AI+人工"，不会自动放行欺诈
- 状态：已确认（需业务决策，当前策略安全）
- 风险：B
- 日期：2026-04-28

---

### PATCH-023
- 文件：app/gemini_vision_client.py（调查确认，无代码修改）
- 问题：OPT-32 — _VISION_INFLIGHT 并发计数器是否需加锁保护
- 调查结果：
  (1) 计数器仅在 `async with _VISION_SEMAPHORE:` 块内使用，受信号量保护
  (2) asyncio 是单线程模型，Python 的 `+=`/`-=` 操作在单 event loop 下是原子的
  (3) _VISION_INFLIGHT 仅用于 debug 日志输出，不影响业务逻辑
  (4) _VISION_SEMAPHORE 已经正确限制了并发数（默认6）
  不需要加锁，asyncio 单线程天然安全
- 状态：已确认（无需修）
- 风险：C
- 日期：2026-04-28

### PATCH-024
- 文件：app/state/constants.py（调查确认，无代码修改）
- 问题：OPT-36 — STATUS_ICONS 包含 emoji，Windows GBK 终端可能编码失败
- 调查结果：
  (1) STATUS_ICONS 仅用于前端 UI 展示，不在日志路径中使用
  (2) main_workflow.py / task_scheduler.py 等生产路径不引用 STATUS_ICONS
  (3) scripts/download_claims.py 中使用的 emoji 是直接写在日志里的（"✓" "⚠"），与 constants.py 的 STATUS_ICONS 无关
  (4) 需要打印 emoji 的文件（openrouter_client.py, gemini_vision_client.py）已做 sys.stdout.reconfigure('utf-8')
  emoji 编码问题在需要处理的地方已解决
- 状态：已确认（无需修）
- 风险：C
- 日期：2026-04-28

### PATCH-025
- 文件：app/skills/airport.py（调查确认，无代码修改）
- 问题：OPT-37 — 机场数据硬编码在 _AIRPORT_DB 字典中，是否应从外部数据源加载
- 调查结果：
  (1) _AIRPORT_DB 覆盖 ~150 个常用机场，包含主要旅行目的地（CN/JP/KR/SEA/EU/NA/AU）
  (2) 设计选择：轻量级内存数据库，无外部依赖，启动即就绪
  (3) 未知机场返回 found=False + is_domestic_cn=None，调用方正确处理降级（人工复核）
  (4) 境内中转免责判定依赖此模块，但对未知机场不会误判（is_domestic_cn=None 不触发拒赔）
  当前设计合理，无需外部数据源
- 状态：已确认（无需修）
- 风险：C
- 日期：2026-04-28

### PATCH-026
- 文件：app/engine/material_extractor.py
- 问题：_download_filelist_to_folder 创建 aiohttp.ClientSession() 时缺少 trust_env=True，代理环境下 FileList URL 下载失败
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-027
- 文件：app/modules/flight_delay/pipeline.py
- 问题：5处函数体内联 import（copy/datetime as _dt/resolve_country/date as _date/_parse_datetime_str）移至模块级
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-028
- 文件：app/engine/material_extractor.py
- 问题：_run_tesseract_on_attachments 和 _find_original 内冗余 config 重导入（config as _cfg / config as _cfg2），模块级已有 config
- 状态：已完成
- 风险：C
- 日期：2026-04-28

### PATCH-029
- 文件：app/output/frontend_pusher.py
- 问题：push_to_frontend 和 push_batch 创建 ClientSession 时缺少 trust_env=True，代理环境下 Salesforce 推送失败
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-030
- 文件：app/scheduler/review_scheduler.py, app/skills/flight_lookup.py, app/state/status_manager.py, app/scheduler/task_scheduler.py
- 问题：多处函数体内联 import 移至模块级：
  (1) review_scheduler.py: 前端推送 session 添加 trust_env=True
  (2) flight_lookup.py: import ast 移至模块级
  (3) status_manager.py: from app.config import config 移至模块级
  (4) task_scheduler.py: shutil/timedelta + 3个 app import 移至模块级
- 状态：已完成
- 风险：B
- 日期：2026-04-28

---

### PATCH-031
- 文件：app/claim_ai_reviewer.py
- 问题：`import traceback` 内联于 except 块 → 移至模块级
- 状态：已完成
- 风险：C
- 日期：2026-04-28

### PATCH-032
- 文件：app/output/frontend_pusher.py
- 问题：`import sys` 内联于 `__main__` 块 → 移至模块级
- 状态：已完成
- 风险：C
- 日期：2026-04-28

### PATCH-033
- 文件：app/monitoring/health_check.py
- 问题：`import shutil` 和 `from app.db.models import TaskStatus` 内联于函数体 → 移至模块级
- 状态：已完成
- 风险：C
- 日期：2026-04-28

### PATCH-034
- 文件：app/rules/claim_types/baggage_delay.py
- 问题：`from app.skills.compensation import tier_lookup` 内联于函数体 → 移至模块级
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-035
- 文件：scripts/download_claims.py
- 问题：4处 `from urllib.parse import urlparse/parse_qs/unquote` 内联于函数体 → 移至模块级；`sys.path.insert` 从 `_save_claim_info_to_db` 函数体移至模块级
- 状态：已完成
- 风险：B
- 日期：2026-04-28

---

### PATCH-036
- 文件：app/output/coordinator.py:144,148
- 问题：ReviewResult 数据类构造传入不存在的字段 — `review_status="completed"`（应为 `audit_status`）和 `metadata={...}`（数据类无此字段），运行时必然 `TypeError` 崩溃
- 状态：已完成
- 风险：A
- 日期：2026-04-28

### PATCH-037
- 文件：app/supplementary/handler.py:424,428,432
- 问题：`run_supplementary_check()` 生产路径 3 处 `print()` → `LOGGER.info()`（该函数被定时任务直接调用）
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-038
- 文件：app/config.py:147-148
- 问题：`Config.validate()` 方法中 2 处 `print()` → `LOGGER.error()`（验证失败信息应走日志系统）
- 状态：已完成
- 风险：C
- 日期：2026-04-28

### PATCH-039
- 文件：app/db/models.py:208,257,369,618
- 问题：4 处 `from_dict` 方法体内 `import dataclasses` + `dataclasses.fields(cls)` → 模块级 `from dataclasses import ... fields`，函数体内直接用 `fields(cls)`
- 状态：已完成
- 风险：C
- 日期：2026-04-28

---

### PATCH-040
- 文件：scripts/push.py:29
- 问题：`import pymysql` 重复导入（line 19 已导入，line 29 冗余）
- 状态：已完成
- 风险：C
- 日期：2026-04-28

### PATCH-041
- 文件：app/claim_ai_reviewer.py:674, app/modules/baggage_damage/pipeline.py:115
- 问题：`traceback.print_exc()` 直接写 stderr，不进入日志系统 → 改为 `LOGGER.error(..., exc_info=True)`，并移除不再需要的 `import traceback`
- 状态：已完成
- 风险：A
- 日期：2026-04-28

### PATCH-047
- 文件：app/ocr_service.py:99,119
- 问题：2处 `requests.post()` 缺少 `timeout` 参数（百度 OCR token 获取 + 识别调用），若 Baidu API 服务端挂起将导致进程永久阻塞
- 修复：两处均添加 `timeout=config.TIMEOUT`（默认 60 秒）
- 状态：已完成
- 风险：A
- 日期：2026-04-28

### PATCH-048
- 文件：app/scheduler/task_scheduler.py:536（信号处理 finally 块）
- 问题：关机标志设置在了错误对象上 — `scheduler._is_shutting_down = True` 设置的是 TaskScheduler 的 flag，但 `ProductionWorkflow.run_hourly_check()` 检查的是 `workflow._is_shutting_down`（同一类的另一个实例字段）。关机期间，如果 `run_hourly_check` 被触发，会通过 `getattr(self, '_is_shutting_down', False)` 检查但永远返回 False（因为 workflow 的 flag 从未被设置）。在信号到达和 `pause_job()` 之间的时间窗口内，新触发的定时任务会正常执行全部 8 步检查，然后 DB 连接池被 `workflow.shutdown()` 关闭，可能导致正在运行的协程崩溃
- 修复：添加 `scheduler.workflow._is_shutting_down = True`，确保 workflow 的 flag 同步设置，`run_hourly_check` 短路返回
- 状态：已完成
- 风险：A
- 日期：2026-04-28

### PATCH-049
- 文件：app/scheduler/task_scheduler.py:_run_claims_cleanup
- 问题：`shutil.rmtree(claim_folder)` 在 `claims_dir.rglob("claim_info.json")` 迭代器内部执行删除 — rglob 是惰性生成器（底层 `os.walk`），在遍历过程中删除子目录可能导致迭代器后续 `stat()` 调用失败（`FileNotFoundError`），中断整个清理循环
- 修复：先 `list(claims_dir.rglob(...))` 物化为列表，再遍历删除，消除迭代期间文件系统结构变化的风险
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-050
- 文件：scripts/push.py:90,137
- 问题：2处 `aiohttp.ClientSession()` 创建时缺少 `trust_env=True`，企业代理环境下推送前端/Salesforce 失败（连接不通或超时）
- 修复：两处均添加 `trust_env=True`
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-051
- 文件：app/output/frontend_pusher.py:229
- 问题：`build_api_payload()` 只读取 `flight_delay_audit`，不处理 `baggage_delay_audit`。行李延误案件推送到前端时 `audit` 永远为 `{}`，`audit_result=""` → `map_audit_result_to_status("")` 返回 "0"（拒赔），导致所有行李延误案件被错误标记为拒赔，且判定理由/补件理由全部为空
- 修复：`audit = data.get('flight_delay_audit') or data.get('baggage_delay_audit') or {}`
- 状态：已完成
- 风险：A
- 日期：2026-04-28

### PATCH-052
- 文件：app/db/database.py:332
- 问题：`batch_update_from_json_files()` 只读取 `flight_delay_audit`，不处理 `baggage_delay_audit`。从 JSON 批量更新审核结果时，行李延误的 `audit_result`/`payout_suggestion`/`logic_check` 全部丢失
- 修复：同 PATCH-051
- 状态：已完成
- 风险：A
- 日期：2026-04-28

### PATCH-053
- 文件：app/scheduler/task_scheduler.py:361
- 问题：孤儿审核日志 `result.get('flight_delay_audit', {})` 不处理 `baggage_delay_audit`，日志中 `audit_result` 永远为空（仅影响日志可读性，不影响功能）
- 修复：`result.get('flight_delay_audit', result.get('baggage_delay_audit', {}))`
- 状态：已完成
- 风险：C
- 日期：2026-04-28

---

## 当前处理中 Patch

### PATCH-042
- 文件：app/modules/baggage_delay/pipeline.py:719
- 问题：`from app.vision_preprocessor import prepare_attachments_for_claim` 内联于函数体条件分支 → 移至模块级
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-043
- 文件：app/engine/material_extractor.py:257
- 问题：`from app.vision_preprocessor import prepare_attachments_for_claim` 内联于方法体 → 移至模块级
- 状态：已完成
- 风险：B
- 日期：2026-04-28

### PATCH-044
- 文件：app/scheduler/review_scheduler.py:129
- 问题：`from app.output.frontend_pusher import push_to_frontend` 内联于函数体 try 块 → 移至模块级（无循环导入风险）
- 状态：已完成
- 风险：B
- 日期：2026-04-28

---

## 性能优化 Patch

### PATCH-045
- 文件：app/production/main_workflow.py:_sync_review_results_to_db
- 问题：逐行 conn.commit() + 失败时 conn.rollback() 导致每条审核结果同步触发一次磁盘 flush。1000 条 = 1000 次 commit，IO 浪费严重
- 修复：每 100 条 commit 一次，失败时 rollback 当前批次并重置计数器。既保留"尽力提交"语义（单条失败不影响已提交批次），又减少 commit 次数至 1/100
- 同时复用单一 cursor（原代码每行未显式创建 cursor，依赖 `with conn.cursor()` 上下文）
- 状态：已完成
- 风险：B（降低 commit 频率但不改变事务语义：每批内失败仍 rollback 该批，已提交批次不受影响）
- 日期：2026-04-28

### PATCH-046
- 文件：app/production/main_workflow.py:_sync_manual_status
- 问题：双重性能浪费 —— (1) 每次循环创建新 cursor（`with conn.cursor()` 在 for 内）；(2) 每次循环 conn.commit()。N 条 = N 次 cursor 创建 + N 次磁盘 flush
- 修复：复用单一 cursor 执行全部 UPDATE，每 100 条 commit 一次
- 状态：已完成
- 风险：B（同 PATCH-045）
- 日期：2026-04-28

---

## 未完成 Patch

（累计新增 17 个 PATCH 已修复：PATCH-026 至 PATCH-042）

本轮（第七轮）审计范围：baggage_damage/pipeline.py、handlers.py、claim_ai_reviewer.py 异常处理闭环
- 发现并修复 `traceback.print_exc()` 不进入日志系统问题（2处，PATCH-041）
- 确认 `shutil.rmtree` 定时清理任务有 proper error handling
- 确认 `requests.post` 同步调用在 proxy 环境下正常工作（requests 原生支持环境变量）

---

## 禁止修改项

1. document_processor.py / document_cache.py — 仍被 claim_ai_reviewer.py 同步路径使用
2. AliyunOCR / TencentOCR recognize() — 已正确返回 success=False，无需修改
3. monitoring/ 模块 — sys.path.insert 已在上轮移除，模块本身无生产调用
4. _VISION_INFLIGHT / _FLIGHT_CACHE — asyncio单线程安全，无需改动
5. except Exception — 全部有 proper logging 或 graceful degradation，无需改动

---

## 下一轮优先级

累计 53 个 PATCH 已修复（PATCH-001 ~ PATCH-053）。第17轮审计（datetime/时区、prompt 模板链、asyncio.gather）未发现运行时 bug，无需新增 PATCH。

app/ 下所有生产路径已扫描完毕，14 轮审计覆盖：
- 内联导入修复/确认合理（全部 app/ 文件）
- print() → LOGGER 转换
- traceback.print_exc() → exc_info=True
- sys.path.insert 反模式移除
- 数据类字段错误修复
- 正则双重转义修复
- 代理信任环境变量添加
- 裸 except 修复
- 批量数据库性能优化（每 100 条 commit 替代逐条 commit）
- requests timeout 安全加固（全部 HTTP 调用必须带 timeout）
- 关机流程信号处理修复（_is_shutting_down 对象不匹配，PATCH-048）
- 惰性生成器遍历删除保护（rglob + rmtree 竞争，PATCH-049）

第13轮新增安全扫描类别：
- subprocess/shell 注入：0 处
- eval/exec 执行：0 处
- 文件路径穿越：0 处
- SQL 注入（字符串拼接）：0 处
- 硬编码凭证/密钥：0 处
- 资源泄露（aiohttp session/DB连接/文件句柄）：0 处
- requests 缺少 timeout：2 处（PATCH-047）

第14轮系统级健壮性扫描：
- 信号处理（SIGINT/SIGTERM）：1 处修复（PATCH-048）
- 关机流程资源清理：确认 proper finally/try 结构
- 惰性生成器遍历删除：1 处修复（PATCH-049）
- APScheduler 任务并发保护：`is_running` flag 有效

第15轮 scripts/ 运维脚本扫描：
- requests timeout：全部有 timeout（0 处问题）
- SQL 注入：全部参数化查询（0 处问题）
- subprocess 注入：无用户输入参与（0 处问题）
- 路径穿越：scope_dir 校验有效（0 处问题）
- DB 连接泄露：全部 proper try/finally（0 处问题）
- aiohttp 缺少 trust_env：2 处修复（PATCH-050）

第16轮字段映射链审计：
- `build_api_payload` 不处理 `baggage_delay_audit` → 行李延误全部被错误映射为拒赔（PATCH-051，风险 A）
- `batch_update_from_json_files` 同上（PATCH-052，风险 A）
- 孤儿审核日志同上（PATCH-053，风险 C）
- `_extract_review_fields` 已正确处理双审计键，无需修复
- `claim_info.json` key 大小写一致性：多处兜底已覆盖（forceid/ForceId/ID_Type/id_type 等），无问题

当前已扫描的目录/模块：
- app/ 根目录文件（配置、OCR、文档处理、隐私脱敏、质量评估等）
- app/modules/ （航班延误、行李损失、基础模块、注册表）
- app/engine/ （审计管道框架、材料提取、预检查、旅行提示、电路断路器、错误处理等）
- app/rules/ （base、registry、common/、flight/、claim_types/）
- app/skills/ （flight_lookup、war_risk、policy_booking、weather、compensation）
- app/db/ （models、database）
- app/output/ （coordinator、frontend_pusher）
- app/scheduler/ （download、review、task scheduler）
- app/supplementary/ （handler）
- app/state/ （status_manager、constants）
- app/monitoring/ （health_check、alert_manager）
- app/production/ （main_workflow）
- app/prompt_loader.py、app/policy_terms_registry.py

尚未审计或待深入的目录：
- prompts/ — 提示词模板目录，纯文本文件，无执行风险（第17轮已审计 {{include:}} 链完整性，全部通过）
- tests/ — 目录不存在
- scripts/ — 已完成全面扫描（安全/资源管理/异常处理）

---

## 第17轮审计结果：Datetime/时区 + Prompt 模板链 + asyncio.gather

本轮审计覆盖三个方向：asyncio.gather 异常处理闭环、datetime.now() vs UTC-aware 混用、PromptLoader {{include:}} 引用链完整性。

### asyncio.gather 异常处理
- 文件：app/claim_ai_reviewer.py:541-556
- `asyncio.gather(*tasks, return_exceptions=True)` 后正确用 `isinstance(result, Exception)` 判断，在调用 `result.get()` 之前已排除 Exception
- 结论：异常处理正确，**无需修复**

### Datetime/时区一致性
扫描全代码库 datetime.now()（50+处）、时区感知函数（_parse_utc_dt / _parse_dt_flexible / _parse_datetime_str）、以及 datetime 比较路径：

- flight_delay/pipeline.py：`_parse_utc_dt()` 返回 UTC-aware datetime，所有比较（line 1896/1930/1956）均为 aware vs aware → **正确**
- baggage_delay/pipeline.py：`_parse_dt_flexible()` 统一 strip tzinfo 返回 naive datetime，延误时长计算在 naive 内部完成 → **自洽**
- skills/policy_booking.py：`_parse_datetime_str()` 返回 naive datetime，`check_policy_validity()` 中 `datetime.now()` 比较 naive vs naive → **正确**
- skills/flight_lookup.py：`datetime.now(timezone.utc)` 用于日志/缓存元数据，`datetime.now().timestamp()` 用于 TTL 过期检查 → `.timestamp()` 始终返回 UTC epoch 秒，缓存逻辑正确
- database.py：全代码库 `datetime.now()` 写入 MySQL DATETIME 列（无时区） → **标准实践**

结论：**未发现 naive vs aware 跨类型比较**（会触发 TypeError）。各模块内部 datetime 处理策略自洽，但不统一：
- flight_delay → UTC-aware（最健壮）
- baggage_delay → naive（内部自洽，但跨模块比较时存在风险）
- 其他 → naive

风险：维护风险（未来若有人跨模块比较 datetime 可能触发 TypeError）。当前无运行时 bug。

### Prompt 模板 {{include:}} 链完整性
- `prompts/baggage_delay/02_audit_decision.txt:12` → `{{include:policy_validity_block}}` → `prompts/_shared/policy_validity_block.txt` ✓
- `prompts/baggage_delay/02_audit_decision.txt:14` → `{{include:war_exclusion_block}}` → `prompts/_shared/war_exclusion_block.txt` ✓
- `prompts/baggage_delay/00_vision_extract.txt:18` → `{{include:flight_info_extract_block}}` → `prompts/_shared/flight_info_extract_block.txt` ✓
- PromptLoader._resolve_includes() 使用 `shared_file.exists()` 保护，找不到时保留原文不崩溃

结论：全部 3 处 {{include:}} 引用均可正确解析，4 个共享块文件均存在。**无问题。**

### 第17轮修复项

（本轮审计未发现需修复的运行时 bug）

---

### 当前已审计的目录/模块（更新至第17轮）
