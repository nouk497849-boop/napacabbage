from __future__ import annotations


AIRLINE_LABELS = {
    "5J": "宿霧太平洋航空",
    "7C": "濟州航空",
    "AA": "美國航空",
    "AC": "加拿大航空",
    "AF": "法國航空",
    "AK": "亞洲航空",
    "BA": "英國航空",
    "BR": "長榮航空",
    "CA": "中國國際航空",
    "CI": "中華航空",
    "CX": "國泰航空",
    "CZ": "中國南方航空",
    "D7": "亞洲航空長程",
    "DL": "達美航空",
    "EK": "阿聯酋航空",
    "FD": "泰國亞洲航空",
    "GA": "印尼鷹航",
    "GK": "捷星日本航空",
    "HX": "香港航空",
    "IT": "台灣虎航",
    "JL": "日本航空",
    "JX": "星宇航空",
    "KE": "大韓航空",
    "KL": "荷蘭皇家航空",
    "LH": "漢莎航空",
    "LJ": "真航空",
    "MF": "廈門航空",
    "MH": "馬來西亞航空",
    "MM": "樂桃航空",
    "MU": "中國東方航空",
    "NH": "全日空",
    "OD": "峇迪航空",
    "OZ": "韓亞航空",
    "PR": "菲律賓航空",
    "QH": "越竹航空",
    "QR": "卡達航空",
    "QZ": "印尼亞洲航空",
    "SL": "泰國獅子航空",
    "SQ": "新加坡航空",
    "TG": "泰國航空",
    "TK": "土耳其航空",
    "TR": "酷航",
    "TW": "德威航空",
    "UA": "聯合航空",
    "UO": "香港快運",
    "VJ": "越捷航空",
    "VN": "越南航空",
    "ZE": "易斯達航空",
}


def airline_label(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    parts = [part.strip() for part in raw.replace("/", ",").split(",") if part.strip()]
    if len(parts) > 1:
        return "、".join(airline_label(part) or part for part in parts)
    code = _airline_code(raw)
    label = AIRLINE_LABELS.get(code)
    if label:
        return f"{label} ({code})"
    return raw


def _airline_code(value: str) -> str:
    token = value.strip().split()[0].upper()
    return token[:2]
