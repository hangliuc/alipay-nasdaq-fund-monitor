"""国泰基金官网独立 Adapter。

国泰网上基金超市的单只产品页同时承担两个公开用途：页面侧栏列出当前
官网基金目录，产品摘要公开直销单笔/累计日限额，产品概况公开基金基本字段。
本模块只读取页面公开内容，不登录、不提交交易；页面没有交易状态字段时不
凭“申购”导航文字猜测开放/暂停状态。
"""

import json
from datetime import datetime, timezone
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

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


class GuotaiAdapter:
    """国泰基金超市产品目录与直销限额 Adapter。"""

    manager_id = "国泰"
    catalogue_url = "https://e.gtfund.com/Etrade/Jijin/view/id/160213"
    product_url_template = "https://e.gtfund.com/Etrade/Jijin/view/id/{code}"
    source_type_catalogue = "guotai_official_fund_supermarket"
    source_type_product = "guotai_official_direct_product_page"

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
    def _headers(cls) -> Dict[str, str]:
        return {**HEADERS, "Referer": cls.catalogue_url}

    @staticmethod
    def _share_class(name: str) -> str:
        match = re.search(r"([A-Z])$", name.strip())
        return match.group(1) if match else ""

    @staticmethod
    def _normalize_date(value: Any) -> str:
        text = str(value or "").strip()
        match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        return text

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
        """从基金超市页面侧栏解析全部公开基金链接并去重。"""
        soup = BeautifulSoup(html, "html.parser")
        found: Dict[str, FundIdentity] = {}
        for anchor in soup.find_all("a", href=True):
            match = re.search(r"/Etrade/Jijin/view/id/(\d{6})(?:[/?#]|$)", anchor["href"], re.I)
            if not match:
                continue
            code = match.group(1)
            name = anchor.get_text(" ", strip=True)
            if not name:
                continue
            url = urljoin(source_url or cls.catalogue_url, anchor["href"])
            found.setdefault(
                code,
                FundIdentity(
                    manager_id=cls.manager_id,
                    code=code,
                    name=name,
                    share_class=cls._share_class(name),
                    source_url=url,
                    source_type=cls.source_type_catalogue,
                ),
            )
        return sorted(found.values(), key=lambda item: item.code)

    def discover_funds(self) -> List[FundIdentity]:
        response = self.session.get(
            self.catalogue_url,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("国泰基金超市需要认证")
        response.raise_for_status()
        return self.parse_catalogue_html(response.content, response.url or self.catalogue_url)

    @staticmethod
    def _gt_option(text: str, key: str) -> str:
        match = re.search(rf"\b{re.escape(key)}\s*:\s*([\"'])(.*?)\1", text, re.S)
        if not match:
            return ""
        raw = match.group(2)
        try:
            return str(json.loads(f'"{raw}"'))
        except (ValueError, TypeError):
            return raw.replace(r"\u", "")

    @classmethod
    def parse_product_html(
        cls,
        html: bytes,
        code: str,
        source_url: str,
        observed_at: str,
    ) -> ProductSnapshot:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        script_text = "\n".join(script.get_text(" ", strip=False) for script in soup.find_all("script"))
        name = cls._gt_option(script_text, "fundname")
        if not name:
            code_match = re.search(r"([^\s]+)\s*\(\s*%s\s*\)" % re.escape(code), text)
            name = code_match.group(1) if code_match else code
        full_name = ""
        full_match = re.search(r"基金全称\s*([^\s]+(?:基金|证券投资基金))", text)
        if full_match:
            full_name = full_match.group(1)
        type_match = re.search(r"基金类型\s*([^\s]+)", text)
        risk_match = re.search(r"风险等级\s*([^\s]+)", text)
        inception_match = re.search(r"基金合同生效日\s*(\d{4}年\d{1,2}月\d{1,2}日)", text)
        nav_match = re.search(r"([\d.]+)\s*单位净值\s*(\d{2}-\d{2})", text)
        daily_match = re.search(r"直销累计日限额\s*([\d,.]+)\s*(亿元|亿|万元|万|元)", text)
        single_match = re.search(r"直销单笔限额\s*([\d,.]+)\s*(亿元|亿|万元|万|元)", text)
        fields = {
            "基金代码": code,
            "基金经理": re.search(r"基金经理\s*([^\s]+)", text).group(1) if re.search(r"基金经理\s*([^\s]+)", text) else "",
            "基金类型": type_match.group(1) if type_match else "",
            "风险等级": risk_match.group(1) if risk_match else "",
            "直销单笔限额": "".join(single_match.groups()) if single_match else "",
            "直销累计日限额": "".join(daily_match.groups()) if daily_match else "",
        }
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=code,
            name=name,
            full_name=full_name,
            fund_type=type_match.group(1) if type_match else "",
            risk_level=risk_match.group(1) if risk_match else "",
            inception_date=cls._normalize_date(inception_match.group(1) if inception_match else ""),
            asset_scale="",
            net_value_date=nav_match.group(2) if nav_match else "",
            trade_status="未知",
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(), timeout=self.timeout)
        response.raise_for_status()
        return self.parse_product_html(response.content, fund.code, url, self._observed_at())

    @classmethod
    def _limit_value(cls, match: Optional[re.Match]) -> Optional[str]:
        return _amount("".join(match.groups())) if match else None

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        product = self.fetch_product(fund)
        daily_raw = product.fields.get("直销累计日限额", "")
        single_raw = product.fields.get("直销单笔限额", "")
        daily_value = _amount(daily_raw)
        single_value = _amount(single_raw)
        # 页面规则优先取累计日限额；只有单笔限额时才退回单笔。
        limit = daily_value or single_value
        raw = dict(product.fields)
        raw["优先规则"] = "直销累计日限额 > 直销单笔限额"
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="国泰官网直销",
            limit=limit,
            status="ok" if limit else "official_api_no_data",
            quota_type="累计日限额" if daily_value else ("单笔限额" if single_value else ""),
            quota_remark="官网明确展示直销限额；页面未公开第三方限额字段。",
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw=raw,
        )

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="国泰官网直销",
            subscription=None,
            limit="",
            quota_remark="产品页未公开当前申购开放/暂停状态；不从导航文字推断。",
            raw=product.fields,
        )
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel],
            api_status=0,
            message="官网产品页未公开可确认的当前申购状态",
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw=product.fields,
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status != "ok":
            return None
        return _record(
            snapshot.limit or "未获取",
            "国泰基金官网直销产品页",
            snapshot.source_url,
            "官网公开展示直销单笔/累计日限额；优先使用累计日限额，页面未公开第三方限额字段。",
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="国泰基金超市目录和产品页可匿名访问；未观察到登录、验证码、设备签名或银行卡绑定要求。",
        )
