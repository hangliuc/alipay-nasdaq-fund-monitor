"""AKShare + 天天基金 HTML 双源聚合、交叉验证和历史兜底。"""

import logging
import random
import re
import time
from typing import Optional

from fund_monitor.fetch.sources import akshare_source, csrc_market_distribution, eastmoney_html
from fund_monitor.fetch.sources.base import SourceRecord, empty_record

log = logging.getLogger(__name__)


def aggregate(
    fund_list: list[dict],
    history_latest: Optional[dict] = None,
    include_market_distribution: bool = False,
    market_distribution_year: int = 2026,
) -> list[dict]:
    """按固定双源策略批量获取并合并基金数据。

    每次运行都会先批量调用 AKShare，再逐只请求天天基金 HTML 详情页。
    两个来源都成功时进行字段级交叉验证；一方失败时由另一方接管；
    两方都失败时使用 ``history_latest``。
    """
    history_latest = history_latest or {}
    total = len(fund_list)

    print("  ▶ AKShare：批量拉取申购状态、限额和净值...", end=" ", flush=True)
    purchase_snap, purchase_error = _safe_snapshot(akshare_source.fetch_purchase_snapshot)
    if purchase_error:
        print(f"❌ {purchase_error}")
    else:
        print(f"OK（{len(purchase_snap)} 只）")

    print("  ▶ AKShare：批量拉取近一年收益率...", end=" ", flush=True)
    rank_snap, rank_error = _safe_snapshot(akshare_source.fetch_rank_snapshot)
    if rank_error:
        print(f"❌ {rank_error}")
    else:
        print(f"OK（{len(rank_snap)} 只）")

    html_records: dict[str, SourceRecord] = {}
    print(f"  ▶ 天天基金 HTML 详情页：逐只获取并交叉验证 {total} 只...")
    for i, fund in enumerate(fund_list):
        code = fund["code"]
        print(f"    [{i + 1}/{total}] {code}...")
        html_records[code] = eastmoney_html.fetch_one(code)
        if i < total - 1:
            time.sleep(random.uniform(1.0, 2.5))

    market_snap = {}
    if include_market_distribution:
        print(f"  ▶ CSRC：拉取 {market_distribution_year} 年季报市场分布...")
        market_snap = csrc_market_distribution.fetch_many(fund_list, year=market_distribution_year)

    results = []
    for fund in fund_list:
        code = fund["code"]
        merged = _merge(
            code=code,
            ak_purchase=purchase_snap.get(code),
            ak_rank=rank_snap.get(code),
            html=html_records.get(code),
            history=history_latest.get(code),
            cfg_name=fund.get("name", ""),
            purchase_error=purchase_error,
            rank_error=rank_error,
        )
        if fund.get("display"):
            merged["display"] = fund["display"]
        if include_market_distribution:
            merged.update(market_snap.get(code, {"market_distribution": {}}))
        results.append(merged)

    return results


def _safe_snapshot(fetcher):
    try:
        return fetcher(), None
    except Exception as exc:
        return {}, str(exc)


def _merge(
    code: str,
    ak_purchase: Optional[SourceRecord],
    ak_rank: Optional[SourceRecord],
    html: Optional[SourceRecord],
    history: Optional[dict],
    cfg_name: str,
    purchase_error: Optional[str],
    rank_error: Optional[str],
) -> dict:
    out = empty_record(code)
    out.update({
        "warnings": [],
        "source": "none",
        "confidence": "low",
        "quota_source": "none",
        "performance_source": "none",
        "cross_validation": "not_available",
    })

    ak_purchase_ok = _has_status(ak_purchase)
    html_ok = _has_status(html)
    rank_ok = _has_performance(ak_rank)
    html_return_ok = bool(html and html.get("return_1y"))

    # 字段级选择：AKShare 优先，HTML 接管，最后使用历史限购值。
    if ak_purchase_ok:
        quota_source = "akshare"
        status = ak_purchase["purchase_status"]
        limit = ak_purchase.get("purchase_limit", "未知")
    elif html_ok:
        quota_source = "html"
        status = html["purchase_status"]
        limit = html.get("purchase_limit", "未知")
    elif history:
        quota_source = "stale"
        status = history.get("purchase_status", "未知")
        limit = history.get("purchase_limit", "未知")
    else:
        quota_source = "none"
        status = "未知"
        limit = "未知"

    if rank_ok:
        performance_source = "akshare"
        return_1y = ak_rank.get("return_1y", "")
    elif html_return_ok:
        performance_source = "html"
        return_1y = html.get("return_1y", "")
    else:
        performance_source = "none"
        return_1y = ""

    # AKShare 的净值优先，两个批量结果之间再互相补充。
    nav, nav_date = _first_value(
        (ak_purchase, "nav", "nav_date"),
        (ak_rank, "nav", "nav_date"),
        (html, "nav", "nav_date"),
    )
    name = _first_text(ak_purchase, "name") or _first_text(ak_rank, "name") \
        or _first_text(html, "name") or cfg_name

    out.update({
        "name": name,
        "purchase_status": status,
        "purchase_limit": limit,
        "nav": nav,
        "nav_date": nav_date,
        "return_1y": return_1y,
        "quota_source": quota_source,
        "performance_source": performance_source,
        "source": quota_source,
    })

    warnings = []
    if purchase_error:
        warnings.append(f"AKShare 申购数据不可用: {purchase_error}")
    if rank_error:
        warnings.append(f"AKShare 收益数据不可用: {rank_error}")
    if html and html.get("error"):
        warnings.append(f"天天基金 HTML 详情页失败: {html['error']}")

    validation_warnings = _cross_check(ak_purchase, ak_rank, html)
    warnings.extend(validation_warnings)

    if ak_purchase_ok and (html_ok or html_return_ok):
        out["cross_validation"] = "mismatch" if validation_warnings else "matched"
    elif ak_purchase_ok or rank_ok:
        out["cross_validation"] = "akshare_only"
    elif html_ok or html_return_ok:
        out["cross_validation"] = "html_only"
    elif history:
        out["cross_validation"] = "stale"
    else:
        out["cross_validation"] = "none"

    if quota_source == "none":
        out["error"] = _first_error(ak_purchase, html) or purchase_error \
            or "AKShare 和天天基金 HTML 详情页均失败且无历史记录"
        warnings.append(f"❌ 无可用限购数据: {out['error']}")
    elif quota_source == "stale":
        out["error"] = "AKShare 和天天基金 HTML 详情页均失败，使用上次历史值"
        warnings.append("⚠️ 数据陈旧：实时双源均失败，回退到上次记录")
    else:
        out["error"] = None

    out["warnings"] = _dedupe(warnings)
    out["confidence"] = _confidence(quota_source, performance_source, out["cross_validation"])
    return out


def _confidence(quota_source: str, performance_source: str, validation: str) -> str:
    if quota_source == "none":
        return "low"
    if quota_source == "stale":
        return "low"
    if validation == "mismatch":
        return "medium"
    if quota_source == "html" or performance_source == "html":
        return "medium"
    return "high"


def _cross_check(
    ak_purchase: Optional[SourceRecord],
    ak_rank: Optional[SourceRecord],
    html: Optional[SourceRecord],
) -> list[str]:
    """比较 AKShare 与天天基金 HTML 的共同字段。"""
    warnings = []
    if not _has_status(ak_purchase) or not _has_status(html):
        return warnings

    ak_status = ak_purchase.get("purchase_status", "")
    html_status = html.get("purchase_status", "")
    if not _status_compat(ak_status, html_status):
        warnings.append(f"交叉验证不一致：状态 AKShare={ak_status} / HTML={html_status}")

    if _status_class(ak_status) in ("limited", "unknown") \
            and _status_class(html_status) in ("limited", "unknown"):
        ak_limit = ak_purchase.get("purchase_limit", "")
        html_limit = html.get("purchase_limit", "")
        if ak_limit and html_limit and not _limit_compat(ak_limit, html_limit):
            warnings.append(f"交叉验证不一致：限额 AKShare={ak_limit} / HTML={html_limit}")

    if ak_rank and ak_rank.get("return_1y") and html.get("return_1y") \
            and not _number_compat(ak_rank["return_1y"], html["return_1y"], 0.05):
        warnings.append(
            f"交叉验证不一致：近1年收益率 AKShare={ak_rank['return_1y']} "
            f"/ HTML={html['return_1y']}"
        )
    return warnings


def _has_status(rec: Optional[SourceRecord]) -> bool:
    return bool(
        rec and not rec.get("error")
        and rec.get("purchase_status") not in (None, "", "未知")
    )


def _has_performance(rec: Optional[SourceRecord]) -> bool:
    return bool(rec and not rec.get("error") and (rec.get("return_1y") or rec.get("nav")))


def _first_value(*sources):
    for source, value_key, date_key in sources:
        if source and source.get(value_key) is not None:
            return source.get(value_key), source.get(date_key, "")
    return None, ""


def _first_text(source: Optional[SourceRecord], key: str) -> str:
    return str(source.get(key, "")).strip() if source and source.get(key) else ""


def _status_compat(a: str, b: str) -> bool:
    return a == b or _status_class(a) == _status_class(b)


def _status_class(status: str) -> str:
    if "暂停" in status:
        return "suspended"
    if "限大额" in status or "限购" in status:
        return "limited"
    if "开放" in status:
        return "open"
    return "unknown"


def _limit_compat(a: str, b: str) -> bool:
    if "不限" in a and "不限" in b or "暂停" in a and "暂停" in b:
        return True
    av, bv = _limit_to_yuan(a), _limit_to_yuan(b)
    return av is not None and bv is not None and abs(av - bv) < 0.01


def _limit_to_yuan(value: str) -> Optional[float]:
    if not value or "未知" in value:
        return None
    match = re.search(r"([\d,.]+)\s*万", value)
    multiplier = 10000 if match else 1
    if not match:
        match = re.search(r"([\d,.]+)", value)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "")) * multiplier
    except ValueError:
        return None


def _number_compat(a: str, b: str, tolerance: float) -> bool:
    def parse(value):
        match = re.search(r"-?[\d,.]+", value or "")
        return float(match.group(0).replace(",", "")) if match else None

    av, bv = parse(a), parse(b)
    return av is not None and bv is not None and abs(av - bv) <= tolerance


def _first_error(*records) -> Optional[str]:
    for record in records:
        if record and record.get("error"):
            return record["error"]
    return None


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
