"""华宝基金官网独立 Adapter。

华宝官网基金超市将当前基金目录以 ``fundMarketList`` 形式内嵌在公开
HTML 的 hidden input 中；每只产品的公开产品页提供产品资料、净值和购买
入口。官网公告查询接口目前要求网点代码，因此本适配器不伪造或绕过该
认证边界，直销限额优先解析官网已公开的限额公告 PDF。

只访问 ``fsfund.com``/``api.fsfund.com``/``e.fsfund.com`` 的官方资源，
不访问第三方平台、不提交交易、不登录、不绕过验证码或网点代码校验。
"""

from datetime import datetime, timezone
from io import BytesIO
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError

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


class HuabaoAdapter:
    """华宝官网公开目录、产品页和直销公告 PDF Adapter。"""

    manager_id = "华宝"
    home_url = "https://www.fsfund.com/"
    catalogue_url = "https://www.fsfund.com/fund/fundMarket.shtml"
    product_url_template = "https://www.fsfund.com/fund/{code}/fundDetail.shtml"
    api_base_url = "https://api.fsfund.com/v2/webzk"
    article_api_url = api_base_url + "/queryController/queryArticles"
    trade_entry_url_template = "https://e.fsfund.com/etrading/trade/buyFund/{code}/0;"
    source_type_catalogue = "huabao_official_fund_catalogue_html"
    source_type_product = "huabao_official_product_page"
    source_type_notice = "huabao_official_notice_pdf"
    source_type_article_api = "huabao_official_article_api"

    # 这些 PDF 是华宝官网公开的当前限额公告。每个份额代码独立记录，
    # 因为同一份公告往往同时列出 A/C 两个代码和代销/直销两个口径。
    official_notice_urls = {
        "008254": "https://www.fsfund.com/webimages/upload2012/2025/03/11/996336b3-c71b-4cc2-85ca-60135a20ed9b/华宝致远混合型证券投资基金（QDII）调整大额申购（含定投）金额上限的公告.pdf",
        "017204": "https://www.fsfund.com/webimages/upload2012/2025/09/29/6af2fb6f-cf7e-4b4d-8bef-3b13c8960206/华宝海外科技股票型证券投资基金（QDII-LOF）调整大额申购（含定投）金额上限的公告.pdf",
        "017437": "https://www.fsfund.com/static/notice/2026/02/09/be5ff786-dc58-411b-a250-20d2c47a101b/华宝纳斯达克精选股票型发起式证券投资基金（QDII）调整大额申购（含定投）金额上限的公告.pdf",
    }

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 25,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._catalogue_rows: Optional[Dict[str, Dict[str, str]]] = None

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
        match = re.search(r"([A-Z])(?:类)?(?:人民币|美元(?:现汇|现钞)?)?$", str(name or "").strip())
        return match.group(1) if match else ""

    @staticmethod
    def _normalize_date(value: Any) -> str:
        text = str(value or "").strip()
        match = re.search(r"(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})", text)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else text

    @classmethod
    def identity_from_config(cls, fund: Dict[str, Any]) -> FundIdentity:
        code = str(fund["code"])
        name = str(fund.get("name") or fund.get("display") or code)
        return FundIdentity(
            manager_id=cls.manager_id, code=code, name=name,
            share_class=cls._share_class(name),
            source_url=cls.product_url_template.format(code=code),
            source_type="config_compatibility",
        )

    @classmethod
    def _records_from_value(cls, value: str) -> List[Dict[str, str]]:
        """解析官网 fundMarketList 的 ``[{KEY=VALUE,...}, ...]`` 数据。"""
        result: List[Dict[str, str]] = []
        for body in re.findall(r"\{([^{}]*)\}", str(value or "")):
            item: Dict[str, str] = {}
            for pair in re.findall(r"([A-Za-z][A-Za-z0-9_]*)=([^,}]*)", body):
                item[pair[0]] = cls._clean(pair[1])
            if item:
                result.append(item)
        return result

    @classmethod
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        """解析基金超市 hidden input 中的全量基金/份额目录。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        node = soup.select_one("#fundMarketList")
        value = node.get("value", "") if node else ""
        found: Dict[str, FundIdentity] = {}
        for row in cls._records_from_value(value):
            code = cls._clean(row.get("FUNDCODE"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = cls._clean(row.get("SHORTNAME") or code)
            found[code] = FundIdentity(
                manager_id=cls.manager_id, code=code, name=name,
                fund_type=cls._clean(row.get("FUNDSTYLE")),
                share_class=cls._share_class(name),
                source_url=cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def _catalogue_rows_from_html(cls, html: bytes) -> Dict[str, Dict[str, str]]:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        node = soup.select_one("#fundMarketList")
        value = node.get("value", "") if node else ""
        return {
            cls._clean(row.get("FUNDCODE")): row
            for row in cls._records_from_value(value)
            if re.fullmatch(r"\d{6}", cls._clean(row.get("FUNDCODE")))
        }

    def discover_funds(self) -> List[FundIdentity]:
        response = self.session.get(self.catalogue_url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("华宝基金目录页需要认证")
        response.raise_for_status()
        self._catalogue_rows = self._catalogue_rows_from_html(response.content)
        funds = self.parse_catalogue_html(response.content, response.url or self.catalogue_url)
        if not funds:
            raise RuntimeError("华宝基金超市未找到 fundMarketList 全量目录")
        return funds

    @classmethod
    def _fields_from_html(cls, html: bytes) -> Dict[str, str]:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields: Dict[str, str] = {}
        for row in soup.find_all("tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            for index in range(0, len(cells) - 1, 2):
                key = cells[index].rstrip("：:").strip()
                if key:
                    fields[key] = cells[index + 1]
        return fields

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        """解析华宝产品页公开产品资料、净值和购买入口。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._fields_from_html(html)
        short = soup.select_one("#shortName")
        name = cls._clean(short.get("value") if short else "") or cls._clean(fields.get("基金简称")) or str(code)
        full_name = cls._clean(fields.get("基金全称"))
        fund_type = cls._clean(fields.get("基金类型"))
        risk = cls._clean(fields.get("风险等级"))
        inception = cls._normalize_date(fields.get("基金合同生效日"))
        fields["基金代码"] = str(code)
        fields["基金简称"] = name
        purchase = None
        for anchor in soup.find_all("a", href=True):
            text = cls._clean(anchor.get_text(" ", strip=True))
            if text in {"立即购买", "购买", "申购"}:
                purchase = anchor
                break
        purchase_open = purchase is not None and not str(purchase.get("href", "")).lower().startswith("javascript:")
        fields["购买按钮状态"] = "open" if purchase_open else "closed" if purchase is not None else "unknown"
        fields["定投按钮状态"] = "unknown"
        status = "购买可用" if purchase_open else "购买不可用" if purchase is not None else "官网产品页未公开当前交易状态"
        return ProductSnapshot(
            manager_id=cls.manager_id, code=str(code), name=name, full_name=full_name,
            fund_type=fund_type, risk_level=risk, inception_date=inception,
            asset_scale=fields.get("最新规模", ""), net_value_date=fields.get("最新净值日期", ""),
            trade_status=status, source_url=source_url, observed_at=observed_at, fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("华宝基金产品页需要认证")
        response.raise_for_status()
        return self.parse_product_html(response.content, fund.code, response.url or url, self._observed_at())

    @staticmethod
    def _status_bool(value: str) -> Optional[bool]:
        return True if value == "open" else False if value == "closed" else None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        raw = product.fields
        channel = ChannelTradeStatus(
            customer_type="individual", channel="华宝基金官网产品页公开购买入口",
            subscription=self._status_bool(raw.get("购买按钮状态", "")),
            redemption=None, sip=self._status_bool(raw.get("定投按钮状态", "")),
            quota_remark="交易状态来自华宝官网产品页；交易提交入口可能需要登录/验证码，适配器不访问。",
            raw=raw,
        )
        return TradeSnapshot(
            manager_id=self.manager_id, code=fund.code, channels=[channel], api_status=0,
            message=product.trade_status, source_url=product.source_url,
            observed_at=product.observed_at, raw=raw,
        )

    @classmethod
    def parse_announcement_text(cls, text: str, code: str, title: str = "", announcement_date: str = "", source_url: str = "") -> Optional[Dict[str, str]]:
        """从公告正文只取明确属于直销柜台/网上直销平台的金额。"""
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if code and code not in normalized:
            return None
        direct_segments = re.findall(r"(?:直销柜台及网上直销平台|网上直销平台|直销柜台|直销渠道)[^。；;]{0,260}", normalized)
        limit: Optional[str] = None
        remark = ""
        if direct_segments:
            amounts = re.findall(r"([\d,.]+)\s*(亿元|亿|万元|万|元)", direct_segments[-1])
            if amounts:
                amount_text = "".join(amounts[-1])
                limit = _amount(amount_text)
                remark = "华宝官网公告正文明确直销柜台及网上直销平台单日单户累计申购（含定投）金额上限。"
        if limit is None and re.search(r"暂停大额申购|暂停大额定期定额投资", cls._clean(title)):
            limit, remark = "暂停", "华宝官网公告明确暂停大额申购/定投。"
        if limit is None:
            return None
        return {
            "limit": limit, "status": "ok", "quota_type": "直销柜台及网上直销平台单日单户累计申购（含定投）上限",
            "quota_remark": remark, "announcement_title": cls._clean(title),
            "announcement_date": cls._normalize_date(announcement_date), "source_url": source_url,
        }

    def _fetch_notice(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        url = self.official_notice_urls.get(fund.code)
        if not url:
            return None
        response = self.session.get(url, headers=self._headers(self.home_url), timeout=max(self.timeout, 30))
        if response.status_code in {401, 403}:
            raise PermissionError("华宝基金限额公告 PDF 需要认证")
        response.raise_for_status()
        try:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
        except (PdfReadError, ValueError, OSError):
            return None
        parsed = self.parse_announcement_text(text, fund.code, url.rsplit("/", 1)[-1], "", url)
        if parsed:
            parsed["announcement_text"] = text
        return parsed

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        observed = self._observed_at()
        notice_url = self.official_notice_urls.get(fund.code)
        try:
            notice = self._fetch_notice(fund)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="华宝基金官网限额公告 PDF", limit=None,
                status="official_interface_requires_auth", quota_remark=str(exc),
                source_url=notice_url or self.product_url_template.format(code=fund.code),
                observed_at=observed, raw={"error": str(exc)},
            )
        if notice:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="华宝基金官网直销限额公告 PDF", limit=notice["limit"], status=notice["status"],
                quota_type=notice["quota_type"], quota_remark=notice["quota_remark"],
                source_url=notice["source_url"], observed_at=observed, raw=notice,
            )
        # 产品页匿名可访问，但官网公告 API 当前返回“网点代码不能为空”；
        # 对没有公开可解析公告的代码明确记录边界，不猜测为不限。
        return DirectLimitSnapshot(
            manager_id=self.manager_id, code=fund.code, customer_type="individual",
            channel="华宝基金官网公告查询 API", limit=None,
            status="official_interface_requires_auth" if not notice_url else "official_api_no_data",
            quota_type="直销申购/定投限额", quota_remark=(
                "官网公告查询接口要求网点代码，未登录不绕过；该代码暂未发现公开可解析限额公告。"
            ), source_url=self.article_api_url, observed_at=observed, raw={},
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(snapshot.limit or "未获取", snapshot.channel, snapshot.source_url, snapshot.quota_remark, status=snapshot.status)

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public_with_auth_announcement_boundary",
            reason="华宝基金目录和产品资料页可匿名访问；官网公告查询 API 当前要求网点代码，网上直销交易入口可能要求登录/验证码。适配器不绕过该认证边界。",
            requires_login=True,
        )

