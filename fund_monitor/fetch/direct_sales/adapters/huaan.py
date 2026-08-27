"""华安基金官网独立 Adapter。

华安官网公开产品页同时展示基金基本信息、当前交易状态，以及“单日单账户
限额”下的直销/代销金额。华安首页的基金表通过 ``viewFund(code)`` 公开产品
代码和名称，可作为当前官网基金目录入口。
"""

from datetime import datetime, timezone
import re
from typing import Any, Callable, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from ..common import _amount, _record
from ..registry import HEADERS
from .base import (
    AuthBoundary,
    ChannelTradeStatus,
    DirectLimitSnapshot,
    FundIdentity,
    ProductSnapshot,
    TradeSnapshot,
)


class HuaanAdapter:
    """华安官网基金目录、产品页和直销/代销限额适配器。"""

    manager_id = "华安"
    catalogue_url = "https://wap.huaan.com.cn/"
    product_url_template = "https://wap.huaan.com.cn/funds/{code}/index.shtml"
    source_type_catalogue = "huaan_official_fund_catalogue"
    source_type_product = "huaan_official_product_page"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _observed_at(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def _headers(cls, referer: str = "") -> Dict[str, str]:
        return {**HEADERS, "Referer": referer or cls.catalogue_url}

    @staticmethod
    def _share_class(name: str) -> str:
        match = re.search(r"([A-Z])$", name.strip())
        return match.group(1) if match else ""

    @classmethod
    def identity_from_config(cls, fund: Dict[str, Any]) -> FundIdentity:
        code = str(fund["code"])
        name = str(fund.get("name") or fund.get("display") or code)
        return FundIdentity(
            manager_id=cls.manager_id,
            code=code,
            name=name,
            share_class=cls._share_class(name),
            source_url=cls.product_url_template.format(code=code),
            source_type="config_compatibility",
        )

    @classmethod
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        """解析华安首页公开基金表及推荐基金中的 ``viewFund(code)``。"""
        soup = BeautifulSoup(bytes(html or b"").decode("utf-8", errors="replace"), "html.parser")
        found: Dict[str, FundIdentity] = {}
        for anchor in soup.find_all("a", onclick=True):
            match = re.search(r"viewFund\(\s*['\"](\d{6})['\"]\s*\)", anchor.get("onclick", ""))
            if not match:
                continue
            code = match.group(1)
            name_node = anchor.find("b") or anchor
            name = name_node.get_text(" ", strip=True)
            name = re.sub(r"\s+", " ", name).strip()
            if not name:
                continue
            found.setdefault(
                code,
                FundIdentity(
                    manager_id=cls.manager_id,
                    code=code,
                    name=name,
                    share_class=cls._share_class(name),
                    source_url=cls.product_url_template.format(code=code),
                    source_type=cls.source_type_catalogue,
                ),
            )
        return sorted(found.values(), key=lambda item: item.code)

    def discover_funds(self) -> List[FundIdentity]:
        response = self.session.get(self.catalogue_url, headers=self._headers(), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("华安基金目录需要认证")
        response.raise_for_status()
        return self.parse_catalogue_html(response.content, response.url or self.catalogue_url)

    @staticmethod
    def _amount_match(text: str, label: str) -> Optional[str]:
        match = re.search(rf"{re.escape(label)}\s*([\d,.]+)\s*(亿元|亿|万元|万|元)", text)
        return _amount("".join(match.groups())) if match else None

    @staticmethod
    def _status(text: str, action: str) -> Optional[bool]:
        match = re.search(rf"(暂停|限额|开放){re.escape(action)}", text)
        if not match:
            return None
        return match.group(1) != "暂停"

    @classmethod
    def parse_product_html(
        cls,
        html: bytes,
        code: str,
        source_url: str,
        observed_at: str,
    ) -> ProductSnapshot:
        text = BeautifulSoup(bytes(html or b"").decode("utf-8", errors="replace"), "html.parser").get_text(" ", strip=True)
        full_name_match = re.search(r"法定名称\s*(.*?)\s*基金代码", text)
        short_name_match = re.search(r"基金简称\s*(.*?)\s*成立日期", text)
        type_match = re.search(r"基金类型\s*(.*?)\s*交易状态", text)
        status_match = re.search(r"交易状态\s*(.*?)\s*基金经理", text)
        manager_match = re.search(r"基金经理\s*(.*?)\s*币种", text)
        risk_match = re.search(r"产品风险等级\s*(R\d)", text)
        scale_match = re.search(r"最新规模\s*([\d,.]+)元", text)
        nav_date_match = re.search(r"最新净值[（(](\d{2}-\d{2})[）)]", text)
        nav_match = re.search(r"单位净值\s*([\d.]+)", text)
        direct_limit = cls._amount_match(text, "直销")
        distribution_limit = cls._amount_match(text, "代销")
        status_text = status_match.group(1).strip() if status_match else ""
        fields = {
            "基金代码": code,
            "法定名称": full_name_match.group(1).strip() if full_name_match else "",
            "基金简称": short_name_match.group(1).strip() if short_name_match else "",
            "基金类型": type_match.group(1).strip() if type_match else "",
            "交易状态": status_text,
            "基金经理": manager_match.group(1).strip() if manager_match else "",
            "产品风险等级": risk_match.group(1) if risk_match else "",
            "直销限额": direct_limit or "",
            "代销限额": distribution_limit or "",
            "最新规模": scale_match.group(1) if scale_match else "",
            "最新净值": nav_match.group(1) if nav_match else "",
        }
        name = short_name_match.group(1).strip() if short_name_match else (full_name_match.group(1).strip() if full_name_match else code)
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=code,
            name=name,
            full_name=full_name_match.group(1).strip() if full_name_match else "",
            fund_type=type_match.group(1).strip() if type_match else "",
            risk_level=risk_match.group(1) if risk_match else "",
            inception_date=(re.search(r"成立日期\s*(\d{4}-\d{2}-\d{2})", text).group(1) if re.search(r"成立日期\s*(\d{4}-\d{2}-\d{2})", text) else ""),
            asset_scale=scale_match.group(1) if scale_match else "",
            net_value_date=nav_date_match.group(1) if nav_date_match else "",
            trade_status=status_text or "未知",
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(self.catalogue_url), timeout=self.timeout)
        response.raise_for_status()
        return self.parse_product_html(response.content, fund.code, url, self._observed_at())

    def fetch_channel_limits(self, fund: FundIdentity) -> Dict[str, Any]:
        """同时返回直销和代销限额，便于三列结果展示。"""
        product = self.fetch_product(fund)
        return {
            "direct_limit": product.fields.get("直销限额") or None,
            "distribution_limit": product.fields.get("代销限额") or None,
            "source_url": product.source_url,
            "observed_at": product.observed_at,
            "raw": product.fields,
        }

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        product = self.fetch_product(fund)
        direct = product.fields.get("直销限额") or None
        distribution = product.fields.get("代销限额") or None
        raw = dict(product.fields)
        raw["distribution_limit"] = distribution or ""
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="华安官网直销",
            limit=direct,
            status="ok" if direct else "official_api_no_data",
            quota_type="单日单账户限额" if direct else "",
            quota_remark=f"官网同时展示代销限额：{distribution or '未获取'}。",
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw=raw,
        )

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        status_text = product.trade_status
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="华安官网直销",
            subscription=self._status(status_text, "申购"),
            redemption=self._status(status_text, "赎回"),
            sip=self._status(status_text, "定投"),
            limit=product.fields.get("直销限额", ""),
            quota_type="单日单账户限额" if product.fields.get("直销限额") else "",
            quota_remark=f"代销限额={product.fields.get('代销限额') or '未获取'}。",
            raw=product.fields,
        )
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel],
            api_status=0,
            message=status_text,
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw=product.fields,
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status != "ok":
            return None
        distribution = snapshot.raw.get("distribution_limit") or "未获取"
        return _record(
            snapshot.limit or "未获取",
            "华安基金官网产品页",
            snapshot.source_url,
            f"官网明确展示直销限额；同时展示代销限额={distribution}。",
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="华安官网首页基金目录和产品页可匿名访问；未观察到登录、验证码、设备签名或银行卡绑定要求。",
        )

