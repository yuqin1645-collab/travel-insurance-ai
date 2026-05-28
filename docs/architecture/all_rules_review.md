# 旅行险AI自动理赔系统 — 全部规则梳理文档

> 生成时间: 2026-05-22 | 最近更新: 2026-05-28（补件兜底规则详解+规则变更说明）
> 目的: 领导要求暂停项目后，将航班延误和行李延误两个模块的**所有规则逻辑**（代码+Prompt）完整梳理出来，明确AI的作用边界和工程解决方案。

---

## 一、系统架构总览

### 整体设计思想

系统采用**混合判定架构**：
- **代码侧规则**: 确定性逻辑，100%稳定，不可被AI推翻
- **AI侧判定**: 处理非结构化材料（图片/PDF/文本），输出可能不稳定
- **后处理兜底**: 代码覆盖AI误判，确保最终结果确定性

### 核心原则

> **AI只做提取和辅助判定，规则判定全部在代码中完成。**

这是系统最重要的设计原则。AI的输出（提取结果、审核意见）会被代码侧规则验证、修正、覆盖。

---

## 二、航班延误险规则（重点）

### 2.1 审核流程总览

```
输入: claim_info.json + 材料图片/文本
 │
 ├── stage0_duplicate: 重复理赔检测（纯代码）
 ├── stage0_vision:    视觉/OCR材料抽取（AI: VISION模型）
 ├── stage1:           AI数据解析与时区标准化（AI: MEDIUM模型）
 ├── stage1.2:         合并Vision抽取结果（纯代码）
 ├── stage1.3:         飞常准航班权威数据查询（API调用）
 ├── stage1.4:         接驳/替代航班飞常准查询（API调用）
 ├── stage_hardcheck:  代码侧硬校验集合（纯代码）
 ├── stage10:          赔付金额预计算（纯代码）
 ├── stage2_precheck:  硬免责前置拦截（纯代码）
 ├── stage2:           AI理赔判定（AI: MEDIUM模型）
 └── postprocess:      规则兜底后处理（纯代码）
 │
 └─→ 输出: audit_result + remark + KeyConclusions
```

**AI调用点**: stage0_vision、stage1、stage2 — 共3处
**纯代码判定**: 其余全部 — 共7处

### 2.2 代码侧规则（确定性，100%稳定）

#### 规则1: 重复理赔检测
**文件**: `app/modules/flight_delay/stages/duplicate.py`

| 检查项 | 规则 | 结果 |
|--------|------|------|
| 同一身份证号 | ID_Number 相同 | 继续检查 |
| 同一产品 | Product_Name 相同 | 继续检查 |
| 同一险种 | BenefitName 相同 | 继续检查 |
| 同一事故日期 | Date_of_Accident 前10字符相同 | 继续检查 |
| 同一航班号 | Flight_No 相同（去空格） | **同一事件** |
| 不同航班号 | Flight_No 不同 | **不视为重复** |

**已结案状态词**（命中即拒赔）:
`零结关案`、`支付成功`、`事后理赔拒赔`、`取消理赔`、`结案待财务付款`、`approved`、`rejected`、`settled`、`closed`、`已赔付`、`已结案`、`已关闭`

**拒赔话术**: `重复理赔：您本次申请的理赔已在#{案件号}做出赔付结论。根据一事不二理原则，本次重复申请不予赔付。`

#### 规则2: 纯国内航班检查
**文件**: `app/modules/flight_delay/stages/hardcheck.py`

| 条件 | 规则 |
|------|------|
| 出发地 = CN 且 目的地 = CN | **拒赔** |
| 任一端非CN或未知 | 继续 |

#### 规则3: 保单有效期检查
**文件**: `app/rules/common/policy_validity.py` + `hardcheck.py`

| 检查点 | 规则 |
|--------|------|
| 5时间点任一在期内 | 通过（保单生效日/事故日期/出境时间/计划起飞时间/航班日期） |
| 事故日期明确且超期 | **拒赔** |
| 安联顺延规则 | 实际出境时间与生效日差距≤15天→顺延生效日 |
| 航班日期不完整 | **需补齐资料** |

#### 规则4: 境内中转免责
**文件**: `app/modules/flight_delay/stages/hardcheck.py`

| 条件 | 规则 |
|------|------|
| 中转地在境内(CN) | **拒赔** |
| 非联程中转 | 无需判定 |

#### 规则5: 中转接驳延误免责
**文件**: `app/modules/flight_delay/stages/hardcheck.py`

| 条件 | 规则 |
|------|------|
| 前序实际到达 > 末段计划出发 | **拒赔**（免责情形4） |
| 前程正常到达 | 不触发免责 |
| 改签场景+无法确认前序延误 | 豁免 |
| 超售/拒绝登机 | 豁免 |

#### 规则6: 非客运航班检查
**文件**: `app/modules/flight_delay/stages/hardcheck.py`

| 条件 | 规则 |
|------|------|
| is_passenger_civil = False | **拒赔** |

#### 规则7: 同天投保免责
**文件**: `app/modules/flight_delay/stages/validators.py` + `hardcheck.py`

| 条件 | 规则 |
|------|------|
| 投保时刻 ≥ 计划起飞时刻 | **拒赔** |
| 投保在计划起飞前1天内+航班已取消 | 拒赔（既存条件） |
| 投保在计划起飞前≥7天+航班已取消 | **拒赔**（欺诈嫌疑） |

#### 规则8: 姓名匹配检查
**文件**: `app/modules/flight_delay/stages/validators.py`

| 条件 | 规则 |
|------|------|
| 材料姓名 = 保单姓名 | 通过 |
| Relationship_with_Insured ≠ "本人" | 通过（家属代办） |
| Relationship_with_Insured = "本人" 但姓名不匹配 | **拒赔** |
| 拼音 vs 中文（跨文字系统） | unknown → 人工确认 |

#### 规则9: 可预见因素/欺诈检测
**文件**: `app/modules/flight_delay/stages/hardcheck.py`

| 条件 | 规则 |
|------|------|
| 投保/订票 ≤ 事故日3天 + 台风/罢工/暴风/洪水 | **拒赔**（欺诈嫌疑） |
| 航班提前取消 + 投保远早于起飞 | **拒赔**（既存条件） |

#### 规则10: 承保区域检查
**文件**: `app/modules/flight_delay/stages/validators.py`

| 条件 | 规则 |
|------|------|
| 出行区域不在保险计划覆盖范围内 | **拒赔** |
| 支持区域: 全球/亚洲/欧洲/美洲/非洲/大洋洲 | |

#### 规则11: 战争风险检查
**文件**: `app/modules/flight_delay/stages/hardcheck.py`

| 条件 | 规则 |
|------|------|
| 出发地/目的地/中转地在战争风险表 | **记录警告，不自动拒赔** |

#### 规则12: 必备材料硬检查
**文件**: `app/modules/flight_delay/stages/hardcheck.py`

| 必备材料 | 兜底规则 |
|---------|---------|
| 权益补偿给付申请书 | claim_info非空且有描述时推断已提供 |
| 保险凭证/会员权益卡 | 保单号+身份证号双匹配+身份证已提供→推断已提供 |
| 申请人身份证明 | claim_info有ID_Type+ID_Number时推断已提供 |
| 承运人延误书面证明 | 3层兜底：①飞常准延误证明确认→推断已满足 ②描述文本含航班号+延误关键词→推断已满足 ③改签场景+飞常准查到→推断已满足 |
| 登机牌或电子客票行程单 | **严格要求，不允许兜底**（延误证明+身份证明齐全不能替代，Vision提取到航班号也不能替代） |
| 被保险人护照照片页 | 2种兜底：①身份证保单+国际航班→推断已提供 ②证件类型为护照→推断已提供 |

**出入境兜底**: 国际航班确认+任一旅行证件→推断已出入境；飞常准延误证明已确认→推断已出入境

#### 规则13: 延误时长计算
**文件**: `app/modules/flight_delay/stages/delay_calc.py`

| 计算口径（优先级） | 规则 |
|-------------------|------|
| 口径1: chain[0]原始计划→飞常准实际 | 联程/中转场景跳过 |
| 口径2: 计划→替代航班alt | 取max(口径2, 口径3) |
| 口径3: 计划→飞常准实际 | 兜底 |
| 延误证明上限 | 明确记录时长时以证明为准 |
| 文本提取兜底 | 从事故描述提取"延误X小时Y分钟" |
| alt时间>原计划7天 | 置空（疑似无关登机牌） |

#### 规则14: 赔付金额计算
**文件**: `app/skills/compensation.py`

| 延误时长 | 赔付金额 |
|---------|---------|
| 满5小时不足10小时 | 300元 |
| 满10小时不足15小时 | 600元 |
| 满15小时不足20小时 | 900元 |
| 满20小时及以上 | 1200元（最高保额） |

**计算规则**: `min(档位金额, 申请金额, 保额, 剩余保额)`

### 2.3 AI侧规则（非确定性，需要工程控制）

#### AI调用1: 视觉/OCR材料抽取（stage0_vision）

**模型**: VISION（图片理解）
**Prompt**: `prompts/flight_delay/00_vision_extract.txt`（~37KB）
**输入**: 材料图片/PDF
**输出**: 结构化JSON（航班列表、证据材料、行程段等）

**AI做什么**:
- 枚举所有航班（不遗漏）
- 识别航班号（逐字符核对，6/8、3/8、1/7易混淆）
- 提取机场三字码
- 识别材料类型（登机牌/行程单/延误证明等）
- 识别改签/联程场景
- 提取证据材料信息（有无登机牌、延误证明等）

**AI不稳定的工程解决**:
1. 抽取结果类型校验（dict/list fallback）
2. Vision只填补unknown/null字段，不覆盖已有非unknown值
3. 抽取失败降级到纯文本处理
4. 后续飞常准API校验AI提取的航班是否存在

#### AI调用2: 数据解析与时区标准化（stage1）

**模型**: MEDIUM
**Prompt**: `prompts/flight_delay/01_data_parse_and_timezone.txt`
**输入**: claim_info.json + 材料OCR文本
**输出**: 结构化JSON（保单信息、航班信息、时间信息等）

**AI做什么**:
- 提取保单信息（保险公司、保单号、有效期）
- 提取航班信息（航班号、承运人、出发到达IATA）
- 提取时间信息（计划起飞/到达、实际起飞/到达、替代航班时间）
- 时区标准化（将时间转换为带时区的本地时间）
- 识别联程/中转信息
- 提取证据材料评估

**AI不稳定的工程解决**:
1. StageRunner重试机制（max_retries=2，指数退避）
2. 解析失败直接转人工（build_stage_error_return）
3. 后续hardcheck会验证AI提取的数据完整性
4. 飞常准API补强AI提取的航班数据（stage1.3）

#### AI调用3: 理赔判定（stage2）

**模型**: MEDIUM
**Prompt**: `prompts/flight_delay/02_audit_decision.txt`
**输入**: parsed（结构化数据）+ 条款摘录 + 硬校验结果 + 赔付预计算
**输出**: `audit_result`（通过/拒绝/需补齐资料）+ `explanation` + `payout_json`

**AI做什么**:
- 综合判断是否赔付
- 考虑战争因素、保单状态、有效期、身份匹配
- 判断航班属性、延误原因、承保范围
- 延误时长取长原则判定
- 登机牌认定与替代交通工具判定
- 免责情形判断
- 可预见因素/反欺诈

**AI不稳定的工程解决 — 核心防御体系**:

```
┌─────────────────────────────────────────────┐
│ 第一层：Retry + Circuit Breaker             │
│ - 最多3次重试，指数退避                       │
│ - 连续5次失败→熔断30秒                        │
├─────────────────────────────────────────────┤
│ 第二层：5层JSON解析fallback                  │
│ 1. json_repair修复                           │
│ 2. json.loads直接解析                        │
│ 3. 正则提取{...}                             │
│ 4. 手动修复转义符                            │
│ 5. 截断修复（补}直到有效JSON）               │
├─────────────────────────────────────────────┤
│ 第三层：hardcheck前置拦截                    │
│ - 硬免责命中→直接返回，不调AI                │
│ - 硬校验结果注入parsed，AI只能参考不能覆盖   │
├─────────────────────────────────────────────┤
│ 第四层：postprocess兜底（最重要）             │
│ - 硬校验通过+延误达标+材料齐全               │
│   但AI输出"拒绝" → 覆盖为"通过"              │
│ - 硬校验确认材料齐全                          │
│   但AI输出"补件" → 覆盖为"通过"              │
│ - 延误时长<门槛 → 覆盖为"拒绝"               │
│ - 缺必备材料 → 覆盖为"需补齐资料"            │
└─────────────────────────────────────────────┘
```

### 2.4 Prompt中的规则（非代码，但影响AI行为）

#### Prompt: 02_audit_decision.txt 中的判定逻辑

**优先级顺序**:
1. 战争因素免责（最高优先级）
2. 重复理赔检测（代码优先）
3. 前置准入审核（保单状态、有效期、身份）
4. 基础赔付条件（航班属性、延误原因、承保范围）
5. 核心赔付条件（延误时长取长原则）
6. 登机牌认定与替代交通工具判定
7. 免责情形（书面证明、未准时登乘、联程限制）
8. 可预见因素/反欺诈

**共享Prompt块**（通过 `{{include:}}` 引用）:

| 共享块 | 文件 | 内容 |
|--------|------|------|
| policy_validity_block | `prompts/_shared/policy_validity_block.txt` | 保单有效期判定（4时间点+安联顺延） |
| identity_check_block | `prompts/_shared/identity_check_block.txt` | 身份匹配规则 |
| war_exclusion_block | `prompts/_shared/war_exclusion_block.txt` | 战争/社会风险/恐怖活动除外 |
| flight_info_extract_block | `prompts/_shared/flight_info_extract_block.txt` | 航班信息识别核心规则 |

---

## 三、行李延误险规则

### 3.1 审核流程总览

```
输入: claim_info.json + 材料图片/文本
 │
 ├── stage0_duplicate: 重复理赔检测（纯代码）
 ├── stage0_vision:    视觉/OCR材料抽取（AI: VISION）
 ├── stage0_5:         AI结构化抽取+视觉合并（AI: MEDIUM）
 ├── 前置准入:          保单有效期/身份/纯国内/除外责任（纯代码）
 ├── stage1:           飞常准航班权威数据查询（API）
 ├── stage2:           转运航班到达时间回退（API）
 ├── stage3:           实际到达vs保单有效期（纯代码）
 ├── stage4:           事故类型校验（丢失vs延误）（AI+代码）
 ├── stage5:           材料门禁（纯代码+规则库）
 └── stage6:           AI审核+赔付核算（AI+代码）
 │
 └─→ 输出: audit_result + remark + KeyConclusions
```

**AI调用点**: stage0_vision、stage0_5、PIR二次提取、stage6 — 共4处

### 3.2 代码侧规则

#### 规则1: 起赔门槛
**起赔条件**: 行李延误满 **6小时**

#### 规则2: 赔付档位

| 延误时长 | 赔付金额 |
|---------|---------|
| 6h ≤ delay < 12h | 500元 |
| 12h ≤ delay < 18h | 1000元 |
| delay ≥ 18h | 1500元 |

#### 规则3: 延误时长计算
**文件**: `app/modules/baggage_delay/stages/calculator.py`

| 计算规则 | 说明 |
|---------|------|
| 签收时间 - 到达时间 | 基础计算方式 |
| 改签场景 | 行李签收时间=改签航班到达时间→0小时 |
| 签收时间来源 | 仅 actual_receipt/airport_counter/courier_delivery 有效 |
| 00:00占位符检测 | 仅有日期无时间→标记警告 |

#### 规则4: 事故类型校验（丢失 vs 延误）
**文件**: `app/modules/baggage_delay/stages/accident_validation.py`

| 条件 | 规则 |
|------|------|
| 有签收时间/可计算延误时长/有延误证明 | 按延误审核（不是丢失） |
| 文本含"找到/送达/领取/收到" | 按延误审核 |
| 行李丢失单独触发 | 拒赔，转随身财产损失 |

#### 规则5: 材料门禁
**文件**: `app/modules/baggage_delay/stages/material_gate.py`

必备材料:
1. 交通票据（机票/登机牌/行程单）
2. 行李延误证明或行李签收单（二选一）
3. 托运行李牌照片
4. 被保险人身份证或护照
5. 护照照片页、签证页、出入境盖章页
6. 银行卡（可选，有警告）

**例外**: 航空公司官方行李记录→视同行李牌已提供

#### 规则6: 特殊场景材料校验
**文件**: `app/modules/baggage_delay/stages/handlers.py`

| 场景 | 规则 |
|------|------|
| 未成年人 | 需监护人材料 |
| 委托代办 | 需委托关系证明 |

#### 规则7: 行李转运航班过滤
**文件**: `app/modules/baggage_delay/stages/aviation_lookup.py`

识别并排除行李转运航班号（非乘客航班），使用乘客航班进行飞常准查询。

#### 规则8: 共用规则（同航班延误）

| 规则 | 文件 |
|------|------|
| 保单有效期 | `app/rules/common/policy_validity.py` |
| 身份匹配 | `app/rules/common/identity_check.py` |
| 材料门禁 | `app/rules/common/material_gate.py` |
| 除外责任 | `app/rules/flight/exclusions.py` |
| 重复理赔 | `app/modules/flight_delay/stages/duplicate.py` |

### 3.3 AI侧规则

#### AI调用1: 视觉/OCR材料抽取
**Prompt**: `prompts/baggage_delay/00_vision_extract.txt`
**AI做什么**: 识别行李牌、PIR报告、延误证明、登机牌等材料

#### AI调用2: 数据解析
**Prompt**: `prompts/baggage_delay/01_data_parse_and_timezone.txt`
**AI做什么**: 提取保单信息、航班信息、行李签收时间、PIR编号等

#### AI调用3: PIR签收时间二次提取
**Prompt**: `prompts/baggage_delay/00b_pir_receipt_time_extract.txt`
**AI做什么**: 当无签收时间时，从PIR报告中提取签收时间

#### AI调用4: 审核决策
**Prompt**: `prompts/baggage_delay/02_audit_decision.txt`
**AI做什么**: 综合判断行李延误是否赔付

### 3.4 兜底逻辑汇总

| 场景 | 兜底方案 |
|------|---------|
| 视觉识别失败 | 降级到纯文本处理 |
| 官方航班查询失败 | 分三类：system_error/evidence_gap/none |
| 签收时间缺失 | 转运航班到达时间代理 |
| 签收时间不可靠 | GDS结案记录代码检测 |
| 行李转运航班误用 | 排除行李转运航班号 |
| 次日场景日期错误 | 自动+1天修正 |
| 行李丢失误判 | 有签收时间→按延误审核 |
| 签收时间=航班到达 | 清除，标记无签收证明 |
| Vision交叉校验 | has_baggage_delay_proof误判自动纠正 |
| 矛盾检测 | document_sources.absent vs flag=true 冲突检测 |

---

## 四、AI不稳定性的工程解决方案

### 4.1 系统为什么需要工程方案

AI（LLM）输出的不稳定性表现在:
1. **格式不稳定**: JSON可能残缺、转义错误、被截断
2. **内容不一致**: 同一案件不同次调用可能输出不同结果
3. **逻辑错误**: AI可能遗漏重要信息或做出不合理推断
4. **网络问题**: API调用可能超时或失败

### 4.2 工程防御体系（4层）

#### 第一层: 可靠性保障（Retry + Circuit Breaker）

| 机制 | 参数 | 作用 |
|------|------|------|
| StageRunner重试 | max_retries=3, base_sleep=3s, 指数退避 | 临时故障自动恢复 |
| 网络错误 | wait时间×3 | 网络抖动容忍 |
| 熔断器 | 连续5次失败→OPEN 30s | 防止服务雪崩 |
| 最大等待 | 60秒上限 | 防止无限等待 |

#### 第二层: JSON解析保障（5层fallback）

```
输入: AI返回的文本
  ↓
1. json_repair 修复（修复残缺JSON）
  ↓ 失败
2. json.loads 直接解析
  ↓ 失败
3. 正则提取 {.*?}（从自由文本中提取JSON块）
  ↓ 失败
4. 手动修复转义符（\n、\" 等）
  ↓ 失败
5. 截断修复（追加 } 直到有效，最多5次）
```

#### 第三层: 代码前置拦截

```
在AI判定之前，先跑代码侧规则：
- 硬免责命中 → 直接返回，不调AI
- 硬校验结果注入parsed
- AI只能"参考"代码判定，不能"覆盖"代码判定
```

#### 第四层: 后处理兜底（最重要的防御）

**文件**: `app/modules/flight_delay/stages/postprocess.py`

```
输入: AI的audit_result + hardcheck结果 + parsed数据
  ↓
优先级1: 硬免责条款检查
  → 命中任一则覆盖为"拒绝"
  ↓
优先级2: 延误时长门槛检查
  → <300分钟覆盖为"拒绝"
  ↓
优先级3: 必备材料缺失检查
  → 缺材料覆盖为"需补齐资料"
  ↓
优先级4: AI误判覆盖
  → 硬校验通过但AI判"补件" → 覆盖为"通过"
  → 硬校验通过+延误达标但AI判"拒绝" → 覆盖为"通过"
  ↓
输出: 最终确定性的audit_result
```

### 4.3 AI的角色定位

| 任务 | AI做还是代码做 | 原因 |
|------|---------------|------|
| 重复理赔检测 | **纯代码** | 确定性匹配，不需要AI |
| 视觉/OCR材料抽取 | **AI** | 图片理解是AI强项 |
| 数据解析提取 | **AI** | 非结构化文本需要AI理解 |
| 时区标准化 | **AI** | 需要理解时间上下文 |
| 飞常准数据查询 | **API** | 权威数据源，不需要AI |
| 硬免责检查 | **纯代码** | 确定性判定，不可出错 |
| 延误时长计算 | **纯代码** | 数学计算，不需要AI |
| 材料完整性检查 | **纯代码** | 规则明确，不需要AI |
| 理赔综合判定 | **AI**（后被代码兜底） | 需要综合理解多因素 |
| 赔付金额计算 | **纯代码** | 档位查询，不需要AI |
| 最终结果覆盖 | **纯代码** | 确保结果确定性 |

**总结**: AI负责"提取和理解"，代码负责"判定和兜底"。

### 4.4 已知问题和风险

| 问题 | 影响 | 状态 |
|------|------|------|
| `claim_info` 为空 | 同天投保/重复理赔检测失效，8件AI误赔 | 待修复下载链路 |
| `benefit_name` 缺失 | 118件无险种信息，影响AI准确率 | 待修复 |
| `history_helpers.py` bug | AI历史记录一条未写入 | 已修复 |
| `main_workflow.py` 缺DictCursor | 130条AI历史写入失败 | 已修复 |
| 登机牌兜底未正式化 | 45件AI补件→人工通过 | 待优化 |
| 监护人材料规则过严 | 28件AI补件→人工通过 | 待优化 |

---

## 五、规则文件索引

### 航班延误

| 规则 | 代码文件 | Prompt文件 |
|------|---------|-----------|
| 模块定义 | `app/modules/flight_delay/module.py` | — |
| 主流程编排 | `app/modules/flight_delay/pipeline.py` | — |
| 重复理赔 | `stages/duplicate.py` | — |
| 视觉抽取 | `stages/vision_merge.py` | `prompts/flight_delay/00_vision_extract.txt` |
| 数据解析 | `stages/ai_calls.py` | `prompts/flight_delay/01_data_parse_and_timezone.txt` |
| 飞常准查询 | `stages/aviation_lookup.py` + `alt_flight_lookup.py` | — |
| 硬校验 | `stages/hardcheck.py` | — |
| 延误计算 | `stages/delay_calc.py` | — |
| 赔付计算 | `stages/payout.py` + `app/skills/compensation.py` | — |
| 理赔判定 | `stages/ai_calls.py` | `prompts/flight_delay/02_audit_decision.txt` |
| 后处理兜底 | `stages/postprocess.py` | — |
| 校验函数 | `stages/validators.py` | — |
| 工具函数 | `stages/utils.py` | — |

### 行李延误

| 规则 | 代码文件 | Prompt文件 |
|------|---------|-----------|
| 模块定义 | `app/modules/baggage_delay/module.py` | — |
| 主流程编排 | `app/modules/baggage_delay/pipeline.py` | — |
| 重复理赔 | `stages/duplicate.py` (复用) | — |
| 视觉抽取 | `stages/vision_merge.py` | `prompts/baggage_delay/00_vision_extract.txt` |
| 数据解析 | `stages/ai_calls.py` | `prompts/baggage_delay/01_data_parse_and_timezone.txt` |
| PIR提取 | `stages/ai_calls.py` | `prompts/baggage_delay/00b_pir_receipt_time_extract.txt` |
| 飞常准查询 | `stages/aviation_lookup.py` | — |
| 事故校验 | `stages/accident_validation.py` | — |
| 材料门禁 | `stages/material_gate.py` | — |
| 处理器 | `stages/handlers.py` | — |
| 延误/赔付计算 | `stages/calculator.py` | — |
| 后处理 | `stages/post_process.py` | `prompts/baggage_delay/02_audit_decision.txt` |
| 工具函数 | `stages/utils.py` | — |

### 共用规则库

| 规则 | 代码文件 |
|------|---------|
| 保单有效期 | `app/rules/common/policy_validity.py` |
| 身份匹配 | `app/rules/common/identity_check.py` |
| 材料门禁 | `app/rules/common/material_gate.py` |
| 除外责任（航班/行李） | `app/rules/flight/exclusions.py` |
| 行李延误专属规则 | `app/rules/claim_types/baggage_delay.py` |
| 赔付档位计算 | `app/skills/compensation.py` |

### 共享Prompt块

| 共享块 | 文件 |
|--------|------|
| 保单有效期判定 | `prompts/_shared/policy_validity_block.txt` |
| 身份匹配规则 | `prompts/_shared/identity_check_block.txt` |
| 战争除外责任 | `prompts/_shared/war_exclusion_block.txt` |
| 航班信息识别 | `prompts/_shared/flight_info_extract_block.txt` |

---

## 六、补件兜底规则详解（2026-05-28新增）

> 本节详细梳理当案件中**真的没有某个材料**时，代码中有哪些**兜底/豁免规则**——即满足哪些条件时，该材料就**不需要补件**了。

### 6.1 航班延误险补件兜底

#### 规则A：硬校验覆盖AI误判补件
**文件**: `app/modules/flight_delay/stages/postprocess.py:244-268`

**触发条件（3个必须同时满足）**:
1. `missing_required = []` — 硬校验确认无缺失材料
2. `scanned_all_attachments = True` — 所有附件已扫描完毕
3. `vision_result_is_empty = False` — Vision提取到了有效数据

**效果**: 将AI误判的"需补齐资料"直接覆盖为"通过"，并重写explanation。

#### 规则B：监护人材料豁免
**文件**: `app/modules/flight_delay/stages/postprocess.py:155-177` → `stages/validators.py:74-183`

**触发条件（2层判定）**:

第一层（postprocess）：`capacity_check.needs_guardian = True`（被保人为未成年人）

第二层（validators）：满足以下**任一**路径即判定为"材料齐全"：
- **路径1（Vision识别）**: `vision_extract.guardian_materials.has_guardian_id = True` AND `has_relationship_proof = True`
- **路径2（关键词回退）**: 在文件名+文本+Vision结果中同时命中：
  - 监护人身份关键词：`监护人/guardian/父亲/母亲/身份证` 等
  - 监护关系关键词：`出生证/户口簿/监护关系/父子/母子` 等

**豁免效果**: 材料齐全 → 不设置"需补齐资料"，继续后续审核；不齐全 → 设置补件并return阻断。

#### 规则C：护照兜底（2种场景）
**文件**: `app/modules/flight_delay/stages/hardcheck.py:894-910`

| 场景 | 触发条件 | 推断逻辑 |
|------|---------|---------|
| C1：身份证保单+国际航班 | `is_id_card_policy=True` + `has_exit_entry_record=True` + `is_international=True` | 没有护照不可能有出入境盖章 |
| C2：证件类型为护照 | 非身份证保单 + `id_type_text` 含"护照"/"passport" | 能识别出证件类型为护照说明护照材料存在 |

**效果**: `has_passport` 置为 `True`，不加入缺失列表。

#### 规则D：扫描状态保护
**文件**: `app/modules/flight_delay/stages/hardcheck.py:914`

```python
effective_missing_required = missing_required if scanned_all_attachments else []
```

**触发条件**: `scanned_all_attachments = False`（材料未全量扫描）

**效果**: 返回空缺失列表，不输出补件要求。注释原文：`"材料未全量扫描完成，本轮不输出缺必备材料结论"`

#### 规则E：延误证明兜底（3层）
**文件**: `app/modules/flight_delay/stages/hardcheck.py:828-861`

| 层级 | 触发条件 | 效果 |
|------|---------|------|
| E1：飞常准延误证明 | `evidence.aviation_delay_proof = True`（飞常准API确认延误） | `has_delay_proof = True` |
| E2：文本关键词+航班号 | 描述文本含航班号 + 含"取消/延误/罢工"等关键词 | `has_delay_proof = True` |
| E3：改签场景 | `parsed.alternate.alt_flight_no` 存在且非unknown | `has_delay_proof = True` |

**注意**: 这3层兜底仅在 `has_delay_proof` 初始不为 `True` 时才触发。

#### 规则F：出入境记录兜底
**文件**: `app/modules/flight_delay/stages/hardcheck.py:791-801`

| 层级 | 触发条件 |
|------|---------|
| F1：国际航班+旅行证件 | `is_international=True` 或机场未知 + 护照/登机牌/身份证任一存在 |
| F2：飞常准延误证明 | `aviation_delay_proof = True` |

### 6.2 行李延误险补件兜底

#### 规则G：航司行李记录替代行李牌
**文件**: `app/modules/baggage_delay/stages/handlers.py:76-81, 99-143`

**触发条件（4个必须同时满足）**:
1. AI或Vision识别到航司官方行李记录（`has_airline_baggage_record = "true"`）
2. 无同行人（`Fellow_Travelers` 为空/none/无）
3. 行李件数 = 1（支持 "1"/"one"/"壹"/"1件"）
4. 行李记录姓名 = 被保险人姓名（大小写+空格不敏感）

**效果**: `has_baggage_tag` 置为 `True`，视同行李牌已提供。

#### 规则H：签收证明放行
**文件**: `app/modules/baggage_delay/stages/material_gate.py:85-89`

**触发条件**:
- `has_delay_proof = True` AND `has_boarding = True` AND `has_baggage_tag = True` AND `has_id = True`
- 仅 `has_receipt_proof = False`

**效果**: 不阻断流程，放行到时长计算阶段。注意：这是"不阻断"而非"豁免"——签收证明仍可能被记录为缺失，但不阻止案件继续审核。

#### 规则I：特殊材料校验（未成年人/委托代办）
**文件**: `app/modules/baggage_delay/stages/handlers.py:60-76`

| 场景 | 条件 | 缺失时补件内容 |
|------|------|--------------|
| 未成年人 | `Is_Minor = "true"` | 监护人身份证正反面、出生证或户口簿 |
| 委托代办 | `Is_Agent = "true"` | 授权委托书、受托人身份证 |

**关键词判定**: 在文本中搜索 `出生/出生证/户口簿/监护关系`（未成年人）或 `委托/授权/受托人`（委托代办）。

### 6.3 兜底规则优先级

```
优先级从高到低：
1. 硬校验覆盖AI误判（postprocess最终防线）
2. 监护人材料豁免（未成年人场景）
3. 护照兜底（hardcheck内）
4. 延误证明兜底（3层，hardcheck内）
5. 出入境记录兜底（hardcheck内）
6. 扫描状态保护（未扫完不判缺）
7. 航司行李记录替代（行李延误）
8. 签收证明放行（行李延误）
```

### 6.4 兜底规则汇总表

| 规则 | 触发条件摘要 | 影响材料 | 代码位置 |
|------|------------|---------|---------|
| A. 硬校验覆盖AI | 硬校验齐全+已全量扫描+Vision非空 | 全部材料 | postprocess.py:244-268 |
| B. 监护人材料豁免 | 未成年人+监护身份证+关系证明 | 监护人材料 | postprocess.py:155 + validators.py:74-183 |
| C1. 护照兜底-国际 | 身份证保单+国际航班+出入境记录 | 护照 | hardcheck.py:894-899 |
| C2. 护照兜底-类型 | 证件类型含"护照" | 护照 | hardcheck.py:902-908 |
| D. 扫描状态保护 | 材料未全量扫描 | 全部材料 | hardcheck.py:914 |
| E1. 延误证明-飞常准 | 飞常准API确认延误 | 延误证明 | hardcheck.py:828-832 |
| E2. 延误证明-文本 | 文本含航班号+延误关键词 | 延误证明 | hardcheck.py:834-851 |
| E3. 延误证明-改签 | 存在改签航班信息 | 延误证明 | hardcheck.py:852-859 |
| F1. 出入境-国际航班 | 国际航班+任一旅行证件 | 出入境记录 | hardcheck.py:791-796 |
| F2. 出入境-飞常准 | 飞常准延误证明确认 | 出入境记录 | hardcheck.py:798-801 |
| G. 航司行李替代 | 官方记录+无同人+1件+姓名匹配 | 托运行李牌 | handlers.py:99-143 |
| H. 签收证明放行 | 4关键材料齐全仅缺签收 | 签收证明 | material_gate.py:85-89 |

---

## 七、关键发现与建议

### 7.1 AI被推翻的根本原因

1. **AI拒赔→人工赔付（166件，占差异56.3%）**: 材料兜底规则不匹配。AI严格按材料清单要求，人工在事实清楚时放宽
2. **AI补件→人工赔付（65件，占差异22.0%）**: 登机牌兜底已生效，但AI模型仍输出补件
3. **AI赔付→人工拒赔（16件，占差异5.4%）**: `claim_info` 为空导致关键上下文缺失

### 7.2 最有问题的规则

| 规则 | 问题 | 影响 |
|------|------|------|
| ~~登机牌要求~~ | ~~延误证明+身份证明齐全时仍要求补件~~ | ~~45件被人工放宽~~ | **已修复**：当前代码严格要求登机牌，不允许兜底（hardcheck.py:890-893） |
| 监护人材料 | claim_info有Relationship字段仍要求补充 | 28件被人工放宽 |
| 延误证明 | 改签场景+飞常准确认仍要求补充 | 3件被人工放宽 | **已修复**：当前代码已有改签场景+飞常准兜底（hardcheck.py:852-859, 828-832） |

### 7.3 规则变更说明

| 变更项 | 旧状态 | 新状态 | 说明 |
|--------|--------|--------|------|
| 登机牌兜底 | 文档记录"延误证明+身份证明齐全→推断已满足" | **严格要求，不允许兜底** | hardcheck.py:890-893明确注释：延误证明+身份证明齐全不能替代登机牌，Vision提取到航班号也不能替代 |
| 延误证明兜底 | 文档记录较粗 | **3层兜底已完整实现** | E1飞常准、E2文本关键词、E3改签场景均已落地 |
| 硬校验覆盖AI误判 | 文档提到但无触发条件 | **3个精确条件** | missing_required=[] + scanned_all_attachments=True + vision_result_is_empty=False |

### 7.4 规则优化优先级

| 优先级 | 优化项 | 类型 | 影响 |
|--------|--------|------|------|
| P0 | 监护人材料简化 | 代码放宽 | 28件 |
| P0 | claim_info数据缺失修复 | 数据修复 | 8件误赔+所有无claim_info案件 |
| P0 | AI补件判定与hardcheck对齐 | 代码规范 | 45件 |
| P1 | 出入境兜底增强 | 代码放宽 | 8件 |
| ~~P1~~ | ~~延误证明兜底增强~~ | ~~代码放宽~~ | ~~3件~~ | **已完成** |
