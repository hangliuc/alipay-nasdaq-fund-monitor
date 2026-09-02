"""
数据源统一约定

所有 source 模块返回的单条记录字段：
- code              基金代码
- name              基金简称（可空，由调用方兜底）
- purchase_status   申购状态："开放申购" / "限大额" / "暂停申购" / "未知"
- purchase_limit    具体限额："不限" / "暂停" / "X元" / "X万元" / "未知"
- nav               最新净值，float 或 None
- nav_date          净值报告时间，"YYYY-MM-DD" 或 "MM-DD"，可空
- error             失败原因，None 表示成功

近1年收益率（return_1y）由 AKShare 和 HTML 源共同提供。
"""

from typing import Optional, TypedDict


class SourceRecord(TypedDict, total=False):
    code: str
    name: str
    purchase_status: str
    purchase_limit: str
    nav: Optional[float]
    nav_date: str
    return_1y: str
    error: Optional[str]


def empty_record(code: str) -> SourceRecord:
    return {
        "code": code,
        "name": "",
        "purchase_status": "未知",
        "purchase_limit": "未知",
        "nav": None,
        "nav_date": "",
        "return_1y": "",
        "error": None,
    }


def format_limit_yuan(value) -> str:
    """
    把 AKShare 的"日累计限定金额"数值（单位：元）格式化为统一字符串，
    与 HTML 解析的 purchase_limit 字段保持一致。

    例：50.0 → "50.00元"；50000.0 → "5.00万元"；None → "未知"
    """
    if value is None:
        return "未知"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "未知"
    if v <= 0:
        return "未知"
    if v >= 10000:
        wan = v / 10000
        return f"{wan:.2f}万元" if wan != int(wan) else f"{int(wan)}万元"
    return f"{v:.2f}元" if v != int(v) else f"{int(v)}元"
