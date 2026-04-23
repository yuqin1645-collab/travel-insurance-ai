# 项目规则

## 语言要求

- 所有输出、提问、回答和生成的文档内容必须使用 **中文**
- 包括但不限于：OpenSpec 生成的 proposal.md、design.md、tasks.md 等文档
- 代码注释使用中文
- 变量名、函数名、文件名可以使用英文

## 文档

- 生成的文档放置在/docs

---

## 新险种开发规范

每次开发新的险种模块时，必须按照以下流程执行，不得跳过。

### 1. 开发前：先查规则知识库

在开始写 pipeline 之前，先检查 `app/rules/` 目录，判断哪些逻辑可以直接复用：

| 检查项 | 对应规则文件 |
|--------|-------------|
| 保单有效期、主险状态、安联顺延规则 | `app/rules/common/policy_validity.py` |
| 申请人与权益人姓名/证件号一致性 | `app/rules/common/identity_check.py` |
| 必备材料门禁（关键词映射） | `app/rules/common/material_gate.py` |
| 战争/罢工/恐怖活动/海关没收等除外责任 | `app/rules/flight/exclusions.py` |
| 赔付档位计算 | `app/skills/compensation.py` 的 `tier_lookup()` |

**原则：能复用的规则一律不重写，直接 import。**

### 2. 新险种 pipeline 引用规则库的方式

```python
# pipeline.py 顶部导入
from app.rules.common.policy_validity import check as check_policy_validity
from app.rules.common.identity_check import check as check_identity
from app.rules.common.material_gate import check as check_material_gate
from app.rules.flight.exclusions import check as check_exclusions, BAGGAGE_DELAY_EXCLUSIONS
from app.rules.claim_types.baggage_delay import BAGGAGE_DELAY_TIERS
from app.skills.compensation import tier_lookup

# 在 pipeline 函数中调用（返回 RuleResult）
policy_result = check_policy_validity(claim_info)
if not policy_result.passed:
    return _result(forceid, policy_result.reason, "N", conclusions, debug)
```

### 3. 新规则沉淀规范

当新险种有**独有的审核逻辑**，且该逻辑**未来其他险种也可能用到**时，必须将其沉淀为规则文件：

**规则文件结构（每个文件必须包含以下三项）：**

```python
# 文件头部元数据
RULE_ID = "claim_types.xxx"       # 规则唯一 ID
RULE_VERSION = "1.0"
DESCRIPTION = "一句话描述规则用途"

# 供 {{include:}} 使用的自然语言提示词块
PROMPT_BLOCK = """
【规则名称】
...规则描述...
""".strip()

# Python 判定函数，返回 RuleResult
def check(claim_info: dict) -> RuleResult:
    ...
```

**新规则文件放置位置：**
- 两个以上险种共用 → `app/rules/common/`
- 与航班/行李相关的飞行类逻辑 → `app/rules/flight/`
- 特定险种专属 → `app/rules/claim_types/<claim_type>.py`

**新建规则文件后，必须同步更新：**
1. `app/rules/registry.py` 的 `RULE_REGISTRY` 字典，添加新规则的元数据
2. `app/rules/__init__.py` 添加导出
3. 若有共享提示词块，在 `prompts/_shared/` 下新建对应 `.txt` 文件

### 4. 提示词开发规范

- 两个以上险种共用的提示词段落，必须抽取到 `prompts/_shared/<block_name>.txt`
- 在险种提示词中通过 `{{include:block_name}}` 引用（PromptLoader 会自动展开）
- 险种特有内容（门槛、档位、特殊除外情形）保留在各险种提示词中

**现有共享块：**

| 文件 | 内容 |
|------|------|
| `prompts/_shared/policy_validity_block.txt` | 保单有效期判定规则（4时间点 + 安联顺延/提前规则） |
| `prompts/_shared/war_exclusion_block.txt` | 战争/社会风险/恐怖活动除外责任 |
| `prompts/_shared/identity_check_block.txt` | 申请人与权益人身份匹配规则 |
| `prompts/_shared/flight_info_extract_block.txt` | 航班信息识别核心规则（逐字符核对、改签/联程识别、保护航班校验、三步枚举法）及对应 JSON 字段结构 |

### 5. RuleResult 数据类说明

```python
@dataclass
class RuleResult:
    passed: bool    # True=通过，False=拒赔/需补件
    action: str     # "approve" | "reject" | "supplement" | "continue"
    reason: str     # 人类可读原因（可直接用于 Remark 字段）
    detail: dict    # 调试信息（写入 DebugInfo）
```

### 6. 新险种开发检查清单

开发完成后，对照以下清单自查：

- [ ] pipeline 中无重复定义的保单有效期/身份校验/除外责任函数（必须引用规则库）
- [ ] 新险种专有规则已沉淀为 `app/rules/claim_types/<type>.py`，包含 `RULE_ID`、`PROMPT_BLOCK`、`check()` 三要素
- [ ] 新规则已注册到 `app/rules/registry.py`
- [ ] 两险种以上共用的提示词段落已提取到 `prompts/_shared/`
- [ ] 险种提示词中使用 `{{include:}}` 引用共享块，而非复制粘贴
- [ ] 用至少一个真实案件（或构造的 claim_info 字典）跑通审核流程，比对 `KeyConclusions` 和 `Remark` 与预期一致

---

## 常用运维脚本

执行前确保已激活虚拟环境：`venv\Scripts\python.exe` (Windows) 或 `python`（已激活 venv）。

### AI 审核

| 脚本 | 用途 | 典型用法 |
|------|------|---------|
| `scripts/batch_review_flight.py` | 对 `claims_data/航班延误/` 下所有案件跑 AI 审核，结果写到 `review_results/flight_delay/` | `python scripts/batch_review_flight.py` |
| `scripts/batch_review_baggage.py` | 对 `claims_data/行李延误/` 下所有案件跑 AI 审核，结果写到 `review_results/baggage_delay/` | `python scripts/batch_review_baggage.py` |
| `scripts/rerun_claims.py` | 强制重跑指定 forceid 的 AI 审核（忽略 Final_Status 过滤） | `python scripts/rerun_claims.py <forceid1> <forceid2> ...` |

### 推送前端 & 数据库同步

| 脚本 | 用途 | 典型用法 |
|------|------|---------|
| `scripts/push_existing_results.py` | 推送**指定** forceid 的已有审核结果到前端 + 数据库（不重新审核，适用于航班/行李任意险种） | `python scripts/push_existing_results.py <forceid1> <forceid2> ...` |
| `scripts/push_baggage_all.py` | 批量推送 `review_results/baggage_delay/` 下**全部**行李延误审核结果到前端 + 数据库 | `python scripts/push_baggage_all.py` |
| `scripts/sync_manual_status.py` | 从接口拉取人工处理状态，更新数据库 `benefit_name / manual_status / manual_conclusion` | `python scripts/sync_manual_status.py` |

### 报表 & 查询

| 脚本 | 用途 | 典型用法 |
|------|------|---------|
| `scripts/export_flight_delay_ai_report.py` | 导出航班延误 AI 审核结果到 Excel（ClaimId / PolicyNo / 状态 / 结论） | `python scripts/export_flight_delay_ai_report.py` |
| `scripts/generate_baggage_report.py` | 生成行李延误审核结果 Excel 报告 | `python scripts/generate_baggage_report.py` |
| `scripts/export_ai_vs_manual_report.py` | 生成 AI vs 人工审核对比报表 xlsx | `python scripts/export_ai_vs_manual_report.py` |
| `scripts/find_claim_by_forceid.py` | 交互式查询 forceid/ClaimId 对应的本地案件路径 | `python scripts/find_claim_by_forceid.py` |

### 数据下载 & 同步

| 脚本 | 用途 | 典型用法 |
|------|------|---------|
| `scripts/download_claims.py` | 下载理赔材料（支持断点续传、自动文件类型检测） | `python scripts/download_claims.py` |
| `scripts/sync_claims_from_api.py` | 从接口拉取案件列表并更新 ClaimId。**日常增量用 `--no-delete`**；直接运行会删除本地不在接口中的目录（超20个需手动确认） | `python scripts/sync_claims_from_api.py --no-delete` |
| `scripts/restore_claims_from_db.py` | 从数据库 `ai_claim_info_raw` 恢复 `claims_data` 目录并重新下载材料文件（误删后恢复用） | `python scripts/restore_claims_from_db.py --skip-existing` |