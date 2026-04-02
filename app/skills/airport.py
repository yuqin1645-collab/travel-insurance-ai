"""
Skill B: airport.resolve_country
机场三字码解析 - 国家、时区、是否境内
用于：境内中转免责判定 / 服务区域校验 / UTC时区换算
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.logging_utils import LOGGER, log_extra

# 内置机场数据库（常用机场，覆盖主要理赔场景）
# 格式：iata -> {country_code, country_name, timezone, city}
_AIRPORT_DB: Dict[str, Dict[str, str]] = {
    # 中国大陆
    "PEK": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "北京"},
    "PKX": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "北京"},
    "PVG": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "上海"},
    "SHA": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "上海"},
    "CAN": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "广州"},
    "SZX": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "深圳"},
    "CTU": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "成都"},
    "TFU": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "成都"},
    "KMG": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "昆明"},
    "XIY": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "西安"},
    "WUH": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "武汉"},
    "CSX": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "长沙"},
    "CKG": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "重庆"},
    "HGH": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "杭州"},
    "NKG": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "南京"},
    "TAO": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "青岛"},
    "XMN": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "厦门"},
    "FOC": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "福州"},
    "TSN": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "天津"},
    "SYX": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "三亚"},
    "HAK": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "海口"},
    "DLC": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "大连"},
    "SHE": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "沈阳"},
    "HRB": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "哈尔滨"},
    "CGQ": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "长春"},
    "TYN": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "太原"},
    "HET": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "呼和浩特"},
    "NNG": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "南宁"},
    "URC": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Urumqi", "city": "乌鲁木齐"},
    "LXA": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "拉萨"},
    "ZUH": {"country_code": "CN", "country_name": "中国", "timezone": "Asia/Shanghai", "city": "珠海"},
    "MFM": {"country_code": "MO", "country_name": "澳门", "timezone": "Asia/Macau", "city": "澳门"},
    "HKG": {"country_code": "HK", "country_name": "香港", "timezone": "Asia/Hong_Kong", "city": "香港"},

    # 日本
    "NRT": {"country_code": "JP", "country_name": "日本", "timezone": "Asia/Tokyo", "city": "东京"},
    "HND": {"country_code": "JP", "country_name": "日本", "timezone": "Asia/Tokyo", "city": "东京"},
    "KIX": {"country_code": "JP", "country_name": "日本", "timezone": "Asia/Tokyo", "city": "大阪"},
    "ITM": {"country_code": "JP", "country_name": "日本", "timezone": "Asia/Tokyo", "city": "大阪"},
    "NGO": {"country_code": "JP", "country_name": "日本", "timezone": "Asia/Tokyo", "city": "名古屋"},
    "CTS": {"country_code": "JP", "country_name": "日本", "timezone": "Asia/Tokyo", "city": "札幌"},
    "FUK": {"country_code": "JP", "country_name": "日本", "timezone": "Asia/Tokyo", "city": "福冈"},

    # 韩国
    "ICN": {"country_code": "KR", "country_name": "韩国", "timezone": "Asia/Seoul", "city": "首尔"},
    "GMP": {"country_code": "KR", "country_name": "韩国", "timezone": "Asia/Seoul", "city": "首尔"},
    "PUS": {"country_code": "KR", "country_name": "韩国", "timezone": "Asia/Seoul", "city": "釜山"},

    # 东南亚
    "BKK": {"country_code": "TH", "country_name": "泰国", "timezone": "Asia/Bangkok", "city": "曼谷"},
    "DMK": {"country_code": "TH", "country_name": "泰国", "timezone": "Asia/Bangkok", "city": "曼谷"},
    "HKT": {"country_code": "TH", "country_name": "泰国", "timezone": "Asia/Bangkok", "city": "普吉岛"},
    "CNX": {"country_code": "TH", "country_name": "泰国", "timezone": "Asia/Bangkok", "city": "清迈"},
    "SIN": {"country_code": "SG", "country_name": "新加坡", "timezone": "Asia/Singapore", "city": "新加坡"},
    "KUL": {"country_code": "MY", "country_name": "马来西亚", "timezone": "Asia/Kuala_Lumpur", "city": "吉隆坡"},
    "KCH": {"country_code": "MY", "country_name": "马来西亚", "timezone": "Asia/Kuching", "city": "古晋"},
    "CGK": {"country_code": "ID", "country_name": "印度尼西亚", "timezone": "Asia/Jakarta", "city": "雅加达"},
    "DPS": {"country_code": "ID", "country_name": "印度尼西亚", "timezone": "Asia/Makassar", "city": "巴厘岛"},
    "MNL": {"country_code": "PH", "country_name": "菲律宾", "timezone": "Asia/Manila", "city": "马尼拉"},
    "CEB": {"country_code": "PH", "country_name": "菲律宾", "timezone": "Asia/Manila", "city": "宿务"},
    "HAN": {"country_code": "VN", "country_name": "越南", "timezone": "Asia/Ho_Chi_Minh", "city": "河内"},
    "SGN": {"country_code": "VN", "country_name": "越南", "timezone": "Asia/Ho_Chi_Minh", "city": "胡志明市"},
    "DAD": {"country_code": "VN", "country_name": "越南", "timezone": "Asia/Ho_Chi_Minh", "city": "岘港"},
    "REP": {"country_code": "KH", "country_name": "柬埔寨", "timezone": "Asia/Phnom_Penh", "city": "暹粒"},
    "PNH": {"country_code": "KH", "country_name": "柬埔寨", "timezone": "Asia/Phnom_Penh", "city": "金边"},
    "RGN": {"country_code": "MM", "country_name": "缅甸", "timezone": "Asia/Rangoon", "city": "仰光"},

    # 南亚
    "DEL": {"country_code": "IN", "country_name": "印度", "timezone": "Asia/Kolkata", "city": "德里"},
    "BOM": {"country_code": "IN", "country_name": "印度", "timezone": "Asia/Kolkata", "city": "孟买"},
    "MAA": {"country_code": "IN", "country_name": "印度", "timezone": "Asia/Kolkata", "city": "钦奈"},
    "CMB": {"country_code": "LK", "country_name": "斯里兰卡", "timezone": "Asia/Colombo", "city": "科伦坡"},
    "KTM": {"country_code": "NP", "country_name": "尼泊尔", "timezone": "Asia/Kathmandu", "city": "加德满都"},

    # 中东
    "DXB": {"country_code": "AE", "country_name": "阿联酋", "timezone": "Asia/Dubai", "city": "迪拜"},
    "AUH": {"country_code": "AE", "country_name": "阿联酋", "timezone": "Asia/Dubai", "city": "阿布扎比"},
    "DOH": {"country_code": "QA", "country_name": "卡塔尔", "timezone": "Asia/Qatar", "city": "多哈"},
    "KWI": {"country_code": "KW", "country_name": "科威特", "timezone": "Asia/Kuwait", "city": "科威特城"},
    "BAH": {"country_code": "BH", "country_name": "巴林", "timezone": "Asia/Bahrain", "city": "麦纳麦"},
    "MCT": {"country_code": "OM", "country_name": "阿曼", "timezone": "Asia/Muscat", "city": "马斯喀特"},
    "SLL": {"country_code": "OM", "country_name": "阿曼", "timezone": "Asia/Muscat", "city": "萨拉拉"},
    "AMM": {"country_code": "JO", "country_name": "约旦", "timezone": "Asia/Amman", "city": "安曼"},
    "AQJ": {"country_code": "JO", "country_name": "约旦", "timezone": "Asia/Amman", "city": "亚喀巴"},
    "RUH": {"country_code": "SA", "country_name": "沙特阿拉伯", "timezone": "Asia/Riyadh", "city": "利雅得"},
    "JED": {"country_code": "SA", "country_name": "沙特阿拉伯", "timezone": "Asia/Riyadh", "city": "吉达"},
    "DMM": {"country_code": "SA", "country_name": "沙特阿拉伯", "timezone": "Asia/Riyadh", "city": "达曼"},
    "MED": {"country_code": "SA", "country_name": "沙特阿拉伯", "timezone": "Asia/Riyadh", "city": "麦地那"},
    "TLV": {"country_code": "IL", "country_name": "以色列", "timezone": "Asia/Jerusalem", "city": "特拉维夫"},
    "IST": {"country_code": "TR", "country_name": "土耳其", "timezone": "Europe/Istanbul", "city": "伊斯坦布尔"},
    "SAW": {"country_code": "TR", "country_name": "土耳其", "timezone": "Europe/Istanbul", "city": "伊斯坦布尔"},

    # 欧洲
    "LHR": {"country_code": "GB", "country_name": "英国", "timezone": "Europe/London", "city": "伦敦"},
    "LGW": {"country_code": "GB", "country_name": "英国", "timezone": "Europe/London", "city": "伦敦"},
    "CDG": {"country_code": "FR", "country_name": "法国", "timezone": "Europe/Paris", "city": "巴黎"},
    "ORY": {"country_code": "FR", "country_name": "法国", "timezone": "Europe/Paris", "city": "巴黎"},
    "FRA": {"country_code": "DE", "country_name": "德国", "timezone": "Europe/Berlin", "city": "法兰克福"},
    "MUC": {"country_code": "DE", "country_name": "德国", "timezone": "Europe/Berlin", "city": "慕尼黑"},
    "BER": {"country_code": "DE", "country_name": "德国", "timezone": "Europe/Berlin", "city": "柏林"},
    "AMS": {"country_code": "NL", "country_name": "荷兰", "timezone": "Europe/Amsterdam", "city": "阿姆斯特丹"},
    "ZRH": {"country_code": "CH", "country_name": "瑞士", "timezone": "Europe/Zurich", "city": "苏黎世"},
    "GVA": {"country_code": "CH", "country_name": "瑞士", "timezone": "Europe/Zurich", "city": "日内瓦"},
    "VIE": {"country_code": "AT", "country_name": "奥地利", "timezone": "Europe/Vienna", "city": "维也纳"},
    "FCO": {"country_code": "IT", "country_name": "意大利", "timezone": "Europe/Rome", "city": "罗马"},
    "MXP": {"country_code": "IT", "country_name": "意大利", "timezone": "Europe/Rome", "city": "米兰"},
    "MAD": {"country_code": "ES", "country_name": "西班牙", "timezone": "Europe/Madrid", "city": "马德里"},
    "BCN": {"country_code": "ES", "country_name": "西班牙", "timezone": "Europe/Madrid", "city": "巴塞罗那"},
    "LIS": {"country_code": "PT", "country_name": "葡萄牙", "timezone": "Europe/Lisbon", "city": "里斯本"},
    "CPH": {"country_code": "DK", "country_name": "���麦", "timezone": "Europe/Copenhagen", "city": "哥本哈根"},
    "ARN": {"country_code": "SE", "country_name": "瑞典", "timezone": "Europe/Stockholm", "city": "斯德哥尔摩"},
    "OSL": {"country_code": "NO", "country_name": "挪威", "timezone": "Europe/Oslo", "city": "奥斯陆"},
    "HEL": {"country_code": "FI", "country_name": "芬兰", "timezone": "Europe/Helsinki", "city": "赫尔辛基"},
    "WAW": {"country_code": "PL", "country_name": "波兰", "timezone": "Europe/Warsaw", "city": "华沙"},
    "PRG": {"country_code": "CZ", "country_name": "捷克", "timezone": "Europe/Prague", "city": "布拉格"},
    "BUD": {"country_code": "HU", "country_name": "匈牙利", "timezone": "Europe/Budapest", "city": "布达佩斯"},
    "ATH": {"country_code": "GR", "country_name": "希腊", "timezone": "Europe/Athens", "city": "雅典"},
    "SVO": {"country_code": "RU", "country_name": "俄罗斯", "timezone": "Europe/Moscow", "city": "莫斯科"},
    "DME": {"country_code": "RU", "country_name": "俄罗斯", "timezone": "Europe/Moscow", "city": "莫斯科"},
    "VKO": {"country_code": "RU", "country_name": "俄罗斯", "timezone": "Europe/Moscow", "city": "莫斯科"},
    "LED": {"country_code": "RU", "country_name": "俄罗斯", "timezone": "Europe/Moscow", "city": "圣彼得堡"},

    # 北美
    "JFK": {"country_code": "US", "country_name": "美国", "timezone": "America/New_York", "city": "纽约"},
    "EWR": {"country_code": "US", "country_name": "美国", "timezone": "America/New_York", "city": "纽约"},
    "LGA": {"country_code": "US", "country_name": "美国", "timezone": "America/New_York", "city": "纽约"},
    "LAX": {"country_code": "US", "country_name": "美国", "timezone": "America/Los_Angeles", "city": "洛杉矶"},
    "SFO": {"country_code": "US", "country_name": "美国", "timezone": "America/Los_Angeles", "city": "旧金山"},
    "SEA": {"country_code": "US", "country_name": "美国", "timezone": "America/Los_Angeles", "city": "西雅图"},
    "ORD": {"country_code": "US", "country_name": "美国", "timezone": "America/Chicago", "city": "芝加哥"},
    "MDW": {"country_code": "US", "country_name": "美国", "timezone": "America/Chicago", "city": "芝加哥"},
    "DFW": {"country_code": "US", "country_name": "美国", "timezone": "America/Chicago", "city": "达拉斯"},
    "MIA": {"country_code": "US", "country_name": "美国", "timezone": "America/New_York", "city": "迈阿密"},
    "BOS": {"country_code": "US", "country_name": "美国", "timezone": "America/New_York", "city": "波士顿"},
    "ATL": {"country_code": "US", "country_name": "美国", "timezone": "America/New_York", "city": "亚特兰大"},
    "DEN": {"country_code": "US", "country_name": "美国", "timezone": "America/Denver", "city": "丹佛"},
    "PHX": {"country_code": "US", "country_name": "美国", "timezone": "America/Phoenix", "city": "凤凰城"},
    "HNL": {"country_code": "US", "country_name": "美国", "timezone": "Pacific/Honolulu", "city": "檀香山"},
    "YVR": {"country_code": "CA", "country_name": "加拿大", "timezone": "America/Vancouver", "city": "温哥华"},
    "YYZ": {"country_code": "CA", "country_name": "加拿大", "timezone": "America/Toronto", "city": "多伦多"},
    "YUL": {"country_code": "CA", "country_name": "加拿大", "timezone": "America/Toronto", "city": "蒙特利尔"},
    "YYC": {"country_code": "CA", "country_name": "加拿大", "timezone": "America/Edmonton", "city": "卡尔加里"},

    # 大洋洲
    "SYD": {"country_code": "AU", "country_name": "澳大利亚", "timezone": "Australia/Sydney", "city": "悉尼"},
    "MEL": {"country_code": "AU", "country_name": "澳大利亚", "timezone": "Australia/Melbourne", "city": "墨尔本"},
    "BNE": {"country_code": "AU", "country_name": "澳大利亚", "timezone": "Australia/Brisbane", "city": "布里斯班"},
    "PER": {"country_code": "AU", "country_name": "澳大利亚", "timezone": "Australia/Perth", "city": "珀斯"},
    "AKL": {"country_code": "NZ", "country_name": "新西兰", "timezone": "Pacific/Auckland", "city": "奥克兰"},
    "WLG": {"country_code": "NZ", "country_name": "新西兰", "timezone": "Pacific/Auckland", "city": "惠灵顿"},
    "CHC": {"country_code": "NZ", "country_name": "新西兰", "timezone": "Pacific/Auckland", "city": "基督城"},

    # 南美
    "GRU": {"country_code": "BR", "country_name": "巴西", "timezone": "America/Sao_Paulo", "city": "圣保罗"},
    "EZE": {"country_code": "AR", "country_name": "阿根廷", "timezone": "America/Argentina/Buenos_Aires", "city": "布宜诺斯艾利斯"},

    # 非洲
    "JNB": {"country_code": "ZA", "country_name": "南非", "timezone": "Africa/Johannesburg", "city": "约翰内斯堡"},
    "CAI": {"country_code": "EG", "country_name": "埃及", "timezone": "Africa/Cairo", "city": "开罗"},
    "NBO": {"country_code": "KE", "country_name": "肯尼亚", "timezone": "Africa/Nairobi", "city": "内罗毕"},

    # 台湾
    "TPE": {"country_code": "TW", "country_name": "台湾", "timezone": "Asia/Taipei", "city": "台北"},
    "KHH": {"country_code": "TW", "country_name": "台湾", "timezone": "Asia/Taipei", "city": "高雄"},
    "RMQ": {"country_code": "TW", "country_name": "台湾", "timezone": "Asia/Taipei", "city": "台中"},

    # 维也纳/欧洲其他
    "BRU": {"country_code": "BE", "country_name": "比利时", "timezone": "Europe/Brussels", "city": "布鲁塞尔"},
    "DUB": {"country_code": "IE", "country_name": "爱尔兰", "timezone": "Europe/Dublin", "city": "都柏林"},
    "ZAG": {"country_code": "HR", "country_name": "克罗地亚", "timezone": "Europe/Zagreb", "city": "萨格勒布"},

    # 中亚
    "ALA": {"country_code": "KZ", "country_name": "哈萨克斯坦", "timezone": "Asia/Almaty", "city": "阿拉木图"},
    "TAS": {"country_code": "UZ", "country_name": "乌兹别克斯坦", "timezone": "Asia/Tashkent", "city": "塔什干"},
}

# 境内（中国大陆 + 港澳特殊行政区）country_code 集合
# 注意：港澳依照条款通常视为"境外"，请结合业务口径调整
_DOMESTIC_CN_CODES = {"CN"}
_CN_SAR_CODES = {"HK", "MO"}  # 港澳 - 依业务口径决定是否纳入境内


def resolve_country(iata: str, treat_sar_as_domestic: bool = False) -> Dict[str, Any]:
    """
    Skill B: airport.resolve_country

    Args:
        iata: 机场三字码（大写），如 "PEK", "NRT"
        treat_sar_as_domestic: 是否将港澳特区视为境内（默认 False，按出境口径）

    Returns:
        {
            "iata": str,
            "country_code": str,
            "country_name": str,
            "timezone": str,
            "city": str,
            "is_domestic_cn": bool,  # 是否境内（中国大陆，视配置是否含港澳）
            "found": bool,
        }
    """
    if not iata:
        return _unknown_result(iata or "")

    iata_upper = str(iata).strip().upper()
    if iata_upper in ("UNKNOWN", "NULL", "NONE", "-", ""):
        return _unknown_result(iata_upper)
    data = _AIRPORT_DB.get(iata_upper)
    if not data:
        LOGGER.warning(f"[airport.resolve_country] 未知机场三字码: {iata_upper}", extra=log_extra(stage="airport", attempt=0))
        return _unknown_result(iata_upper)

    cc = data["country_code"]
    is_domestic = cc in _DOMESTIC_CN_CODES
    if treat_sar_as_domestic:
        is_domestic = is_domestic or (cc in _CN_SAR_CODES)

    return {
        "iata": iata_upper,
        "country_code": cc,
        "country_name": data["country_name"],
        "timezone": data["timezone"],
        "city": data.get("city", ""),
        "is_domestic_cn": is_domestic,
        "found": True,
    }


def _unknown_result(iata: str) -> Dict[str, Any]:
    return {
        "iata": iata,
        "country_code": "unknown",
        "country_name": "unknown",
        "timezone": "unknown",
        "city": "unknown",
        "is_domestic_cn": None,
        "found": False,
    }


def check_transit_domestic(transit_iata: str) -> Dict[str, Any]:
    """
    检查中转地是否在境内（用于联程/中转免责判定）。
    返回：{is_domestic_cn, country_code, country_name, iata, found}
    """
    result = resolve_country(transit_iata)
    return {
        "iata": result["iata"],
        "country_code": result["country_code"],
        "country_name": result["country_name"],
        "is_domestic_cn": result["is_domestic_cn"],
        "found": result["found"],
    }
