#!/usr/bin/env python3
"""独立的 QDII 基金查询 Skill；不依赖本项目其它 Python 模块。"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FUNDS = SKILL_DIR / "funds.json"
DEFAULT_TIMEOUT = 30
USER_AGENT = "qdii-fund-radar-skill/1.0 (+https://fund.eastmoney.com/)"

JJJZ_URL = "https://fund.eastmoney.com/Data/Fund_JJJZ_Data.aspx"
RANKING_URL = "https://fund.eastmoney.com/data/rankhandler.aspx"
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


def _limit(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "未知"
    if amount <= 0:
        return "未知"
    if amount >= 10000:
        wan = amount / 10000
        return f"{int(wan)}万元" if wan == int(wan) else f"{wan:.2f}万元"
    return f"{int(amount)}元" if amount == int(amount) else f"{amount:.2f}元"


def _record(code: str, **values) -> dict:
    return {"code": code, "name": "", "purchase_status": "未知", "purchase_limit": "未知",
            "nav": None, "nav_date": "", "return_1y": "", "error": None, **values}


def _float(value):
    try:
        return float(value) if value not in (None, "", "-", "--") else None
    except (TypeError, ValueError):
        return None


def _jjjz_rows(text: str) -> dict[str, dict]:
    match = re.search(r"datas:(\[.*?\])\s*,", text, re.DOTALL)
    if not match:
        raise RuntimeError("JJJZ 接口返回结构异常")
    try:
        rows = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RuntimeError("JJJZ 接口 JSON 解析失败") from exc
    result = {}
    for row in rows:
        if not row or not row[0]:
            continue
        status = row[5] or "未知"
        limit = "暂停" if "暂停" in status else "不限" if "开放" in status else _limit(row[9])
        result[row[0]] = _record(row[0], name=row[1] or "", nav=_float(row[3]), nav_date=row[4] or "",
                                 purchase_status=status, purchase_limit=limit)
    return result


def _ranking_rows(text: str) -> dict[str, dict]:
    match = re.search(r"datas:\s*\[(.*?)\]", text, re.DOTALL)
    if not match:
        raise RuntimeError("RANKING 接口返回结构异常")
    rows = re.findall(r'"([^\"]*)"', match.group(1))
    if not rows:
        raise RuntimeError("RANKING 接口没有数据")
    result = {}
    for raw in rows:
        row = raw.split(",")
        if not row or not row[0]:
            continue
        result[row[0]] = _record(row[0], name=row[1] if len(row) > 1 else "",
                                 nav_date=row[3] if len(row) > 3 else "",
                                 nav=_float(row[4]) if len(row) > 4 else None,
                                 return_1y=f"{row[11]}%" if len(row) > 11 and row[11] not in ("", "--") else "")
    return result


def _html_record(code: str) -> dict:
    result = _record(code)
    try:
        raw = _get(FUND_URL.format(code=code), headers={"Accept": "text/html"}).decode("utf-8", "ignore")
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
                result["purchase_limit"] = f"{found.group(1)}元"
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
        path.write_text(json.dumps({"updated_at": datetime.now().isoformat(timespec="seconds"),
                                    "funds": {r["code"]: r for r in rows}}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def load_funds(path: str | None) -> list[dict]:
    source = Path(path).expanduser() if path else DEFAULT_FUNDS
    raw = json.loads(source.read_text(encoding="utf-8"))
    if "passive_funds" in raw or "active_funds" in raw:
        return [{**fund, "group": group} for group in ("passive", "active") for fund in raw.get(f"{group}_funds", [])]
    return [{**fund, "group": fund.get("group", "active")} for fund in raw.get("funds", [])]


def fetch_funds(funds: list[dict], include_market: bool = False, year: int = 2026) -> list[dict]:
    cache = _load_cache()
    try:
        jjjz = _jjjz_rows(_get(JJJZ_URL, {"t": "8", "page": "1,50000", "js": "reData", "sort": "fcode,asc"}).decode("utf-8", "ignore"))
        jjjz_error = None
    except Exception as exc:
        jjjz, jjjz_error = {}, str(exc)
    try:
        today = datetime.now().date()
        ranking = _ranking_rows(_get(RANKING_URL, {"op": "ph", "dt": "kf", "ft": "all", "sc": "1nzf", "st": "desc", "sd": str(today - timedelta(days=365)), "ed": str(today), "qdii": "", "pi": "1", "pn": "50000", "dx": "0"}).decode("utf-8", "ignore"))
        ranking_error = None
    except Exception as exc:
        ranking, ranking_error = {}, str(exc)
    rows = []
    for fund in funds:
        code = fund["code"]
        base = _record(code, name=fund.get("name", ""), display=fund.get("display", ""), group=fund["group"])
        j, rank = jjjz.get(code), ranking.get(code)
        html = _html_record(code) if not j or not rank else None
        chosen = j if j and j.get("purchase_status") not in ("", "未知", None) else html
        if chosen and chosen.get("purchase_status") not in ("", "未知", None):
            base.update(chosen)
            base["source"] = "jjjz" if chosen is j else "html"
            base["confidence"] = "high" if chosen is j else "medium"
        elif cache.get(code):
            base.update(cache[code]); base["source"] = "stale"; base["confidence"] = "low"
            base["warnings"] = ["主源和备源失败，使用本地历史缓存"]
        else:
            base["source"] = "none"; base["confidence"] = "low"
            base["error"] = jjjz_error or "未取得申购数据"
            base["warnings"] = ["没有可用的当前数据或历史缓存"]
        if rank and rank.get("return_1y"):
            base.update(return_1y=rank["return_1y"], nav=rank.get("nav") or base.get("nav"), nav_date=rank.get("nav_date") or base.get("nav_date"))
        elif html and html.get("return_1y"):
            base["return_1y"] = html["return_1y"]
        if ranking_error:
            base.setdefault("warnings", []).append(f"收益率主源失败: {ranking_error}")
        if jjjz_error:
            base.setdefault("warnings", []).append(f"限额主源失败: {jjjz_error}")
        rows.append(base)
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


def _number(value):
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def query(options: dict) -> dict:
    all_funds = load_funds(options.get("config"))
    group = options.get("group", "all")
    codes = set(options.get("code") or [])
    funds = [f for f in all_funds if (group == "all" or f["group"] == group) and (not codes or f["code"] in codes)]
    rows = fetch_funds(funds, options.get("action") == "market-distribution" or options.get("include_market_distribution", False), options.get("year", 2026))
    if options.get("status"):
        rows = [r for r in rows if options["status"] in r.get("purchase_status", "")]
    action = options.get("action", "snapshot")
    fields = {
        "quota": ("code", "name", "display", "group", "purchase_status", "purchase_limit", "source", "confidence", "warnings", "error"),
        "performance": ("code", "name", "display", "group", "return_1y", "nav", "nav_date", "source", "confidence", "warnings", "error"),
        "market-distribution": ("code", "name", "display", "group", "market_distribution", "market_distribution_report_id", "market_distribution_year", "market_distribution_error"),
    }
    if action == "summary":
        data = {"by_group": {g: sum(r.get("group") == g for r in rows) for g in ("passive", "active")},
                "by_status": {s: sum(r.get("purchase_status") == s for r in rows) for s in sorted({r.get("purchase_status") for r in rows})},
                "top_return_1y": sorted(rows, key=lambda r: _number(r.get("return_1y")) or -1e9, reverse=True)[:10]}
    else:
        data = [{key: row.get(key) for key in fields[action]} for row in rows] if action in fields else rows
        sort_key = options.get("sort")
        if sort_key:
            data.sort(key=lambda row: row.get(sort_key, "") if sort_key == "name" else (_number(row.get(sort_key)) or -1e9), reverse=sort_key == "return_1y")
        if options.get("limit") is not None:
            data = data[:max(0, options["limit"])]
    return {"as_of": datetime.now().isoformat(timespec="seconds"), "action": action, "count": len(rows), "data": data,
            "warnings": [f"{r['code']}: {w}" for r in rows for w in r.get("warnings", [])]}


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
