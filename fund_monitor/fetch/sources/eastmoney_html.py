"""
数据源：天天基金 HTML 详情页

接口：http://fund.eastmoney.com/{code}.html
特点：
- 含申购状态、限购额度和近1年收益率
- 依赖 HTML class/正则，前端改版会失效，因此需要与 AKShare 交叉验证
- 每次运行逐只请求，既作为 AKShare 的备用，也用于验证共同字段
"""

import re
import random
import time
import requests
from typing import Optional

from fund_monitor.fetch.sources.base import SourceRecord, empty_record

FUND_URL = "https://fund.eastmoney.com/{code}.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
        "Referer": "https://fund.eastmoney.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _parse_page(html: str) -> Optional[dict]:
    """从 HTML 中解析限购信息和近1年收益率"""
    info = {}

    # 基金名称
    m = re.search(r"<title>(.+?)\((\d{6})\)", html)
    if m:
        info["name"] = m.group(1).strip()

    # 近1年收益率
    m = re.search(r"近1年[：:]</span>\s*<span[^>]*>([-\d.]+%)</span>", html)
    if m:
        info["return_1y"] = m.group(1)

    # 交易状态区域
    section = re.search(
        r'class="buyWayStatic">(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL
    )
    if not section:
        section = re.search(r"交易状态[：:](.*?)购买手续费", html, re.DOTALL)

    text = ""
    if section:
        text = re.sub(r"<[^>]+>", " ", section.group(1))
        text = re.sub(r"\s+", " ", text).strip()

    if not text:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)

    # 申购状态
    if "暂停申购" in text:
        info["purchase_status"] = "暂停申购"
        info["purchase_limit"] = "暂停"
    elif "限大额" in text:
        info["purchase_status"] = "限大额"
    elif "开放申购" in text:
        info["purchase_status"] = "开放申购"
        info["purchase_limit"] = "不限"

    # 具体限额
    for pattern in [
        r"单日累计购买上限([\d,.]+万)元",
        r"单日累计购买上限([\d,.]+)元",
        r"购买上限([\d,.]+万)元",
        r"购买上限([\d,.]+)元",
        r"限大额.*?上限.*?([\d,.]+万)\s*元",
        r"限大额.*?上限.*?([\d,.]+)\s*元",
        r"限大额.*?\((.*?元)\)",
        r"单日.*?限额.*?([\d,.]+)\s*元",
    ]:
        m = re.search(pattern, text)
        if m:
            amount = m.group(1).strip()
            info["purchase_limit"] = amount if "元" in amount else f"{amount}元"
            break

    if info.get("purchase_status") == "限大额" and "purchase_limit" not in info:
        info["purchase_limit"] = "限大额(金额未知)"

    if info.get("purchase_status") or info.get("name"):
        return info
    return None


def fetch_one(code: str, timeout: int = 15) -> SourceRecord:
    """抓取单只基金的 HTML 详情。"""
    rec = empty_record(code)
    try:
        resp = requests.get(FUND_URL.format(code=code), headers=HEADERS, timeout=timeout)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            rec["error"] = f"HTTP {resp.status_code}"
            return rec
        info = _parse_page(resp.text)
        if info:
            rec.update(info)
        else:
            rec["error"] = "HTML 中未解析到限购信息"
    except Exception as e:
        rec["error"] = str(e)
    return rec


def fetch_many(codes: list[str], delay: tuple = (1.0, 2.5)) -> dict[str, SourceRecord]:
    """兼容入口：按速率限制逐只抓取 HTML 详情页。"""
    out = {}
    for i, code in enumerate(codes):
        out[code] = fetch_one(code)
        if i < len(codes) - 1:
            time.sleep(random.uniform(*delay))
    return out
