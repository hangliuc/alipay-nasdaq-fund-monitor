#!/usr/bin/env python3
"""独立的 QDII 基金查询 Skill；不依赖主项目其它 Python 模块。"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FUNDS = SKILL_DIR / "funds.json"
DEFAULT_TIMEOUT = 30
USER_AGENT = "qdii-fund-radar-skill/2.0 (+https://fund.eastmoney.com/)"
FUND_URL = "https://fund.eastmoney.com/{code}.html"
CSRC_SEARCH_URL = "http://eid.csrc.gov.cn/fund/disclose/advanced_search_report.do"
CSRC_PDF_URL = "http://eid.csrc.gov.cn/fund/disclose/instance_show_pdf_id.do?instanceid={instance_id}"

MAIN_CODE_MAP = {
    "017437": "017436", "014002": "014001", "021277": "021276", "017731": "017730",
    "012922": "012920", "021842": "021841", "015202": "015201", "024239": "024238",
    "016702": "016701", "018036": "018035", "022184": "100055", "017204": "017203",
    "008254": "008253", "017093": "017092", "016665": "016664", "021662": "457001",
}


def _get(url: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
         headers: dict | None = None) -> bytes:
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(str(exc)) from exc


def _record(code: str, **values) -> dict:
    return {"code": code, "name": "", "purchase_status": "未知", "purchase_limit": "未知",
            "nav": None, "nav_date": "", "return_1y": "", "error": None, **values}


def _missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return str(value).strip() in {"", "nan", "NaN", "NaT", "--", "-", "<NA>"}


def _text(value) -> str:
    return "" if _missing(value) else str(value).strip()


def _code(value) -> str:
    text = _text(value)
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text else ""


def _float(value):
    if _missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_text(value) -> str:
    if _missing(value):
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def _percent(value) -> str:
    number = _float(value)
    return "" if number is None else f"{number:g}%"


def _limit(value) -> str:
    amount = _float(value)
    if amount is None or amount <= 0:
        return "未知"
    if amount >= 10000:
        wan = amount / 10000
        return f"{int(wan)}万元" if wan == int(wan) else f"{wan:.2f}万元"
    return f"{int(amount)}元" if amount == int(amount) else f"{amount:.2f}元"


def _validate_frame(df, required: set[str], name: str) -> None:
    if df is None or getattr(df, "empty", True):
        raise RuntimeError(f"{name} 返回空数据")
    missing = sorted(required - set(getattr(df, "columns", [])))
    if missing:
        raise RuntimeError(f"{name} 缺少字段: {', '.join(missing)}")


def _load_akshare():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("未安装 AKShare，请在 Skill 目录执行 pip install -r requirements.txt") from exc
    return ak


def _ak_purchase_rows() -> dict[str, dict]:
    df = _load_akshare().fund_purchase_em()
    _validate_frame(df, {
        "基金代码", "基金简称", "最新净值/万份收益", "最新净值/万份收益-报告时间",
        "申购状态", "日累计限定金额",
    }, "fund_purchase_em")
    result = {}
    for _, row in df.iterrows():
        code = _code(row.get("基金代码"))
        if not code:
            continue
        result[code] = _record(
            code,
            name=_text(row.get("基金简称")),
            nav=_float(row.get("最新净值/万份收益")),
            nav_date=_date_text(row.get("最新净值/万份收益-报告时间")),
            purchase_status=_text(row.get("申购状态")) or "未知",
            purchase_limit=_limit(row.get("日累计限定金额")),
        )
    if not result:
        raise RuntimeError("fund_purchase_em 返回空数据")
    return result


def _ak_rank_rows() -> dict[str, dict]:
    df = _load_akshare().fund_open_fund_rank_em(symbol="全部")
    _validate_frame(df, {"基金代码", "基金简称", "日期", "单位净值", "近1年"},
                    "fund_open_fund_rank_em")
    result = {}
    for _, row in df.iterrows():
        code = _code(row.get("基金代码"))
        if not code:
            continue
        result[code] = _record(
            code,
            name=_text(row.get("基金简称")),
            nav=_float(row.get("单位净值")),
            nav_date=_date_text(row.get("日期")),
            return_1y=_percent(row.get("近1年")),
        )
    if not result:
        raise RuntimeError("fund_open_fund_rank_em 返回空数据")
    return result


def _html_record(code: str) -> dict:
    result = _record(code)
    try:
        raw = _get(FUND_URL.format(code=code), headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://fund.eastmoney.com/",
        }).decode("utf-8", "ignore")
        name = re.search(r"<title>(.+?)\(\d{6}\)", raw)
        if name:
            result["name"] = name.group(1).strip()
        return_1y = re.search(r"近1年[：:]</span>\s*<span[^>]*>([-\d.]+%)</span>", raw)
        if return_1y:
            result["return_1y"] = return_1y.group(1)

        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text)
        if "暂停申购" in text:
            result.update(purchase_status="暂停申购", purchase_limit="暂停")
        elif "限大额" in text:
            result["purchase_status"] = "限大额"
        elif "开放申购" in text:
            result.update(purchase_status="开放申购", purchase_limit="不限")

        patterns = [
            r"单日累计购买上限([\d,.]+万)元", r"单日累计购买上限([\d,.]+)元",
            r"购买上限([\d,.]+万)元", r"购买上限([\d,.]+)元",
            r"限大额.*?上限.*?([\d,.]+万)\s*元", r"限大额.*?上限.*?([\d,.]+)\s*元",
            r"单日.*?限额.*?([\d,.]+)\s*元",
        ]
        for pattern in patterns:
            found = re.search(pattern, text)
            if found:
                amount = found.group(1)
                result["purchase_limit"] = amount if amount.endswith("元") else f"{amount}元"
                break
        if result["purchase_status"] == "限大额" and result["purchase_limit"] == "未知":
            result["purchase_limit"] = "限大额(金额未知)"
        if result["purchase_status"] == "未知" and not result["name"]:
            result["error"] = "HTML 中未解析到基金信息"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _cache_path() -> Path:
    configured = os.environ.get("QDII_RADAR_CACHE")
    if configured:
        return Path(configured).expanduser()
    base = Path.home() / ("Library/Caches" if sys.platform == "darwin" else ".cache") / "qdii-fund-radar"
    return base / "latest.json"


def _load_cache() -> dict[str, dict]:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8")).get("funds", {})
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _save_cache(rows: list[dict]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "funds": {r["code"]: r for r in rows},
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def load_funds(path: str | None) -> list[dict]:
    source = Path(path).expanduser() if path else DEFAULT_FUNDS
    raw = json.loads(source.read_text(encoding="utf-8"))
    if "passive_funds" in raw or "active_funds" in raw:
        return [{**fund, "group": group} for group in ("passive", "active")
                for fund in raw.get(f"{group}_funds", [])]
    return [{**fund, "group": fund.get("group", "active")} for fund in raw.get("funds", [])]


def _has_status(row: dict | None) -> bool:
    return bool(row and not row.get("error")
                and row.get("purchase_status") not in (None, "", "未知"))


def _has_performance(row: dict | None) -> bool:
    return bool(row and not row.get("error") and (row.get("return_1y") or row.get("nav")))


def _status_class(status: str) -> str:
    if "暂停" in status:
        return "suspended"
    if "限大额" in status or "限购" in status:
        return "limited"
    if "开放" in status:
        return "open"
    return "unknown"


def _status_compat(a: str, b: str) -> bool:
    return a == b or _status_class(a) == _status_class(b)


def _limit_yuan(value: str):
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


def _limit_compat(a: str, b: str) -> bool:
    if "不限" in a and "不限" in b or "暂停" in a and "暂停" in b:
        return True
    av, bv = _limit_yuan(a), _limit_yuan(b)
    return av is not None and bv is not None and abs(av - bv) < 0.01


def _number(value: str):
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def _number_compat(a: str, b: str, tolerance: float = 0.05) -> bool:
    av, bv = _number(a), _number(b)
    return av is not None and bv is not None and abs(av - bv) <= tolerance


def _cross_check(ak_purchase: dict | None, ak_rank: dict | None, html: dict | None) -> list[str]:
    warnings = []
    if _has_status(ak_purchase) and _has_status(html):
        ak_status = ak_purchase["purchase_status"]
        html_status = html["purchase_status"]
        if not _status_compat(ak_status, html_status):
            warnings.append(f"交叉验证不一致：状态 AKShare={ak_status} / HTML={html_status}")
        if _status_class(ak_status) in ("limited", "unknown") \
                and _status_class(html_status) in ("limited", "unknown"):
            ak_limit = ak_purchase.get("purchase_limit", "")
            html_limit = html.get("purchase_limit", "")
            if ak_limit and html_limit and not _limit_compat(ak_limit, html_limit):
                warnings.append(f"交叉验证不一致：限额 AKShare={ak_limit} / HTML={html_limit}")
    if ak_rank and ak_rank.get("return_1y") and html and html.get("return_1y") \
            and not _number_compat(ak_rank["return_1y"], html["return_1y"]):
        warnings.append(
            f"交叉验证不一致：近1年收益率 AKShare={ak_rank['return_1y']} "
            f"/ HTML={html['return_1y']}"
        )
    return warnings


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def fetch_funds(funds: list[dict], include_market: bool = False, year: int = 2026) -> list[dict]:
    cache = _load_cache()
    try:
        purchase = _ak_purchase_rows()
        purchase_error = None
    except Exception as exc:
        purchase, purchase_error = {}, str(exc)
    try:
        ranking = _ak_rank_rows()
        ranking_error = None
    except Exception as exc:
        ranking, ranking_error = {}, str(exc)

    rows = []
    for index, fund in enumerate(funds):
        code = fund["code"]
        ak_purchase = purchase.get(code)
        ak_rank = ranking.get(code)
        html = _html_record(code)
        base = _record(code, name=fund.get("name", ""), display=fund.get("display", ""),
                       group=fund["group"], warnings=[])
        warnings = []

        if _has_status(ak_purchase):
            quota_source = "akshare"
            base.update(purchase_status=ak_purchase["purchase_status"],
                        purchase_limit=ak_purchase.get("purchase_limit", "未知"))
        elif _has_status(html):
            quota_source = "html"
            base.update(purchase_status=html["purchase_status"],
                        purchase_limit=html.get("purchase_limit", "未知"))
        elif cache.get(code):
            quota_source = "stale"
            cached = cache[code]
            base.update(purchase_status=cached.get("purchase_status", "未知"),
                        purchase_limit=cached.get("purchase_limit", "未知"))
        else:
            quota_source = "none"

        if _has_performance(ak_rank):
            performance_source = "akshare"
            base.update(return_1y=ak_rank.get("return_1y", ""), nav=ak_rank.get("nav"),
                        nav_date=ak_rank.get("nav_date", ""))
        elif html.get("return_1y"):
            performance_source = "html"
            base["return_1y"] = html["return_1y"]
        else:
            performance_source = "none"

        # AKShare 的申购接口结果优先补充名称/净值，排行接口作为净值补充。
        for candidate in (ak_purchase, ak_rank, html):
            if not candidate:
                continue
            if not base.get("name") and candidate.get("name"):
                base["name"] = candidate["name"]
            if base.get("nav") is None and candidate.get("nav") is not None:
                base["nav"] = candidate["nav"]
            if not base.get("nav_date") and candidate.get("nav_date"):
                base["nav_date"] = candidate["nav_date"]

        if purchase_error:
            warnings.append(f"AKShare 申购数据不可用: {purchase_error}")
        if ranking_error:
            warnings.append(f"AKShare 收益数据不可用: {ranking_error}")
        if html.get("error"):
            warnings.append(f"天天基金 HTML 详情页失败: {html['error']}")
        warnings.extend(_cross_check(ak_purchase, ak_rank, html))

        validation_sources = (_has_status(ak_purchase) or _has_performance(ak_rank))
        if validation_sources and (_has_status(html) or html.get("return_1y")):
            base["cross_validation"] = "mismatch" if any("交叉验证不一致" in w for w in warnings) else "matched"
        elif validation_sources:
            base["cross_validation"] = "akshare_only"
        elif _has_status(html) or html.get("return_1y"):
            base["cross_validation"] = "html_only"
        elif quota_source == "stale":
            base["cross_validation"] = "stale"
        else:
            base["cross_validation"] = "none"

        base["quota_source"] = quota_source
        base["performance_source"] = performance_source
        base["source"] = quota_source
        if quota_source == "none":
            base["error"] = purchase_error or "AKShare 和天天基金 HTML 详情页均失败且无历史记录"
            warnings.append(f"❌ 无可用限购数据: {base['error']}")
        elif quota_source == "stale":
            base["error"] = "AKShare 和天天基金 HTML 详情页均失败，使用上次历史值"
            warnings.append("⚠️ 数据陈旧：实时双源均失败，回退到上次记录")
        else:
            base["error"] = None
        expected_html_performance = (
            quota_source == "akshare"
            and performance_source == "html"
            and "暂停" in base.get("purchase_status", "")
        )
        base["confidence"] = (
            "low" if quota_source in ("none", "stale") else
            "medium" if quota_source == "html" or base["cross_validation"] == "mismatch"
            or (performance_source == "html" and not expected_html_performance) else "high"
        )
        base["warnings"] = _dedupe(warnings)
        rows.append(base)
        if index < len(funds) - 1:
            time.sleep(1.0)

    if include_market:
        market = fetch_market_distribution(funds, year)
        for row in rows:
            row.update(market.get(row["code"], {"market_distribution": {}}))
    _save_cache(rows)
    return rows


def _csrc_search(fund: dict, year: int) -> str | None:
    payload = [
        {"name": "sEcho", "value": 1}, {"name": "iColumns", "value": 6},
        {"name": "iDisplayStart", "value": 0}, {"name": "iDisplayLength", "value": 20},
        {"name": "mDataProp_0", "value": "fund"}, {"name": "mDataProp_1", "value": "fund"},
        {"name": "mDataProp_2", "value": "reportName"}, {"name": "mDataProp_3", "value": "reportName"},
        {"name": "mDataProp_4", "value": "reportDesp"}, {"name": "mDataProp_5", "value": "reportSendDate"},
        {"name": "iSortingCols", "value": 0}, {"name": "fundType", "value": ""},
        {"name": "reportType", "value": "FB030"}, {"name": "reportYear", "value": str(year)},
        {"name": "fundCompanyShortName", "value": ""},
        {"name": "fundCode", "value": fund.get("main_code") or MAIN_CODE_MAP.get(fund["code"]) or fund["code"]},
        {"name": "fundShortName", "value": ""}, {"name": "startUploadDate", "value": ""},
        {"name": "endUploadDate", "value": ""},
    ]
    try:
        raw = _get(CSRC_SEARCH_URL, {"aoData": json.dumps(payload, ensure_ascii=False)}).decode("utf-8", "ignore")
        records = json.loads(raw).get("aaData", [])
    except Exception:
        records = []
    for keyword in ("第2季度", "第二季度", "第1季度", "第一季度"):
        for record in records:
            if keyword in record.get("reportName", ""):
                return str(record.get("uploadInfoId", "")) or None
    return str(records[0].get("uploadInfoId", "")) if records else None


def _parse_pdf(content: bytes) -> dict[str, float]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("市场分布需要安装可选依赖: pip install -r requirements.txt") from exc
    result, in_section = {}, False
    pattern = re.compile(r"(美国|中国内地|中国大陆|中国香港|中国台湾|中国|日本|韩国|英国|德国|法国|印度|新加坡|澳大利亚|加拿大|瑞士|荷兰|巴西|以色列|台湾|香港)\s+[\d,，.]+\s+([\d.]+)")
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                line = line.strip()
                if "国家" in line and any(k in line for k in ("公允", "比例", "资产净值")) and "合计" not in line:
                    in_section = True
                    continue
                if not in_section:
                    continue
                if line.startswith(("合计", "注", "5.")):
                    in_section = False
                    continue
                match = pattern.match(line)
                if match:
                    name = {"中国": "中国内地", "中国大陆": "中国内地", "台湾": "中国台湾", "香港": "中国香港"}.get(match.group(1), match.group(1))
                    result.setdefault(name, float(match.group(2)))
    return result


def fetch_market_distribution(funds: list[dict], year: int) -> dict[str, dict]:
    result = {}
    for index, fund in enumerate(funds):
        try:
            report_id = _csrc_search(fund, year)
            if not report_id:
                result[fund["code"]] = {"market_distribution": {}, "market_distribution_error": "未找到证监会季报", "market_distribution_year": year}
                continue
            result[fund["code"]] = {"market_distribution": _parse_pdf(_get(CSRC_PDF_URL.format(instance_id=report_id))),
                                     "market_distribution_report_id": report_id, "market_distribution_year": year}
        except Exception as exc:
            result[fund["code"]] = {"market_distribution": {}, "market_distribution_error": str(exc), "market_distribution_year": year}
        if index < len(funds) - 1:
            time.sleep(0.2)
    return result


def query(options: dict) -> dict:
    all_funds = load_funds(options.get("config"))
    group = options.get("group", "all")
    codes = set(options.get("code") or [])
    funds = [f for f in all_funds if (group == "all" or f["group"] == group)
             and (not codes or f["code"] in codes)]
    rows = fetch_funds(
        funds,
        options.get("action") == "market-distribution" or options.get("include_market_distribution", False),
        options.get("year", 2026),
    )
    if options.get("status"):
        rows = [r for r in rows if options["status"] in r.get("purchase_status", "")]
    action = options.get("action", "snapshot")
    # 双源、置信度和交叉验证只用于 Skill 内部决策，不暴露给最终用户。
    # 这样 Agent 可以直接消费基金业务字段，不会把内部数据质量诊断渲染成用户报告。
    common = ("code", "name", "display", "group", "error")
    fields = {
        "quota": common + ("purchase_status", "purchase_limit"),
        "performance": common + ("return_1y", "nav", "nav_date"),
        "market-distribution": ("code", "name", "display", "group", "market_distribution",
                                 "market_distribution_report_id", "market_distribution_year",
                                 "market_distribution_error"),
    }
    if action == "summary":
        data = {
            "by_group": {g: sum(r.get("group") == g for r in rows) for g in ("passive", "active")},
            "by_status": {s: sum(r.get("purchase_status") == s for r in rows)
                          for s in sorted({r.get("purchase_status") for r in rows})},
            "top_return_1y": [
                {key: row.get(key) for key in ("code", "name", "display", "group",
                                                "return_1y", "nav", "nav_date", "error")}
                for row in sorted(rows, key=lambda r: _number(r.get("return_1y")) or -1e9,
                                  reverse=True)[:10]
            ],
        }
    else:
        if action in fields:
            data = [{key: row.get(key) for key in fields[action]} for row in rows]
        else:
            data = [{key: row.get(key) for key in (
                "code", "name", "display", "group", "purchase_status", "purchase_limit",
                "return_1y", "nav", "nav_date", "market_distribution",
                "market_distribution_report_id", "market_distribution_year",
                "market_distribution_error", "error",
            )} for row in rows]
        sort_key = options.get("sort")
        if sort_key:
            data.sort(key=lambda row: row.get(sort_key, "") if sort_key == "name"
                      else (_number(row.get(sort_key)) or -1e9), reverse=sort_key == "return_1y")
        if options.get("limit") is not None:
            data = data[:max(0, options["limit"])]
    return {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "count": len(rows),
        "data": data,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="独立 QDII 基金查询 Skill（JSON 输出）")
    parser.add_argument("--action", choices=["snapshot", "quota", "performance", "market-distribution", "summary"], default="snapshot")
    parser.add_argument("--config", help="可选基金配置 JSON；默认使用 Skill 自带清单")
    parser.add_argument("--code", nargs="+")
    parser.add_argument("--group", choices=["all", "passive", "active"], default="all")
    parser.add_argument("--status")
    parser.add_argument("--sort", choices=["return_1y", "purchase_limit", "name"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--include-market-distribution", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(query(vars(args)), ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
