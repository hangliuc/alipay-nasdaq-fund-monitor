"""华夏基金官网独立 Adapter。

华夏官网公开的基金产品目录由 ``/front/front/es/fundInfo/fundList`` 提供，
单只基金产品页公开当前交易状态，统一交易状态表公开各基金的申购状态和
“单日单账户累计”限制说明。本模块只访问这些公开页面/接口，不登录、不发起
交易，也不把没有返回金额的开放状态推断为“不限”。
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


class ChinaAMCAdapter:
    """华夏基金公开目录、产品页和交易状态表适配器。"""

    manager_id = "华夏"
    catalogue_url = "https://fund.chinaamc.com/jjcp/"
    catalogue_api_url = "https://fund.chinaamc.com/front/front/es/fundInfo/fundList"
    product_url_template = "https://www.chinaamc.com/fund/{code}/index.shtml"
    calendar_url = "https://fund.chinaamc.com/ProductForWeb/getCalendar"
    source_type_catalogue = "chinaamc_official_fund_catalogue_api"
    source_type_product = "chinaamc_official_product_page"
    source_type_calendar = "chinaamc_official_trade_calendar"

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
        return {
            **HEADERS,
            "Referer": referer or cls.catalogue_url,
            "X-Requested-With": "XMLHttpRequest",
        }

    @staticmethod
    def _decode_html(content: bytes) -> str:
        """解码华夏老产品页的 GBK/GB18030 与新页面 UTF-8。"""
        raw = bytes(content or b"")
        try:
            utf8 = raw.decode("utf-8")
            if "交易状态" in utf8 or "基金代码" in utf8:
                return utf8
        except UnicodeDecodeError:
            pass
        return raw.decode("gb18030", errors="replace")

    @staticmethod
    def _share_class(name: str) -> str:
        match = re.search(r"([A-Z])$", name.strip())
        return match.group(1) if match else ""

    @staticmethod
    def _normalize_date(value: Any) -> str:
        text = str(value or "").strip()
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
        return "-".join(match.groups()) if match else text

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
    def parse_catalogue_payload(cls, payload: Dict[str, Any]) -> List[FundIdentity]:
        """解析官网 ``fundList`` JSON，保留当前公开目录中的所有份额。"""
        if str(payload.get("status")) not in {"1", "success", "ok"}:
            raise RuntimeError(str(payload.get("message") or "华夏基金目录接口失败"))
        data = payload.get("data") or {}
        rows = data.get("data") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        found: Dict[str, FundIdentity] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("fundCode") or row.get("fund_code") or "").strip()
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = str(row.get("fundName") or row.get("fund_name") or row.get("fundAliasName") or code).strip()
            found.setdefault(
                code,
                FundIdentity(
                    manager_id=cls.manager_id,
                    code=code,
                    name=name,
                    fund_type=str(row.get("fundType") or row.get("fundtype") or ""),
                    share_class=cls._share_class(name),
                    source_url=cls.product_url_template.format(code=code),
                    source_type=cls.source_type_catalogue,
                ),
            )
        return sorted(found.values(), key=lambda item: item.code)

    def discover_funds(self) -> List[FundIdentity]:
        """从华夏官网基金目录 API 发现当前公开的全部基金份额。"""
        params = {"pageIndex": 0, "pageSize": 2000, "searchName": ""}
        response = self.session.get(
            self.catalogue_api_url,
            params=params,
            headers=self._headers(self.catalogue_url),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("华夏基金目录接口需要认证")
        response.raise_for_status()
        return self.parse_catalogue_payload(response.json())

    @staticmethod
    def _label(text: str, label: str, labels: List[str]) -> str:
        stop = "|".join(re.escape(item) for item in labels if item != label)
        pattern = rf"{re.escape(label)}\s*[：:]\s*(.*?)(?=\s+(?:{stop})\s*[：:]|$)"
        match = re.search(pattern, text)
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""

    @classmethod
    def parse_product_html(
        cls,
        html: bytes,
        code: str,
        source_url: str,
        observed_at: str,
    ) -> ProductSnapshot:
        soup = BeautifulSoup(cls._decode_html(html), "lxml")
        text = soup.get_text(" ", strip=True)
        labels = [
            "基金全称",
            "基金代码",
            "基金合同生效日",
            "基金管理人",
            "基金托管人",
            "交易币种",
            "运作方式",
            "基金经理",
            "客服电话",
        ]
        fields = {
            label: cls._label(text, label, labels)
            for label in labels
            if cls._label(text, label, labels)
        }
        title_node = soup.find(class_="j-tit")
        title_text = title_node.get_text(" ", strip=True) if title_node else ""
        name = re.sub(r"\s*[（(]基金代码[：:]\s*\d{6}[）)]", "", title_text).strip()
        if not name:
            title = soup.title
            name = re.sub(r"基金行情.*$", "", title.get_text(" ", strip=True)) if title else code
        risk_match = re.search(r"(?:低|中低|中|中高|高)风险\s*\(R\d\)", text)
        type_match = re.search(r"(?:货币型|债券型|混合型|股票型|指数型|基金中基金|FOF型)", text)
        status_match = re.search(r"交易状态\s*(暂停申购|开放申购|暂停赎回|开放赎回|暂停|开放)", text)
        nav_match = re.search(r"([\d.]+)\s*净值\s*[（(](\d{4}-\d{2}-\d{2})[）)]", text)
        inception = fields.get("基金合同生效日", "")
        if "基金合同生效日" in inception:
            inception = inception.split("基金合同生效日", 1)[-1].strip(" ：:")
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=code,
            name=name,
            full_name=fields.get("基金全称", ""),
            fund_type=type_match.group(0) if type_match else "",
            risk_level=risk_match.group(0) if risk_match else "",
            inception_date=cls._normalize_date(inception),
            asset_scale="",
            net_value_date=nav_match.group(2) if nav_match else "",
            trade_status=status_match.group(1) if status_match else "未知",
            source_url=source_url,
            observed_at=observed_at,
            fields={**fields, "最新净值": nav_match.group(1) if nav_match else ""},
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(self.catalogue_url), timeout=self.timeout)
        response.raise_for_status()
        return self.parse_product_html(response.content, fund.code, url, self._observed_at())

    @classmethod
    def parse_calendar_html(
        cls,
        html: bytes,
        codes: Optional[set[str]] = None,
        source_url: str = "",
        observed_at: str = "",
    ) -> Dict[str, Dict[str, str]]:
        """解析统一交易状态表，返回代码到状态/限额的映射。"""
        soup = BeautifulSoup(html, "html.parser")
        found: Dict[str, Dict[str, str]] = {}
        for row in soup.find_all("tr"):
            text = re.sub(r"\s+", " ", row.get_text(" ", strip=True))
            matched_codes = re.findall(r"\b\d{6}\b", text)
            if not matched_codes:
                continue
            for code in matched_codes:
                if codes is not None and code not in codes:
                    continue
                status = "暂停" if "暂停" in text else ("开放-有限制" if "开放-有限制" in text else "开放")
                amount_match = re.search(r"累计\s*([\d,.]+)\s*(亿元|亿|万元|万|元)", text)
                limit = _amount("".join(amount_match.groups())) if amount_match else ""
                found[code] = {
                    "status": status,
                    "limit": limit,
                    "remark": text,
                    "source_url": source_url,
                    "observed_at": observed_at,
                }
        return found

    def _calendar(self, code: str = "") -> Dict[str, Dict[str, str]]:
        response = self.session.get(
            self.calendar_url,
            headers=self._headers(self.catalogue_url),
            timeout=self.timeout,
        )
        response.raise_for_status()
        codes = {code} if code else None
        return self.parse_calendar_html(
            response.content,
            codes=codes,
            source_url=self.calendar_url,
            observed_at=self._observed_at(),
        )

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        calendar = self._calendar(fund.code).get(fund.code, {})
        status = calendar.get("status") or product.trade_status
        limit = calendar.get("limit") or ""
        subscription = None if status in {"未知", ""} else not status.startswith("暂停")
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="华夏官网公开交易状态",
            subscription=subscription,
            limit=limit,
            quota_type="单日单账户累计" if limit else "",
            quota_remark=calendar.get("remark", ""),
            raw={"product": product.fields, "calendar": calendar},
        )
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel],
            api_status=0,
            message=status,
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw={"product": product.fields, "calendar": calendar},
        )

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        calendar = self._calendar(fund.code).get(fund.code)
        if calendar:
            if calendar["status"] == "暂停":
                limit, status = "暂停", "ok"
            elif calendar["limit"]:
                limit, status = calendar["limit"], "ok"
            else:
                limit, status = None, "official_api_no_data"
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="华夏官网公开交易状态",
                limit=limit,
                status=status,
                quota_type="单日单账户累计" if calendar["limit"] else "",
                quota_remark=calendar["remark"],
                source_url=self.calendar_url,
                observed_at=calendar["observed_at"],
                raw=calendar,
            )
        product = self.fetch_product(fund)
        if product.trade_status.startswith("暂停"):
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="华夏官网产品页",
                limit="暂停",
                status="ok",
                source_url=product.source_url,
                observed_at=product.observed_at,
                raw=product.fields,
            )
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="华夏官网公开交易状态",
            limit=None,
            status="official_api_no_data",
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
            "华夏官网公开交易状态表" if snapshot.channel != "华夏官网产品页" else "华夏官网产品页",
            snapshot.source_url,
            "官网公开当前交易状态/限额；华夏状态表的限制说明按当前产品规则作为直销结果。",
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="华夏基金目录 API、产品页和交易状态表均可匿名访问；未观察到登录、验证码、设备签名或银行卡绑定要求。",
        )
