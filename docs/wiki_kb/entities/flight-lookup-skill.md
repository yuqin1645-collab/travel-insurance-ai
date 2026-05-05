---
title: 飞常准航班查询技能
created: 2026-05-05
updated: 2026-05-05
type: entity
tags: [flight-delay, skills, aviation]
sources: [app/skills/flight_lookup.py]
confidence: high
---

# 飞常准航班查询技能 (FlightLookupSkill)

## 概述
`FlightLookupSkill` 是航班延误审核流程的核心数据源技能，通过飞常准（VariFlight）MCP API 获取航班权威状态数据，包括计划/实际起降时间、航班状态、延误原因等。

## 位置
- **文件**: `app/skills/flight_lookup.py`
- **类**: `FlightLookupSkill`
- **单例获取**: `get_flight_lookup_skill()`

## 数据源

### 主数据源：飞常准 VariFlight MCP API
- **接口**: `https://ai.variflight.com/servers/aviation/mcp/`
- **方法**: `searchFlightsByNumber`（JSON-RPC 2.0）
- **认证**: API Key（环境变量 `VARIFLIGHT_API_KEY`）
- **协议**: Streamable HTTP（支持 SSE）

### 降级策略
飞常准未返回数据时 → 降级 mock（返回 `success: False`）

## 查询接口

```python
async def lookup_status(
    flight_no: str,      # 航班号，如 "OS76", "MU5183"
    date: str,           # 日期 "YYYY-MM-DD"
    dep_iata: Optional[str] = None,  # 出发机场三字码
    arr_iata: Optional[str] = None,  # 到达机场三字码
) -> Dict[str, Any]
```

## 输出字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | bool | 查询是否成功 |
| `flight_no` | str | 航班号 |
| `operating_carrier` | str | 承运航司 |
| `dep_iata` / `arr_iata` | str | 出发/到达机场三字码 |
| `planned_dep` / `planned_arr` | str (ISO) | 计划起飞/到达时间 |
| `actual_dep` / `actual_arr` | str (ISO) | 实际起飞/到达时间 |
| `status` | str | 航班状态（取消/延误/已到达/飞行中/计划/备降/未知） |
| `delay_reason` | str | 延误原因 |
| `aircraft_type` | str | 机型 |
| `on_time_rate` | str | 准点率 |
| `segments` | list | 多航段数据（中途停靠时） |
| `source` | str | 数据来源（"variflight"） |

## 缓存机制

- **TTL**: 300 秒（5分钟）
- **缓存 Key**: `{flight_no}_{date}_{dep_iata}`
- **实现**: 模块级 `_FLIGHT_CACHE` 字典

## 关键实现细节

### 航班号清洗
```python
# 取分号/斜杠/加号/括号前的第一个有效航班号
fnum_clean = re.split(r'[;/+（(]', flight_no)[0].strip().upper().replace(" ", "")
```

### 机场码校验
- 国际航班必须同时传 dep + arr，否则触发 `error_code:10`
- 无效值过滤：`""`, `"UNKNOWN"`, `"NULL"`, `"NONE"`, `"-"`
- error_code=10 时自动降级为只用航班号+日期重查

### 时区处理
- 飞常准返回 `org_timezone` / `dst_timezone`（秒偏移）
- 转换为 ISO8601 格式：`YYYY-MM-DDTHH:MM:SS+HH:MM`

### 状态标准化
```python
status_map = {
    "取消": "取消", "延误": "延误",
    "到达": "已到达", "起飞": "飞行中",
    "计划": "计划", "备降": "备降",
}
```

## 在 Pipeline 中的使用

在 `flight_delay/pipeline.py` 的 stage1.3 中调用：

1. 从 Vision 抽取结果和 AI 解析结果中收集候选航班号
2. 按优先级遍历候选：claim_focus.flight_no → ticket_flight_no → all_flights_found
3. 对每个候选调用 `lookup_status()`
4. 路线匹配（dep/arr iata 一致）时终止遍历
5. 成功结果通过 `_merge_aviation_into_parsed()` 合并到 parsed

## 环境变量

| 变量 | 说明 |
|------|------|
| `VARIFLIGHT_API_KEY` | 飞常准 API Key |
| `FLIGHT_DATA_PROVIDER` | 数据源选择（默认 "mock"） |

## 相关页面
- [[flight-delay-module]] — 航班延误模块
- [[flight-delay-compensation]] — 航班延误赔付规则
- [[pipeline-architecture]] — Pipeline 架构
