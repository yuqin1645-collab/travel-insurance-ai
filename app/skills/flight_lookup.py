"""
航班权威数据查询 Skill
用于从第三方航班数据源获取航班状态、延误时间、原因等信息
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp

from app.logging_utils import LOGGER

# 缓存配置
_FLIGHT_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SECONDS = 300  # 5分钟缓存


class FlightLookupSkill:
    """
    航班权威数据查询技能（飞常准 VariFlight）

    输出字段：
    - planned_dep/arr: 计划起飞/到达时间
    - actual_dep/arr: 实际起飞/到达时间
    - status: 航班状态
    - delay_reason: 延误原因
    """

    def __init__(
        self,
        variflight_api_key: Optional[str] = None,
        cache_ttl: int = _CACHE_TTL_SECONDS,
    ):
        self.variflight_api_key = variflight_api_key
        self.cache_ttl = cache_ttl
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建HTTP会话（禁用SSL验证以兼容代理环境）"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session
    
    async def close(self):
        """关闭会话"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    def _make_cache_key(self, flight_no: str, date: str, dep_iata: Optional[str] = None) -> str:
        """生成缓存key"""
        key = f"{flight_no}_{date}"
        if dep_iata:
            key += f"_{dep_iata}"
        return key
    
    def _get_cached(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """获取缓存数据"""
        if cache_key in _FLIGHT_CACHE:
            cached = _FLIGHT_CACHE[cache_key]
            if cached.get("_expires_at", 0) > datetime.now().timestamp():
                return cached.get("data")
        return None
    
    def _set_cached(self, cache_key: str, data: Dict[str, Any]):
        """设置缓存"""
        _FLIGHT_CACHE[cache_key] = {
            "data": data,
            "_expires_at": datetime.now().timestamp() + self.cache_ttl,
        }
    
    async def lookup_status(
        self,
        flight_no: str,
        date: str,
        dep_iata: Optional[str] = None,
        arr_iata: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> Dict[str, Any]:
        """
        查询航班权威状态

        Args:
            flight_no: 航班号，如 "OS76", "MU5183"
            date: 日期，格式 "YYYY-MM-DD"
            dep_iata: 出发地机场三字码（可选）
            arr_iata: 目的地机场三字码（可选）

        Returns:
            Dict包含：
            - success: bool
            - planned_dep: str (ISO格式)
            - planned_arr: str (ISO格式)
            - actual_dep: str (ISO格式) 或 None
            - actual_arr: str (ISO格式) 或 None
            - status: str (起飞/到达/取消/备降/未知)
            - delay_reason: str 或 None
            - source: str 数据来源
            - queried_at: str 查询时间
            - error: str 错误信息（失败时）
        """
        # 检查缓存
        cache_key = self._make_cache_key(flight_no, date, dep_iata)
        cached = self._get_cached(cache_key)
        if cached:
            LOGGER.info(f"航班数据命中缓存: {flight_no} {date}")
            return {**cached, "from_cache": True}

        try:
            use_session = session if (session and not session.closed) else await self._get_session()

            result = await self._query_variflight_mcp(use_session, flight_no, date, dep_iata, arr_iata)
            if not result.get("success"):
                LOGGER.info(
                    f"飞常准未返回数据，降级mock: {result.get('error','')}",
                    extra={"forceid": "-", "stage": "fd_aviation_lookup", "attempt": 0},
                )
                result = await self._query_mock(flight_no, date, dep_iata, arr_iata)

            # 缓存结果
            if result.get("success"):
                self._set_cached(cache_key, result)

            return {**result, "from_cache": False}

        except Exception as e:
            LOGGER.error(f"航班查询异常: {flight_no} {date}, error={e}")
            return {
                "success": False,
                "error": str(e),
                "flight_no": flight_no,
                "date": date,
                "source": "variflight",
                "queried_at": datetime.now(timezone.utc).isoformat(),
            }
    
    async def _query_variflight_mcp(
        self,
        session: aiohttp.ClientSession,
        flight_no: str,
        date: str,
        dep_iata: Optional[str],
        arr_iata: Optional[str],
    ) -> Dict[str, Any]:
        """
        飞常准 MCP API 查询（Streamable HTTP）。
        支持历史+实时数据，返回计划/实际起降时间、航班状态、延误原因。
        接口文档：https://ai.variflight.com/servers/aviation/mcp/
        """
        if not self.variflight_api_key:
            return {"success": False, "error": "飞常准API Key未配置",
                    "source": "variflight", "queried_at": datetime.now(timezone.utc).isoformat()}

        mcp_url = f"https://ai.variflight.com/servers/aviation/mcp/?api_key={self.variflight_api_key}"
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

        # 清理机场码，过滤无效值
        _invalid = {"", "UNKNOWN", "NULL", "NONE", "-"}
        dep_clean = (dep_iata or "").strip().upper()
        arr_clean = (arr_iata or "").strip().upper()
        dep_clean = dep_clean if dep_clean not in _invalid else ""
        arr_clean = arr_clean if arr_clean not in _invalid else ""

        # 飞常准规范：国际航班必须同时传 dep + arr，否则触发 error_code:10
        # 两者都有才传；只有一个或都没有则不传（国内航班可仅用航班号+日期查询）
        # 清洗航班号：取分号/斜杠/加号/括号前的第一个有效航班号，去除空格
        import re as _re
        fnum_clean = _re.split(r'[;/+（(]', flight_no)[0].strip().upper().replace(" ", "")
        arguments: Dict[str, Any] = {"fnum": fnum_clean, "date": date}
        if dep_clean and arr_clean:
            arguments["dep"] = dep_clean
            arguments["arr"] = arr_clean

        async def _do_request(args: Dict[str, Any]) -> Dict[str, Any]:
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": "searchFlightsByNumber", "arguments": args},
            }
            try:
                async with session.post(mcp_url, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        return {"success": False,
                                "error": f"飞常准MCP HTTP {resp.status}",
                                "source": "variflight",
                                "queried_at": datetime.now(timezone.utc).isoformat()}
                    raw = await resp.text()
                    data = json.loads(raw)
            except Exception as e:
                return {"success": False, "error": f"飞常准MCP请求失败: {e}",
                        "source": "variflight", "queried_at": datetime.now(timezone.utc).isoformat()}
            try:
                content = data.get("result", {}).get("content", [])
                text_block = next((c["text"] for c in content if c.get("type") == "text"), "")
                return self._parse_variflight_response(text_block, flight_no)
            except Exception as e:
                return {"success": False, "error": f"飞常准响应解析失败: {e}",
                        "source": "variflight", "queried_at": datetime.now(timezone.utc).isoformat()}

        # 第一次尝试：带 dep/arr（若有）
        result = await _do_request(arguments)

        # error_code=10 说明 dep/arr 传错了或不匹配——降级为只用航班号+日期重查
        if (
            not result.get("success")
            and "error_code=10" in str(result.get("error", ""))
            and ("dep" in arguments or "arr" in arguments)
        ):
            arguments_no_airport = {"fnum": arguments["fnum"], "date": arguments["date"]}
            result = await _do_request(arguments_no_airport)

        return result

    def _parse_variflight_response(self, text: str, flight_no: str) -> Dict[str, Any]:
        """
        解析飞常准MCP返回的文本。
        返回格式：Flight details: {'code':200, 'data':[{...}]}
        """
        # 提取 Python dict 字符串
        prefix = "Flight details: "
        if text.startswith(prefix):
            dict_str = text[len(prefix):]
        else:
            dict_str = text

        # 用 ast.literal_eval 安全解析（飞常准返回的是Python dict格式）
        import ast
        try:
            result_dict = ast.literal_eval(dict_str)
        except Exception:
            # 降级：尝试json解析
            try:
                result_dict = json.loads(dict_str)
            except Exception as e:
                return {"success": False, "error": f"飞常准数据解析失败: {e}",
                        "source": "variflight", "queried_at": datetime.now(timezone.utc).isoformat()}

        if result_dict.get("code") != 200:
            return {"success": False,
                    "error": f"飞常准返回错误: code={result_dict.get('code')}, msg={result_dict.get('message','')}",
                    "source": "variflight", "queried_at": datetime.now(timezone.utc).isoformat()}

        flights = result_dict.get("data") or []
        # data 为 dict（含 error_code）时说明查询失败（如不支持的航班）
        if isinstance(flights, dict):
            err_code = flights.get("error_code")
            err_msg = flights.get("error", "未知错误")
            return {"success": False,
                    "error": f"飞常准查询失败: error_code={err_code}, {err_msg}",
                    "source": "variflight", "queried_at": datetime.now(timezone.utc).isoformat()}
        if not flights:
            return {"success": False, "error": f"飞常准未找到航班: {flight_no}",
                    "source": "variflight", "queried_at": datetime.now(timezone.utc).isoformat()}

        f = flights[0]

        # 时区偏移（秒）→ UTC offset字符串，用于拼接ISO时间
        def _to_iso(dt_str: str, tz_seconds: Any) -> Optional[str]:
            """将飞常准的 'YYYY-MM-DD HH:MM:SS' + 时区秒偏移 转为 ISO8601"""
            if not dt_str or dt_str.strip() in ("", "None"):
                return None
            try:
                offset_sec = int(tz_seconds or 0)
                sign = "+" if offset_sec >= 0 else "-"
                h, m = divmod(abs(offset_sec) // 60, 60)
                tz_str = f"{sign}{h:02d}:{m:02d}"
                return f"{dt_str.strip().replace(' ', 'T')}{tz_str}"
            except Exception:
                return dt_str.strip() or None

        org_tz = f.get("org_timezone", 0)
        dst_tz = f.get("dst_timezone", 0)

        planned_dep = _to_iso(f.get("FlightDeptimePlanDate", ""), org_tz)
        planned_arr = _to_iso(f.get("FlightArrtimePlanDate", ""), dst_tz)
        actual_dep  = _to_iso(f.get("FlightDeptimeDate", ""), org_tz)
        actual_arr  = _to_iso(f.get("FlightArrtimeDate", ""), dst_tz)

        # 航班状态中文标准化
        state_raw = str(f.get("FlightState") or f.get("AssistFlightState") or "").strip()
        status_map = {
            "取消": "取消", "cancel": "取消",
            "延误": "延误", "delay": "延误",
            "到达": "已到达", "landed": "已到达",
            "起飞": "飞行中", "active": "飞行中",
            "计划": "计划", "scheduled": "计划",
            "备降": "备降",
        }
        status = next((v for k, v in status_map.items() if k in state_raw.lower()), state_raw or "未知")

        # 延误原因
        delay_reason = f.get("DelayReason") or None
        if not delay_reason and status == "取消":
            delay_reason = "航班取消"

        # 多航段：同一航班号有多条记录时（中途停靠），保留所有段供AI辨认
        segments = []
        for seg in flights:
            seg_org_tz = seg.get("org_timezone", 0)
            seg_dst_tz = seg.get("dst_timezone", 0)
            segments.append({
                "dep_iata": seg.get("FlightDepcode"),
                "arr_iata": seg.get("FlightArrcode"),
                "planned_dep": _to_iso(seg.get("FlightDeptimePlanDate", ""), seg_org_tz),
                "planned_arr": _to_iso(seg.get("FlightArrtimePlanDate", ""), seg_dst_tz),
                "actual_dep": _to_iso(seg.get("FlightDeptimeDate", ""), seg_org_tz),
                "actual_arr": _to_iso(seg.get("FlightArrtimeDate", ""), seg_dst_tz),
                "status": next((v for k, v in status_map.items() if k in str(seg.get("FlightState") or "").lower()), str(seg.get("FlightState") or "未知")),
            })

        return {
            "success": True,
            "flight_no": f.get("FlightNo", flight_no),
            "operating_carrier": f.get("FlightCompany"),
            "dep_iata": f.get("FlightDepcode"),
            "arr_iata": f.get("FlightArrcode"),
            "planned_dep": planned_dep,
            "planned_arr": planned_arr,
            "actual_dep": actual_dep,
            "actual_arr": actual_arr,
            "status": status,
            "status_raw": state_raw,
            "delay_reason": delay_reason,
            "aircraft_type": f.get("ftype"),
            "on_time_rate": f.get("OntimeRate"),
            "segments": segments if len(segments) > 1 else [],
            "source": "variflight",
            "queried_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _query_mock(
        self,
        flight_no: str,
        date: str,
        dep_iata: Optional[str],
        arr_iata: Optional[str],
    ) -> Dict[str, Any]:
        """
        Mock查询（用于测试/降级）
        实际场景中应返回明确的失败或调用备用数据源
        """
        LOGGER.info(f"使用Mock数据: {flight_no} {date}")
        return {
            "success": False,
            "error": "航班数据源未配置或不可用",
            "flight_no": flight_no,
            "date": date,
            "dep_iata": dep_iata,
            "arr_iata": arr_iata,
            "source": "mock",
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "note": "请配置真实的航班数据API（如飞常准、航旅纵横）",
        }


# 单例实例
_flight_lookup_skill: Optional[FlightLookupSkill] = None


def get_flight_lookup_skill() -> FlightLookupSkill:
    """获取单例实例"""
    global _flight_lookup_skill
    import os
    # 确保 .env 已加载（flight_lookup 可能在 config 之前被调用）
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    provider = os.getenv("FLIGHT_DATA_PROVIDER", "mock")

    if _flight_lookup_skill is not None and _flight_lookup_skill.variflight_api_key != os.getenv("VARIFLIGHT_API_KEY"):
        _flight_lookup_skill = None

    if _flight_lookup_skill is None:
        _flight_lookup_skill = FlightLookupSkill(
            variflight_api_key=os.getenv("VARIFLIGHT_API_KEY"),
        )
    return _flight_lookup_skill


async def flight_lookup_status(
    flight_no: str,
    date: str,
    dep_iata: Optional[str] = None,
    arr_iata: Optional[str] = None,
) -> Dict[str, Any]:
    """
    MCP Skill: flight.lookup_status
    
    查询航班权威数据，用于：
    1. 核验材料中的航班时间是否与官方一致
    2. 获取延误原因（外部原因判定）
    3. 补充缺失的航班时间点
    """
    skill = get_flight_lookup_skill()
    return await skill.lookup_status(
        flight_no=flight_no,
        date=date,
        dep_iata=dep_iata,
        arr_iata=arr_iata,
    )


# 便捷函数：计算延误分钟数
def calculate_delay_minutes(
    planned_dep: Optional[str],
    actual_dep: Optional[str],
    planned_arr: Optional[str],
    actual_arr: Optional[str],
) -> Dict[str, Any]:
    """
    计算延误分钟数（取长原则）
    
    Returns:
        - a_minutes: 起飞口径延误分钟数
        - b_minutes: 到达口径延误分钟数
        - final_minutes: 最终延误分钟数（取长）
        - method: 计算方法说明
    """
    def parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    
    pd = parse_dt(planned_dep)
    ad = parse_dt(actual_dep)
    pa = parse_dt(planned_arr)
    aa = parse_dt(actual_arr)
    
    a_minutes = None
    b_minutes = None
    
    if pd and ad:
        delta = int((ad - pd).total_seconds() / 60)
        if delta >= 0:
            a_minutes = delta
    
    if pa and aa:
        delta = int((aa - pa).total_seconds() / 60)
        if delta >= 0:
            b_minutes = delta
    
    candidates = [m for m in [a_minutes, b_minutes] if isinstance(m, int)]
    final_minutes = max(candidates) if candidates else None
    
    return {
        "a_minutes": a_minutes,
        "b_minutes": b_minutes,
        "final_minutes": final_minutes,
        "method": "max(起飞延误, 到达延误)",
    }