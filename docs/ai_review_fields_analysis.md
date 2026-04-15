# AI审核输出文件字段说明

## 概述

该JSON文件包含了航班延误理赔审核的完整处理结果。为了排查问题，文件保留了多个处理阶段的中间数据，导致内容较长且存在重复。

---

## 字段层级结构

### 第一层：核心输出字段

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `forceid` | string | 理赔案件ID |
| `claim_type` | string | 理赔类型，值为 `flight_delay` |
| `Remark` | string | 审核结论说明（**重复**：与 `KeyConclusions[0].Remark`、`flight_delay_audit.explanation` 内容相同） |
| `IsAdditional` | string | 是否需要补齐资料，`Y`=是，`N`=否 |
| `KeyConclusions` | array | 审核结论数组，每个元素包含 checkpoint、Eligible、Remark |
| `flight_delay_audit` | object | **核心审核结果**（你需要的最终输出） |
| `DebugInfo` | object | 调试信息，包含各处理阶段的中间结果 |

---

### 核心审核结果 (`flight_delay_audit`)

这是你需要输出的核心内容：

```json
{
  "audit_result": "需补齐资料",        // 审核结果：通过/拒绝/需补齐资料
  "confidence_score": 0.45,            // 置信度分数 (0-1)
  "key_data": {
    "passenger_name": "唐桐诏",        // 乘客姓名
    "delay_duration_minutes": 0,       // 延误时长（分钟）
    "reason": "原因未知"               // 延误原因
  },
  "logic_check": {
    "identity_match": true,            // 身份信息是否匹配
    "threshold_met": false,            // 是否达到起赔阈值
    "exclusion_triggered": false       // 是否触发免责条款
  },
  "payout_suggestion": {
    "currency": "CNY",                 // 币种
    "amount": 0,                       // 建议赔付金额
    "basis": "起赔4小时..."            // 计算依据说明
  },
  "explanation": "按审核优先级结论..." // 详细解释说明
}
```

---

## DebugInfo 处理阶段说明

`DebugInfo` 包含以下处理阶段的中间结果：

### 1. `flight_delay_vision_extract` - 图像OCR提取
从上传的图片材料中提取原始信息：
- `flight`: 航班基本信息（航班号、承运人）
- `route`: 航线（出发/到达机场IATA代码）
- `schedule_local`: 计划时间（本地时区）
- `actual_local`: 实际时间（本地时区）
- `alternate_local`: 替代交通时间
- `delay_reason`: 延误原因
- `evidence`: 材料完整性检查结果

### 2. `flight_delay_parse` - 数据解析
对OCR结果进行结构化解析：
- `policy_hint`: 保单信息（保险公司、保单号、有效期）
- `passenger`: 乘客身份信息
- `flight`: 航班详细信息
- `route`: 航线信息
- `schedule_local`: 计划起降时间
- `actual_local`: 实际起降时间
- `alternate_local`: 替代交通信息
- `utc`: UTC时间（用于跨时区计算）
- `evidence`: 材料完整性
- `extraction_notes`: 提取过程备注

### 3. `flight_delay_aviation_lookup` - 航旅数据查询
从航旅数据库查询的航班实时信息：
- `success`: 查询是否成功
- `flight_no`: 航班号
- `operating_carrier`: 承运人
- `dep_iata`/`arr_iata`: 出发/到达机场
- `planned_dep`/`planned_arr`: 计划起降时间
- `actual_dep`/`actual_arr`: 实际起降时间
- `status`: 航班状态（已到达/取消/延误等）
- `delay_reason`: 延误原因
- `aircraft_type`: 机型
- `source`: 数据来源

### 4. `flight_delay_parse_enriched` - 增强解析
将OCR数据与航旅查询结果合并，并计算延误时长：
- 包含 `flight_delay_parse` 的所有字段
- `computed_delay`: 延误时长计算结果
  - `a_minutes`: 按起飞时间计算的延误分钟
  - `b_minutes`: 按到达时间计算的延误分钟
  - `final_minutes`: 最终延误时长（取长原则）
  - `method`: 计算方法说明
  - `threshold_minutes`: 起赔阈值（分钟）
  - `threshold_met`: 是否达到阈值

### 5. `flight_delay_hardcheck` - 硬性规则检查
执行各项规则校验：
- `dep_airport`/`arr_airport`: 机场信息（国家、时区、城市）
- `transit_check`: 中转检查
- `war_risk`: 战争风险检查
- `policy_window`: 保单有效期检查
- `coverage_area`: 承保区域检查
- `passenger_civil_check`: 民航客运检查
- `missed_connection_check`: 联程误机检查
- `required_materials_check`: 必备材料检查
- `fraud_foreseeability_check`: 欺诈风险检查

### 6. `flight_delay_payout` - 赔付金额计算
- `status`: 计算状态
- `note`: 备注
- `final_amount`: 最终赔付金额

### 7. `flight_delay_audit` - 审核结论（处理阶段版本）
与外层的 `flight_delay_audit` 内容相同，这是生成过程中的中间结果。

### 8. `flight_delay_audit_post` - 后处理审核结论
与 `flight_delay_audit` 内容相同，可能是后处理步骤的输出。

---

## 重复内容分析

以下字段内容**完全相同**（长文本重复了4次）：

| 字段路径 | 内容 |
|----------|------|
| `Remark` | 审核结论说明 |
| `KeyConclusions[0].Remark` | 审核结论说明 |
| `flight_delay_audit.explanation` | 审核结论说明 |
| `DebugInfo.flight_delay_audit.explanation` | 审核结论说明 |
| `DebugInfo.flight_delay_audit_post.explanation` | 审核结论说明 |

**优化建议**：如果只需要输出核心审核结果，可以删除 `DebugInfo` 或将其简化。

---

## 推荐的最小输出结构

如果只需返回核心审核结果，输出结构可简化为：

```json
{
  "forceid": "a0nC800000Hd733IAB",
  "claim_type": "flight_delay",
  "audit_result": "需补齐资料",
  "confidence_score": 0.45,
  "key_data": {
    "passenger_name": "唐桐诏",
    "delay_duration_minutes": 0,
    "reason": "原因未知（申请称取消；航旅查询显示已到达）"
  },
  "logic_check": {
    "identity_match": true,
    "threshold_met": false,
    "exclusion_triggered": false
  },
  "payout_suggestion": {
    "currency": "CNY",
    "amount": 0,
    "basis": "起赔4小时；需按"取长原则"用承运人/行程可核验时间计算，目前无法证实≥4小时"
  },
  "explanation": "按审核优先级结论：..."
}
```

---

## 字段依赖关系图

```
vision_extract (图像提取)
       ↓
parse (结构化解析)
       ↓
aviation_lookup (航旅查询) ←──┐
       ↓                      │
parse_enriched (增强解析) ────┘
       ↓
hardcheck (规则检查)
       ↓
payout (金额计算)
       ↓
audit (审核结论)
       ↓
audit_post (后处理，可选)
```

---

## 本次案例的关键问题总结

1. **时间数据矛盾**：申请称航班取消，但航旅查询显示已到达，延误仅70分钟
2. **材料不完整**：机场证明无盖章/签字，无登机牌/行程单
3. **替代交通不明确**：火车票日期与航班日期不符
4. **无法验证延误时长**：无法证实达到4小时起赔阈值