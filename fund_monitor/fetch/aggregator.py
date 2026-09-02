"""
数据聚合器：多源编排 + 交叉验证 + 历史快照兜底

数据源职责：
  ┌──────────────────┬──────────┬──────────────────────────────────────┐
  │ 源               │ 角色     │ 提供字段                              │
  ├──────────────────┼──────────┼──────────────────────────────────────┤
  │ JJJZ (JSON)      │ 限购主源 │ 申购状态、限额、最新净值              │
  │ RANKING (JSON)   │ 业绩主源 │ 近1年收益率、净值日期                 │
  │ HTML (详情页)    │ 限购备源 │ 申购状态、限额（仅 JJJZ 失败时启用）  │
  │ history.json     │ 兜底    │ 上次成功值                            │
  └──────────────────┴──────────┴──────────────────────────────────────┘

  正常路径：仅 2 个 HTTP 请求（JJJZ + RANKING），不调 HTML
  降级路径：JJJZ 失败 → 逐只 HTML（与改造前相同）

每条结果新增字段（不破坏旧字段）：
  - source       "jjjz" / "html" / "stale" / "none"（限购数据来源）
  - confidence   "high" / "medium" / "low"
  - warnings     list[str]，发现的不一致或异常
"""

import logging
import random
import time
from typing import Optional

from fund_monitor.fetch.sources import eastmoney_html, eastmoney_jjjz, eastmoney_ranking
from fund_monitor.fetch.sources import csrc_market_distribution
from fund_monitor.fetch.sources.base import SourceRecord, empty_record

log = logging.getLogger(__name__)


# ── 主入口 ────────────────────────────────────────

def aggregate(
    fund_list: list[dict],
    history_latest: Optional[dict] = None,
    include_market_distribution: bool = False,
    market_distribution_year: int = 2026,
) -> list[dict]:
    """
    Args:
        fund_list:       [{"code": "008971", "name": "...", "display": "..."}, ...]
        history_latest:  history.json 中对应 namespace 下的 latest 快照，用于兜底
                         结构：{code: {"name", "purchase_status", "purchase_limit"}}
    Returns:
        list[dict]，每条至少包含 code/name/purchase_status/purchase_limit/return_1y/error，
        以及新增的 source/confidence/warnings。
    """
    history_latest = history_latest or {}
    total = len(fund_list)

    # ── 1. 限购主源：JJJZ ──
    print("  ▶ 主源 JJJZ：拉取全市场限购快照...", end=" ", flush=True)
    try:
        jjjz_snap = eastmoney_jjjz.fetch_market_snapshot()
        print(f"OK（{len(jjjz_snap)} 只）")
    except Exception as e:
        jjjz_snap = None
        print(f"❌ {e}")

    # ── 2. 业绩主源：RANKING ──
    print("  ▶ 主源 RANKING：拉取全市场业绩快照...", end=" ", flush=True)
    try:
        rank_snap = eastmoney_ranking.fetch_market_snapshot()
        print(f"OK（{len(rank_snap)} 只）")
    except Exception as e:
        rank_snap = None
        print(f"❌ {e}")

    # ── 3. HTML 备源：仅 JJJZ 失败、缺漏，或 RANKING 全挂时启用 ──
    html_records: dict[str, SourceRecord] = {}
    html_needed_codes = _decide_html_codes(fund_list, jjjz_snap, rank_snap)
    if html_needed_codes:
        reason = _html_reason(jjjz_snap, rank_snap)
        print(f"  ▶ 备源 HTML：{reason}，逐只抓取 {len(html_needed_codes)} 只...")
        for i, code in enumerate(html_needed_codes):
            print(f"    [{i + 1}/{len(html_needed_codes)}] {code}...")
            html_records[code] = eastmoney_html.fetch_one(code)
            if i < len(html_needed_codes) - 1:
                time.sleep(random.uniform(1.0, 2.5))
    else:
        print(f"  ▶ 备源 HTML：跳过（{total} 只全部命中 JJJZ + RANKING）")

    market_snap = {}
    if include_market_distribution:
        print(f"  ▶ CSRC：拉取 {market_distribution_year} 年季报市场分布...")
        market_snap = csrc_market_distribution.fetch_many(fund_list, year=market_distribution_year)

    # ── 4. 合并 ──
    results = []
    for fund in fund_list:
        code = fund["code"]
        merged = _merge(
            code=code,
            jjjz=jjjz_snap.get(code) if jjjz_snap else None,
            ranking=rank_snap.get(code) if rank_snap else None,
            html=html_records.get(code),
            history=history_latest.get(code),
            cfg_name=fund.get("name", ""),
        )
        if fund.get("display"):
            merged["display"] = fund["display"]
        if include_market_distribution:
            merged.update(market_snap.get(code, {"market_distribution": {}}))
        results.append(merged)

    return results


def _decide_html_codes(
    fund_list: list[dict],
    jjjz_snap: Optional[dict],
    rank_snap: Optional[dict],
) -> list[str]:
    """
    决定哪些基金需要走 HTML 备源。触发条件（任一）：
    - JJJZ 整体失败 → 全员上 HTML 取限购
    - 该 code 在 JJJZ 中缺失 → 单独上 HTML 补限购
    - RANKING 整体失败 → 全员上 HTML 补收益率
    - 该 code 在 RANKING 中缺失 → 单独上 HTML 补收益率
    """
    codes = []
    for f in fund_list:
        code = f["code"]
        miss_jjjz = jjjz_snap is None or code not in jjjz_snap
        miss_rank = rank_snap is None or code not in rank_snap
        if miss_jjjz or miss_rank:
            codes.append(code)
    return codes


def _html_reason(jjjz_snap: Optional[dict], rank_snap: Optional[dict]) -> str:
    parts = []
    if jjjz_snap is None:
        parts.append("JJJZ 全挂")
    if rank_snap is None:
        parts.append("RANKING 全挂")
    if not parts:
        parts.append("部分基金缺漏")
    return "、".join(parts)


# ── 合并单条 ───────────────────────────────────────

def _merge(
    code: str,
    jjjz: Optional[SourceRecord],
    ranking: Optional[SourceRecord],
    html: Optional[SourceRecord],
    history: Optional[dict],
    cfg_name: str,
) -> dict:
    """
    单条记录的多源合并决策。
    """
    out = empty_record(code)
    out["warnings"] = []
    out["source"] = "none"
    out["confidence"] = "low"

    # ── 收益率：RANKING 优先，HTML 兜底 ──
    return_1y = ""
    if ranking and ranking.get("return_1y"):
        return_1y = ranking["return_1y"]
    elif html and html.get("return_1y"):
        return_1y = html["return_1y"]
    out["return_1y"] = return_1y

    # ── 限购数据：JJJZ → HTML → history → 失败 ──
    jjjz_ok = _has_status(jjjz)
    html_ok = _has_status(html)

    # A. JJJZ 成功
    if jjjz_ok:
        out.update({
            "name": jjjz.get("name") or cfg_name,
            "purchase_status": jjjz["purchase_status"],
            "purchase_limit": jjjz.get("purchase_limit", "未知"),
            "nav": jjjz.get("nav"),
            "nav_date": jjjz.get("nav_date", ""),
            "source": "jjjz",
            "confidence": "high",
            "error": None,
        })
        # 交叉验证：HTML 也成功时（即 JJJZ 没覆盖到的情况以外，这里基本不会发生）
        if html_ok:
            warns = _cross_check(jjjz, html)
            if warns:
                out["warnings"].extend(warns)
                out["confidence"] = "medium"
        return out

    # B. JJJZ 失败、HTML 接管
    if html_ok:
        out.update({
            "name": html.get("name") or cfg_name,
            "purchase_status": html["purchase_status"],
            "purchase_limit": html.get("purchase_limit", "未知"),
            "source": "html",
            "confidence": "medium",
            "error": None,
        })
        out["warnings"].append("主源 JJJZ 失败，已回落 HTML")
        return out

    # C. 主备都失败，回退历史
    if history:
        out.update({
            "name": history.get("name") or cfg_name,
            "purchase_status": history.get("purchase_status", "未知"),
            "purchase_limit": history.get("purchase_limit", "未知"),
            "source": "stale",
            "confidence": "low",
            "error": "主备源均失败，使用上次历史值",
        })
        out["warnings"].append("⚠️ 数据陈旧：主备源均失败，回退到上次记录")
        return out

    # D. 全军覆没
    out.update({
        "name": cfg_name,
        "error": _first_error(jjjz, html) or "主备源均失败且无历史记录",
        "source": "none",
        "confidence": "low",
    })
    out["warnings"].append(f"❌ 主备源均失败且无历史记录: {out['error']}")
    return out


def _has_status(rec: Optional[SourceRecord]) -> bool:
    return bool(
        rec and not rec.get("error")
        and rec.get("purchase_status") not in (None, "", "未知")
    )


# ── 交叉验证 ──────────────────────────────────────

def _cross_check(jjjz: SourceRecord, html: SourceRecord) -> list[str]:
    """JJJZ 与 HTML 的限购结果比对，返回告警列表。"""
    warns = []

    js = jjjz.get("purchase_status", "")
    hs = html.get("purchase_status", "")
    status_ok = (not js or not hs) or _status_compat(js, hs)
    if not status_ok:
        warns.append(f"状态不一致: JJJZ={js} / HTML={hs}")

    # 状态都为"暂停/开放"时跳过限额比对（限额此时无意义，避免噪音）
    j_class = _status_class(js)
    h_class = _status_class(hs)
    if status_ok and j_class in ("limited", "unknown") and h_class in ("limited", "unknown"):
        jl = jjjz.get("purchase_limit", "")
        hl = html.get("purchase_limit", "")
        if jl and hl and not _limit_compat(jl, hl):
            warns.append(f"限额不一致: JJJZ={jl} / HTML={hl}")

    return warns


def _status_compat(a: str, b: str) -> bool:
    if a == b:
        return True
    return _status_class(a) == _status_class(b)


def _status_class(s: str) -> str:
    if "暂停" in s:
        return "suspended"
    if "限大额" in s or "限购" in s:
        return "limited"
    if "开放" in s:
        return "open"
    return "unknown"


def _limit_compat(a: str, b: str) -> bool:
    if a == b:
        return True
    if ("不限" in a) and ("不限" in b):
        return True
    if ("暂停" in a) and ("暂停" in b):
        return True
    return _limit_to_yuan(a) == _limit_to_yuan(b)


def _limit_to_yuan(s: str) -> Optional[float]:
    import re
    if not s or "未知" in s:
        return None
    m = re.search(r"([\d,.]+)\s*万", s)
    if m:
        try:
            return float(m.group(1).replace(",", "")) * 10000
        except ValueError:
            return None
    m = re.search(r"([\d,.]+)", s)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _first_error(*recs) -> Optional[str]:
    for r in recs:
        if r and r.get("error"):
            return r["error"]
    return None
