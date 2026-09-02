"""中国证监会基金季报中的国家/地区证券市场投资分布。"""

from __future__ import annotations

import io
import json
import logging
import re
from typing import Optional

import requests

log = logging.getLogger(__name__)

SEARCH_URL = "http://eid.csrc.gov.cn/fund/disclose/advanced_search_report.do"
PDF_URL = "http://eid.csrc.gov.cn/fund/disclose/instance_show_pdf_id.do?instanceid={instance_id}"
# C 类/发起式份额到主基金代码的已知映射；其它基金仍可在配置中显式提供 main_code。
MAIN_CODE_MAP = {
    "017437": "017436", "014002": "014001", "021277": "021276", "017731": "017730",
    "012922": "012920", "021842": "021841", "015202": "015201", "024239": "024238",
    "016702": "016701", "018036": "018035", "022184": "100055", "017204": "017203",
    "008254": "008253", "017093": "017092", "016665": "016664", "021662": "457001",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "http://eid.csrc.gov.cn/fund/disclose/advanced_search.html",
}
_COUNTRY_RE = re.compile(
    r"(美国|中国内地|中国大陆|中国香港|中国台湾|中国|日本|韩国|英国|德国|法国|印度|新加坡|澳大利亚|加拿大|瑞士|荷兰|巴西|以色列|开曼群岛|百慕大|台湾|香港|意大利|西班牙|越南|印度尼西亚|马来西亚|泰国|菲律宾)\s+[\d,，.]+\s+([\d.]+)"
)


def _search(code: str = "", short_name: str = "", year: int = 2026, timeout: int = 20) -> Optional[str]:
    ao_data = [
        {"name": "sEcho", "value": 1}, {"name": "iColumns", "value": 6},
        {"name": "iDisplayStart", "value": 0}, {"name": "iDisplayLength", "value": 20},
        {"name": "mDataProp_0", "value": "fund"}, {"name": "mDataProp_1", "value": "fund"},
        {"name": "mDataProp_2", "value": "reportName"}, {"name": "mDataProp_3", "value": "reportName"},
        {"name": "mDataProp_4", "value": "reportDesp"}, {"name": "mDataProp_5", "value": "reportSendDate"},
        {"name": "iSortingCols", "value": 0}, {"name": "fundType", "value": ""},
        {"name": "reportType", "value": "FB030"}, {"name": "reportYear", "value": str(year)},
        {"name": "fundCompanyShortName", "value": ""}, {"name": "fundCode", "value": code},
        {"name": "fundShortName", "value": short_name}, {"name": "startUploadDate", "value": ""},
        {"name": "endUploadDate", "value": ""},
    ]
    try:
        response = requests.get(SEARCH_URL, params={"aoData": json.dumps(ao_data)}, headers=HEADERS, timeout=timeout)
        response.raise_for_status()
        records = response.json().get("aaData", [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("CSRC 季报检索失败 %s: %s", code or short_name, exc)
        return None
    for keyword in ("第2季度", "第二季度", "第1季度", "第一季度"):
        for record in records:
            if keyword in record.get("reportName", ""):
                return str(record.get("uploadInfoId", "")) or None
    return str(records[0].get("uploadInfoId", "")) if records else None


def parse_market_distribution(pdf_content: bytes) -> dict[str, float]:
    """解析季报中的市场分布表；支持表头与数据跨页。"""
    result: dict[str, float] = {}
    in_section = False
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
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
                    match = _COUNTRY_RE.match(line)
                    if match:
                        country = {"中国": "中国内地", "中国大陆": "中国内地", "台湾": "中国台湾", "香港": "中国香港"}.get(match.group(1), match.group(1))
                        result.setdefault(country, float(match.group(2)))
    except Exception as exc:  # PDF 结构不稳定，单只基金失败不应中断批次
        log.warning("季报 PDF 解析失败: %s", exc)
    return result


def fetch_one(fund: dict, year: int = 2026, timeout: int = 30) -> dict:
    """返回 market_distribution、market_distribution_report_id 和错误信息。"""
    code = fund["code"]
    main_code = fund.get("main_code") or MAIN_CODE_MAP.get(code) or code
    instance_id = _search(code=main_code, year=year, timeout=timeout)
    if not instance_id:
        instance_id = _search(short_name=fund.get("short_name") or fund.get("name", ""), year=year, timeout=timeout)
    if not instance_id:
        return {"market_distribution": {}, "market_distribution_error": "未找到证监会季报"}
    try:
        response = requests.get(PDF_URL.format(instance_id=instance_id), headers=HEADERS, timeout=timeout)
        response.raise_for_status()
        return {"market_distribution": parse_market_distribution(response.content), "market_distribution_report_id": instance_id}
    except requests.RequestException as exc:
        return {"market_distribution": {}, "market_distribution_error": f"季报 PDF 获取失败: {exc}"}


def fetch_many(funds: list[dict], year: int = 2026) -> dict[str, dict]:
    return {fund["code"]: fetch_one(fund, year=year) for fund in funds}
