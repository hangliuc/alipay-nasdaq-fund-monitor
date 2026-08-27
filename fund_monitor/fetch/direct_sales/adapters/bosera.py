"""博时基金官网独立 Adapter。

博时基金目录页把当前公开基金/份额以 ``window.fundListJson`` 嵌入页面；
每个产品页同时公开交易按钮、当前交易状态、净值和基金资料。目录 JSON
中的 ``limitLargeDesc`` 是官网产品目录用于展示的当前大额申购限制字段。

本模块只访问 ``bosera.com``，不进入交易表单，不登录，不使用第三方数据。
"""

from datetime import datetime, timezone
import json
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


class BoseraAdapter:
    """博时官网目录、产品页和当前公开交易限制 Adapter。"""

    manager_id = "博时"
    home_url = "https://www.bosera.com/"
    catalogue_url = "https://www.bosera.com/fund/index.html"
    product_url_template = "https://www.bosera.com/fund/{code}.html"
    source_type_catalogue = "bosera_official_fund_catalogue_json"
    source_type_product = "bosera_official_product_page"
    source_type_limit = "bosera_official_catalogue_limit"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._catalogue_rows: Optional[List[Dict[str, Any]]] = None

    def _observed_at(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def _headers(cls, referer: str = "") -> Dict[str, str]:
        return {**HEADERS, "Referer": referer or cls.home_url}

    @staticmethod
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").replace("\xa0", " ").split())

    @staticmethod
    def _share_class(name: str) -> str:
        match = re.search(r"([A-Z])(?:人民币|美元现汇|美元)?$", str(name or "").strip())
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
    def parse_catalogue_payload(cls, payload: Any, source_url: str = "") -> List[FundIdentity]:
        """解析官网 ``fundListJson`` 数组并按份额代码去重。"""
        rows = payload if isinstance(payload, list) else []
        found: Dict[str, FundIdentity] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("fundCode") or row.get("subFundCode") or "").strip()
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = cls._clean(row.get("shortName") or row.get("fundName") or code)
            found[code] = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=cls._clean(row.get("fundTypeShow") or row.get("fundType")),
                share_class=cls._share_class(name),
                source_url=cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def parse_catalogue_rows(cls, payload: Any) -> List[Dict[str, Any]]:
        """返回可审计的官网原始行，供限额解析使用。"""
        rows = payload if isinstance(payload, list) else []
        return [row for row in rows if isinstance(row, dict)]

    @classmethod
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        text = bytes(html or b"").decode("utf-8", errors="ignore")
        match = re.search(r"window\.fundListJson\s*=\s*(\[.*?\]);", text, re.S)
        if not match:
            return []
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []
        return cls.parse_catalogue_payload(payload, source_url)

    @classmethod
    def _catalogue_payload_from_html(cls, html: bytes) -> List[Dict[str, Any]]:
        text = bytes(html or b"").decode("utf-8", errors="ignore")
        match = re.search(r"window\.fundListJson\s*=\s*(\[.*?\]);", text, re.S)
        if not match:
            return []
        try:
            return cls.parse_catalogue_rows(json.loads(match.group(1)))
        except json.JSONDecodeError:
            return []

    def _load_catalogue_rows(self) -> List[Dict[str, Any]]:
        if self._catalogue_rows is not None:
            return self._catalogue_rows
        response = self.session.get(
            self.catalogue_url,
            headers=self._headers(self.home_url),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("博时基金目录页需要认证")
        response.raise_for_status()
        rows = self._catalogue_payload_from_html(response.content)
        if not rows:
            raise RuntimeError("博时基金目录页未找到 fundListJson")
        self._catalogue_rows = rows
        return rows

    def discover_funds(self) -> List[FundIdentity]:
        return self.parse_catalogue_payload(self._load_catalogue_rows(), self.catalogue_url)

    @classmethod
    def _table_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for row in soup.select("#fundInfo tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            if len(cells) >= 2 and cells[0]:
                fields[cells[0]] = cells[1]
        return fields

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._table_fields(soup)
        name_node = soup.select_one(".fund-essential_head .name")
        code_node = soup.select_one(".fund-essential_head .code")
        name = cls._clean(name_node.get_text(" ", strip=True) if name_node else "") or str(code)
        page_code = cls._clean(code_node.get_text(" ", strip=True) if code_node else str(code))
        status_node = soup.select_one(".fund-essential_head .stint-money")
        status_text = cls._clean(status_node.get_text(" ", strip=True) if status_node else "")
        type_node = soup.select_one(".fund-essential_item:nth-of-type(1) em")
        risk_node = soup.select_one(".fund-essential_item:nth-of-type(2) em")
        fund_type = cls._clean(type_node.get_text(" ", strip=True) if type_node else fields.get("基金类型"))
        risk = cls._clean(risk_node.get_text(" ", strip=True) if risk_node else fields.get("风险等级"))
        net_node = soup.select_one(".fund-essential_return .num")
        net_text = cls._clean(net_node.get_text(" ", strip=True) if net_node else "")
        nav_match = re.search(r"([\d]+\.\d+)", net_text)
        nav = nav_match.group(1) if nav_match else ""
        page_text = cls._clean(soup.get_text(" ", strip=True))
        nav_date_match = re.search(r"单位净值\s*/\s*日涨幅\s*\((\d{4}-\d{2}-\d{2})\)", page_text)
        nav_date = nav_date_match.group(1) if nav_date_match else ""
        buy = soup.select_one(".fund-essential_action .btn-primary")
        sip = soup.select_one(".fund-essential_action .btn-default")
        buy_status = "closed" if buy is None or "disabled" in set(buy.get("class") or []) else "open"
        sip_status = "closed" if sip is None or "disabled" in set(sip.get("class") or []) else "open"
        fields.update({
            "基金代码": page_code,
            "当前交易状态": status_text,
            "最新净值": nav,
            "最新净值日期": nav_date,
            "购买按钮状态": buy_status,
            "定投按钮状态": sip_status,
        })
        if not status_text:
            status_text = "正常开放" if buy_status == "open" else "购买不可用"
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=fields.get("基金名称", ""),
            fund_type=fund_type,
            risk_level=risk,
            inception_date=fields.get("成立生效日期", ""),
            asset_scale=fields.get("基金规模", ""),
            net_value_date=nav_date,
            trade_status=status_text,
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("博时基金产品页需要认证")
        response.raise_for_status()
        return self.parse_product_html(response.content, fund.code, response.url or url, self._observed_at())

    @staticmethod
    def _status_bool(value: str) -> Optional[bool]:
        if value == "open":
            return True
        if value == "closed":
            return False
        return None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="博时官网产品页",
            subscription=self._status_bool(product.fields.get("购买按钮状态", "")),
            redemption=None,
            sip=self._status_bool(product.fields.get("定投按钮状态", "")),
            quota_remark="购买/定投状态来自博时官网产品页公开按钮；目录 JSON 限额字段单独保留。")
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel],
            api_status=0,
            message=product.trade_status,
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw=product.fields,
        )

    @classmethod
    def _limit_from_desc(cls, desc: str) -> Optional[str]:
        text = cls._clean(desc)
        if not text:
            return None
        if re.search(r"暂停(?:大额)?申购", text):
            return "暂停"
        if re.search(r"不限|不设上限|无限额", text):
            return "不限"
        # 优先采用个人客户金额；不要把“起购金额”混入限额。
        personal = re.search(r"个人[^\d]{0,30}([\d,.]+\s*(?:亿元|亿|万元|万|元))", text)
        if personal:
            return _amount(personal.group(1))
        return _amount(text)

    @classmethod
    def parse_limit_row(cls, row: Dict[str, Any]) -> Optional[Dict[str, str]]:
        desc = cls._clean(row.get("limitLargeDesc"))
        limit = cls._limit_from_desc(desc)
        if not limit:
            return None
        return {
            "limit": limit,
            "status": "ok",
            "quota_type": "官网产品目录当前大额申购限制",
            "quota_remark": f"博时官网基金目录 limitLargeDesc：{desc}",
        }

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        try:
            row = next((item for item in self._load_catalogue_rows()
                        if str(item.get("fundCode") or item.get("subFundCode") or "") == fund.code), None)
            product = self.fetch_product(fund)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="博时官网目录/产品页", limit=None, status="official_interface_requires_auth",
                quota_remark=str(exc), source_url=self.product_url_template.format(code=fund.code),
                observed_at=self._observed_at(), raw={"error": str(exc)},
            )
        parsed = self.parse_limit_row(row or {})
        if parsed is None and product.trade_status and re.search(r"暂停(?:大额)?申购", product.trade_status):
            parsed = {"limit": "暂停", "status": "ok", "quota_type": "官网产品页当前交易状态",
                      "quota_remark": "博时官网产品页在基金代码旁公开显示暂停申购。"}
        if parsed is None:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="博时官网目录/产品页", limit=None, status="official_api_no_data",
                quota_remark="产品页和目录可访问，但未公开可确认的大额申购限额；不以起投金额代替。",
                source_url=product.source_url, observed_at=product.observed_at,
                raw={**(row or {}), **product.fields},
            )
        source_url = self.catalogue_url if row else product.source_url
        return DirectLimitSnapshot(
            manager_id=self.manager_id, code=fund.code, customer_type="individual",
            channel="博时官网基金目录/产品页", limit=parsed["limit"], status=parsed["status"],
            quota_type=parsed["quota_type"], quota_remark=parsed["quota_remark"],
            source_url=source_url, observed_at=product.observed_at,
            raw={**(row or {}), **product.fields},
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(snapshot.limit or "未获取", "博时基金官网目录/产品页", snapshot.source_url,
                       snapshot.quota_remark, status=snapshot.status)

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="博时基金目录页、产品页和当前交易按钮均可匿名访问；本 Adapter 不访问登录交易接口。",
        )
