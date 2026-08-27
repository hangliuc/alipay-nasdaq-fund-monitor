"""官网产品页与公开状态/API 适配器。"""

import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .common import _fetch_adapter_limits, _record
from .adapters.chinaamc import ChinaAMCAdapter
from .adapters.bosera import BoseraAdapter
from .adapters.efunds import EFundsAdapter
from .adapters.guotai import GuotaiAdapter
from .adapters.huaan import HuaanAdapter
from .adapters.huatai_pb import HuataiPBAdapter
from .adapters.jiashi import JiashiAdapter
from .adapters.southern import SouthernAdapter
from .registry import HEADERS, adapter_for_fund


PUBLIC_PRODUCT_URL_TEMPLATES = {
    "国富": "https://www.ftsfund.com/qxjj/jjxq/{code}",
    "华宝": "https://www.fsfund.com/fund/{code}/fundDetail.shtml",
    "汇添富": "https://www.99fund.com/main/products/pofund/{code}/fundgk.shtml",
    "招商": "https://www.cmfchina.com/web/fundDetail/{code}/index.html",
    "天弘": "https://www.thfund.com.cn/fundinfo/{code}",
}


def _fetch_efunds(funds: list[dict]) -> dict[str, dict]:
    """兼容旧编排器的易方达入口；具体规则由独立 Adapter 负责。"""
    return _fetch_adapter_limits(funds, EFundsAdapter())


def _fetch_huaan(funds: list[dict]) -> dict[str, dict]:
    """兼容旧编排器的华安入口；具体规则由独立 Adapter 负责。"""
    return _fetch_adapter_limits(funds, HuaanAdapter())


def _fetch_chinaamc(funds: list[dict]) -> dict[str, dict]:
    """兼容旧编排器的华夏入口；具体规则由独立 Adapter 负责。"""
    return _fetch_adapter_limits(funds, ChinaAMCAdapter())


def _fetch_jsfund(funds: list[dict]) -> dict[str, dict]:
    """兼容旧编排器入口；具体规则由独立 ``JiashiAdapter`` 负责。"""
    return _fetch_adapter_limits(funds, JiashiAdapter())


def _fetch_huatai_pb(funds: list[dict]) -> dict[str, dict]:
    """兼容旧编排器的华泰柏瑞入口；具体规则由独立 Adapter 负责。"""
    return _fetch_adapter_limits(funds, HuataiPBAdapter())


def _fetch_gtfund(funds: list[dict]) -> dict[str, dict]:
    """兼容旧编排器的国泰入口；具体规则由独立 Adapter 负责。"""
    return _fetch_adapter_limits(funds, GuotaiAdapter())


def _fetch_southern(funds: list[dict]) -> dict[str, dict]:
    """兼容旧编排器的南方入口；规则由独立 Adapter 负责。"""
    return _fetch_adapter_limits(funds, SouthernAdapter())


def _fetch_bosera_public_product_pages(funds: list[dict]) -> dict[str, dict]:
    """兼容旧编排器的博时入口；具体规则由独立 Adapter 负责。"""
    return _fetch_adapter_limits(funds, BoseraAdapter())


def _probe_public_product_pages(
    funds: list[dict], existing_codes: Optional[set[str]] = None
) -> dict[str, dict]:
    """验证官网产品页可访问性，并明确区分“页面存在”和“直销限额已获取”。"""
    existing_codes = existing_codes or set()
    records = {}
    for fund in funds:
        if fund["code"] in existing_codes:
            continue
        adapter = adapter_for_fund(fund)
        if not adapter or adapter.manager not in PUBLIC_PRODUCT_URL_TEMPLATES:
            continue
        url = PUBLIC_PRODUCT_URL_TEMPLATES[adapter.manager].format(code=fund["code"])
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            text = BeautifulSoup(response.content, "lxml").get_text(" ", strip=True)
        except requests.RequestException:
            continue
        # 不以“最低申购金额”或泛化的“限额”冒充直销大额申购限制。只有清楚
        # 出现直销和金额同一语境时，才交由专用 Adapter 产出 ok 结果。
        if re.search(r"直销.{0,40}([\d,.]+)\s*(亿元|亿|万元|万|元)", text):
            note = "官网产品页含直销相关文字，但当前通用解析器未能验证其为单日申购上限；等待专用 Adapter。"
        else:
            note = "官网产品页可访问，但未找到可验证的直销单日申购上限字段。"
        records[fund["code"]] = _record(
            "未获取",
            f"{adapter.manager}基金官网产品页",
            url,
            note,
            status="official_public_page_no_direct_limit",
        )
    return records
