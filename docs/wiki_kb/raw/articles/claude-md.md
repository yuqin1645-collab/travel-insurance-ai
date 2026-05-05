---
source_url: https://github.com/yuqin1645-collab/travel-insurance-ai/blob/main/CLAUDE.md
ingested: 2026-05-05
sha256: dbfd0d22128af1a5c3ee7262894074005fa8aa2a473f9943198a5afdeed72f3a
---

     1|# 项目规则
     2|
     3|## 语言要求
     4|
     5|- 所有输出、提问、回答和生成的文档内容必须使用 **中文**
     6|- 包括但不限于：OpenSpec 生成的 proposal.md、design.md、tasks.md 等文档
     7|- 代码注释使用中文
     8|- 变量名、函数名、文件名可以使用英文
     9|
    10|## 文档
    11|
    12|- 生成的文档放置在/docs
    13|
    14|---
    15|
    16|## 新险种开发规范
    17|
    18|每次开发新的险种模块时，必须按照以下流程执行，不得跳过。
    19|
    20|### 1. 开发前：先查规则知识库
    21|
    22|在开始写 pipeline 之前，先检查 `app/rules/` 目录，判断哪些逻辑可以直接复用：
    23|
    24|| 检查项 | 对应规则文件 |
    25||--------|-------------|
    26|| 保单有效期、主险状态、安联顺延规则 | `app/rules/common/policy_validity.py` |
    27|| 申请人与权益人姓名/证件号一致性 | `app/rules/common/identity_check.py` |
    28|| 必备材料门禁（关键词映射） | `app/rules/common/material_gate.py` |
    29|| 战争/罢工/恐怖活动/海关没收等除外责任 | `app/rules/flight/exclusions.py` |
    30|| 赔付档位计算 | `app/skills/compensation.py` 的 `tier_lookup()` |
    31|
    32|**原则：能复用的规则一律不重写，直接 import。**
    33|
    34|### 2. 新险种 pipeline 引用规则库的方式
    35|
    36|```python
    37|# pipeline.py 顶部导入
    38|from app.rules.common.policy_validity import check as check_policy_validity
    39|from app.rules.common.identity_check import check as check_identity
    40|from app.rules.common.material_gate import check as check_material_gate
    41|from app.rules.flight.exclusions import check as check_exclusions, BAGGAGE_DELAY_EXCLUSIONS
    42|from app.rules.claim_types.baggage_delay import BAGGAGE_DELAY_TIERS
    43|from app.skills.compensation import tier_lookup
    44|
    45|# 在 pipeline 函数中调用（返回 RuleResult）
    46|policy_result = check_policy_validity(claim_info)
    47|if not policy_result.passed:
    48|    return _result(forceid, policy_result.reason, "N", conclusions, debug)
    49|```
    50|
    51|### 3. 新规则沉淀规范
    52|
    53|当新险种有**独有的审核逻辑**，且该逻辑**未来其他险种也可能用到**时，必须将其沉淀为规则文件：
    54|
    55|**规则文件结构（每个文件必须包含以下三项）：**
    56|
    57|```python
    58|# 文件头部元数据
    59|RULE_ID = "claim_types.xxx"       # 规则唯一 ID
    60|RULE_VERSION = "1.0"
    61|DESCRIPTION = "一句话描述规则用途"
    62|
    63|# 供 {{include:}} 使用的自然语言提示词块
    64|PROMPT_BLOCK = """
    65|【规则名称】
    66|...规则描述...
    67|""".strip()
    68|
    69|# Python 判定函数，返回 RuleResult
    70|def check(claim_info: dict) -> RuleResult:
    71|    ...
    72|```
    73|
    74|**新规则文件放置位置：**
    75|- 两个以上险种共用 → `app/rules/common/`
    76|- 与航班/行李相关的飞行类逻辑 → `app/rules/flight/`
    77|- 特定险种专属 → `app/rules/claim_types/<claim_type>.py`
    78|
    79|**新建规则文件后，必须同步更新：**
    80|1. `app/rules/registry.py` 的 `RULE_REGISTRY` 字典，添加新规则的元数据
    81|2. `app/rules/__init__.py` 添加导出
    82|3. 若有共享提示词块，在 `prompts/_shared/` 下新建对应 `.txt` 文件
    83|
    84|### 4. 提示词开发规范
    85|
    86|- 两个以上险种共用的提示词段落，必须抽取到 `prompts/_shared/<block_name>.txt`
    87|- 在险种提示词中通过 `{{include:block_name}}` 引用（PromptLoader 会自动展开）
    88|- 险种特有内容（门槛、档位、特殊除外情形）保留在各险种提示词中
    89|
    90|**现有共享块：**
    91|
    92|| 文件 | 内容 |
    93||------|------|
    94|| `prompts/_shared/policy_validity_block.txt` | 保单有效期判定规则（4时间点 + 安联顺延/提前规则） |
    95|| `prompts/_shared/war_exclusion_block.txt` | 战争/社会风险/恐怖活动除外责任 |
    96|| `prompts/_shared/identity_check_block.txt` | 申请人与权益人身份匹配规则 |
    97|| `prompts/_shared/flight_info_extract_block.txt` | 航班信息识别核心规则（逐字符核对、改签/联程识别、保护航班校验、三步枚举法）及对应 JSON 字段结构 |
    98|
    99|### 5. RuleResult 数据类说明
   100|
   101|```python
   102|@dataclass
   103|class RuleResult:
   104|    passed: bool    # True=通过，False=拒赔/需补齐资料
   105|    action: str     # "approve" | "reject" | "supplement" | "continue"
   106|    reason: str     # 人类可读原因（可直接用于 Remark 字段）
   107|    detail: dict    # 调试信息（写入 DebugInfo）
   108|```
   109|
   110|### 6. 新险种开发检查清单
   111|
   112|开发完成后，对照以下清单自查：
   113|
   114|- [ ] pipeline 中无重复定义的保单有效期/身份校验/除外责任函数（必须引用规则库）
   115|- [ ] 新险种专有规则已沉淀为 `app/rules/claim_types/<type>.py`，包含 `RULE_ID`、`PROMPT_BLOCK`、`check()` 三要素
   116|- [ ] 新规则已注册到 `app/rules/registry.py`
   117|- [ ] 两险种以上共用的提示词段落已提取到 `prompts/_shared/`
   118|- [ ] 险种提示词中使用 `{{include:}}` 引用共享块，而非复制粘贴
   119|- [ ] 用至少一个真实案件（或构造的 claim_info 字典）跑通审核流程，比对 `KeyConclusions` 和 `Remark` 与预期一致
   120|
   121|### 7. Pipeline 拆分规范（强制）
   122|
   123|`pipeline.py` 文件行数**不得超过 500 行**。超过时必须拆分出 `stages/` 子目录。
   124|
   125|**`pipeline.py` 只做编排，不做实现。** 所有业务函数必须放在 `stages/` 子目录中：
   126|
   127|```
   128|app/modules/<claim_type>/
   129|├── module.py
   130|├── pipeline.py              ← 纯编排层（≤500行），只做 stage 串联
   131|└── stages/
   132|    ├── __init__.py          ← re-export 所有 stage 函数
   133|    ├── utils.py             ← 纯工具函数（_safe_float, _parse_date, _is_unknown, _result 等）
   134|    ├── handlers.py          ← handler/check 函数（保单校验、材料门禁、除外责任等）
   135|    └── calculator.py        ← 计算函数（延误时长、赔付金额等）
   136|```
   137|
   138|新增险种时：
   139|1. 先建 `stages/` 子目录（utils.py, handlers.py, calculator.py, __init__.py）
   140|2. 再写 `pipeline.py`，只从 `stages/` 导入并串联
   141|3. 禁止在 `pipeline.py` 中定义纯工具函数或业务校验函数
   142|
   143|详细规范见 [docs/module_architecture_and_new_claim_template.md](docs/module_architecture_and_new_claim_template.md) 第 2.5 节。
   144|
   145|---
   146|
   147|## 常用运维脚本
   148|
   149|执行前确保已激活虚拟环境：`venv\Scripts\python.exe` (Windows) 或 `python`（已激活 venv）。
   150|
   151|### 重跑案件（强制规则）
   152|
   153|**重跑案件必须使用 `scripts/review.py --forceid`**，该脚本会自动完成：
   154|1. 重新审核
   155|2. 推送前端
   156|3. 同步数据库
   157|
   158|**禁止**单独用 `push.py --forceid` 做数据库同步（历史 bug：`_extract_review_fields()` 返回三元组，push.py 曾直接传元组给 `_sync_to_db()` 导致同步失败，现已修复但 review.py 是更可靠的入口）。
   159|
   160|### AI vs 人工差异追踪
   161|
   162|每次重跑后必须检查 [docs/issue_cluster_tracker.md](docs/issue_cluster_tracker.md)，该文档记录了：
   163|- P0/P1/P2 分类及数量
   164|- 数据库一致性统计
   165|- 根因分析和修复历程
   166|- 趋势数据
   167|
   168|重跑导致结论变化时，必须同步更新该文档。
   169|
   170|### 统一入口脚本（推荐）
   171|
   172|| 脚本 | 用途 | 典型用法 |
   173||------|------|---------|
   174|| `scripts/review.py` | 统一审核入口（批量/重跑/统计） | `python review.py`（全量）<br>`python review.py --type baggage`（只行李）<br>`python review.py --forceid xxx`（重跑指定）<br>`python review.py --redownloaded`（重审未审核）<br>`python review.py --analyze`（统计分布） |
   175|| `scripts/push.py` | 统一推送入口（前端+数据库） | `python push.py --forceid xxx`（推单个）<br>`python push.py --all --type baggage`（批量）<br>`python push.py --sync-db`（同步数据库）<br>`python push.py --sync-db --dry-run`（预览） |
   176|| `scripts/report.py` | 统一报表入口 | `python report.py --type flight`（航班报表）<br>`python report.py --type baggage`（行李报表）<br>`python report.py --type compare`（AI vs 人工） |
   177|| `scripts/query.py` | 统一查询入口 | `python query.py forceid xxx`（查案件）<br>`python query.py status`（数据库状态）<br>`python query.py count`（统计数量） |
   178|| `scripts/data.py` | 统一数据管理入口 | `python data.py download`（全量下载）<br>`python data.py sync --no-delete`（API同步）<br>`python data.py restore --skip-existing`（从数据库恢复） |
   179|
   180|### 独立脚本
   181|
   182|| 脚本 | 用途 | 典型用法 |
   183||------|------|---------|
   184|| `scripts/download_claims.py` | 下载理赔材料（核心库，被 scheduler 等模块直接 import，**不要删除或改名**） | `python scripts/download_claims.py` |
   185|| `scripts/sync_manual_status.py` | 从接口拉取人工处理状态，更新数据库 `benefit_name / manual_status / manual_conclusion` | `python scripts/sync_manual_status.py` |
   186|| `scripts/upload_ai_conclusion.py` | 上传 AI 结论到 Salesforce | `python scripts/upload_ai_conclusion.py` |
   187|| `scripts/import_segments_from_local.py` | 导入联程航段数据到数据库 | `python scripts/import_segments_from_local.py` |
   188|| `scripts/find_claim_by_forceid.py` | 根据 forceid/ClaimId 查找案件路径；也可作为模块导入 `fetch_by_forceid()` | `python scripts/find_claim_by_forceid.py`（交互）<br>`python scripts/find_claim_by_forceid.py xxx`（直接查询） |
   189|| `scripts/restore_claims_from_db.py` | 从数据库 `ai_claim_info_raw` 恢复 `claims_data` 目录并重新下载材料文件（误删后恢复用） | `python scripts/restore_claims_from_db.py --skip-existing` |
   190|| `scripts/sync_claims_from_api.py` | 从接口拉取案件列表并更新 ClaimId。**日常增量用 `--no-delete`**；直接运行会删除本地不在接口中的目录（超20个需手动确认） | `python scripts/sync_claims_from_api.py --no-delete` |