"""AKShare 数据源。

AKShare 负责批量获取两类结构化数据：

* ``fund_purchase_em``：申购状态、日累计限购额度和最新净值；
* ``fund_open_fund_rank_em``：近一年收益率、净值和净值日期。

本模块只负责请求和标准化，不负责和 HTML 或历史快照合并。
"""

import math
from datetime import date, datetime
from typing import Any

from fund_monitor.fetch.sources.base import SourceRecord, empty_record, format_limit_yuan


PURCHASE_COLUMNS = {
    "基金代码", "基金简称", "最新净值/万份收益", "最新净值/万份收益-报告时间",
    "申购状态", "日累计限定金额",
}
RANK_COLUMNS = {"基金代码", "基金简称", "日期", "单位净值", "近1年"}


def fetch_purchase_snapshot() -> dict[str, SourceRecord]:
    """批量获取申购状态、限购额度和最新净值。"""
    ak = _load_akshare()
    df = ak.fund_purchase_em()
    _validate_frame(df, PURCHASE_COLUMNS, "fund_purchase_em")

    result: dict[str, SourceRecord] = {}
    for _, row in df.iterrows():
        code = _code(row.get("基金代码"))
        if not code:
            continue
        rec = empty_record(code)
        rec.update({
            "name": _text(row.get("基金简称")),
            "nav": _number(row.get("最新净值/万份收益")),
            "nav_date": _date_text(row.get("最新净值/万份收益-报告时间")),
            "purchase_status": _text(row.get("申购状态")) or "未知",
            "purchase_limit": format_limit_yuan(row.get("日累计限定金额")),
            "error": None,
        })
        result[code] = rec

    if not result:
        raise ValueError("fund_purchase_em 返回空数据")
    return result


def fetch_rank_snapshot() -> dict[str, SourceRecord]:
    """批量获取近一年收益率、净值和净值日期。"""
    ak = _load_akshare()
    df = ak.fund_open_fund_rank_em(symbol="全部")
    _validate_frame(df, RANK_COLUMNS, "fund_open_fund_rank_em")

    result: dict[str, SourceRecord] = {}
    for _, row in df.iterrows():
        code = _code(row.get("基金代码"))
        if not code:
            continue
        rec = empty_record(code)
        rec.update({
            "name": _text(row.get("基金简称")),
            "nav": _number(row.get("单位净值")),
            "nav_date": _date_text(row.get("日期")),
            "return_1y": _percent(row.get("近1年")),
            "error": None,
        })
        result[code] = rec

    if not result:
        raise ValueError("fund_open_fund_rank_em 返回空数据")
    return result


def _load_akshare():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("未安装 AKShare，请执行 pip install -r requirements.txt") from exc
    return ak


def _validate_frame(df: Any, required: set[str], name: str) -> None:
    if df is None or getattr(df, "empty", True):
        raise ValueError(f"{name} 返回空数据")
    columns = set(getattr(df, "columns", []))
    missing = sorted(required - columns)
    if missing:
        raise ValueError(f"{name} 缺少字段: {', '.join(missing)}")


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return str(value).strip() in {"", "nan", "NaN", "NaT", "--", "-"}


def _text(value: Any) -> str:
    return "" if _missing(value) else str(value).strip()


def _code(value: Any) -> str:
    if _missing(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6)


def _number(value: Any):
    if _missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent(value: Any) -> str:
    number = _number(value)
    return "" if number is None else f"{number:g}%"


def _date_text(value: Any) -> str:
    if _missing(value):
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()
