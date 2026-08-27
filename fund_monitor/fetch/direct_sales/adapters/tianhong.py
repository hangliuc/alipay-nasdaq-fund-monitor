"""天弘基金官网独立 Adapter。

天弘官网前端公开声明了基金目录 API（``/thfund/fundlist/api``）和产品页
模板，但当前运行环境访问这些入口会收到阿里云 WAF JavaScript challenge。
适配器记录这一边界，不执行 challenge 计算、不伪造 Cookie、不绕过验证。

天弘产品页公告附件位于官方 CDN，仍可匿名读取。对已经发现的基金代码，
适配器从官方 CDN PDF 解析最新大额申购/定投公告；PDF 中明确的“直销机构”
规则优先于代销机构规则。
"""

from datetime import datetime, timezone
from io import BytesIO
import re
from typing import Any, Callable, Dict, List, Optional

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


class TianhongAdapter:
    """天弘官网目录边界、产品页和官方公告 CDN Adapter。"""

    manager_id = "天弘"
    home_url = "https://www.thfund.com.cn/"
    catalogue_url = "https://www.thfund.com.cn/fundlist"
    catalogue_api_url = "https://www.thfund.com.cn/thfund/fundlist/api"
    product_url_template = "https://www.thfund.com.cn/fundinfo/{code}"
    source_type_catalogue = "tianhong_official_fund_catalogue_api"
    source_type_product = "tianhong_official_product_page"
    source_type_notice = "tianhong_official_notice_cdn_pdf"
    # These are public attachments linked by天弘产品页公告；每次运行仍重新下载，
    # 不把历史值写死为结果。
    NOTICE_URLS = {
        "016665": "https://cdn-thweb.tianhongjijin.com.cn/fundnotice/7884d8fa6b0bf00ed0e6106ab693b556.pdf",
        "018044": "https://cdn-thweb.tianhongjijin.com.cn/fundnotice/574b5473d3485eeca8990102f6098ebd.pdf",
    }

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 25,
        clock: Optional[Callable[[], datetime]] = None,
        notice_urls: Optional[Dict[str, str]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.notice_urls = dict(notice_urls or self.NOTICE_URLS)

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
        match = re.search(r"([A-Z])$", str(name or "").strip())
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
            manager_id=cls.manager_id,
            code=code,
            name=name,
            share_class=cls._share_class(name),
            source_url=cls.product_url_template.format(code=code),
            source_type="config_compatibility",
        )

    @staticmethod
    def is_waf_challenge(content: bytes) -> bool:
        text = bytes(content or b"").decode("utf-8", errors="ignore")
        return "renderData" in text and ("acw_sc__v2" in text or "aliyun_waf_aa" in text)

    @classmethod
    def parse_catalogue_payload(cls, payload: Any, source_url: str = "") -> List[FundIdentity]:
        rows: Any = []
        if isinstance(payload, dict):
            rows = payload.get("fundDataInfo") or payload.get("data") or payload.get("fundListInfos") or []
        found: Dict[str, FundIdentity] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("fundCode") or row.get("fund_code") or "").strip()
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = cls._clean(row.get("fundName") or row.get("fund_name") or row.get("shortName") or code)
            found[code] = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=cls._clean(row.get("fundType") or row.get("fund_type")),
                share_class=cls._share_class(name),
                source_url=cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields: Dict[str, str] = {}
        for row in soup.find_all("tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            if len(cells) >= 2 and cells[0]:
                fields[cells[0]] = cells[1]
        title = cls._clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        name = cls._clean(soup.select_one("h1, .fund-name, .fundName").get_text(" ", strip=True)
                          if soup.select_one("h1, .fund-name, .fundName") else title) or str(code)
        page_text = cls._clean(soup.get_text(" ", strip=True))
        status_match = re.search(r"(?:交易状态|申购状态)\s*[:：]?\s*(正常|开放|暂停[^\s]*)", page_text)
        status = status_match.group(1) if status_match else fields.get("交易状态", "")
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=fields.get("基金名称", name),
            fund_type=fields.get("基金类型", ""),
            risk_level=fields.get("风险等级", ""),
            inception_date=cls._normalize_date(fields.get("成立日期") or fields.get("基金合同生效日")),
            asset_scale=fields.get("资产规模", ""),
            net_value_date=cls._normalize_date(fields.get("净值日期")),
            trade_status=status,
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def _get_public(self, url: str, referer: str = "") -> requests.Response:
        response = self.session.get(url, headers=self._headers(referer), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("天弘基金官网接口需要认证")
        response.raise_for_status()
        return response

    def discover_funds(self) -> List[FundIdentity]:
        response = self._get_public(
            f"{self.catalogue_api_url}?limit=any_limit&type=0", self.catalogue_url
        )
        if self.is_waf_challenge(response.content):
            raise PermissionError("天弘基金目录 API 当前返回阿里云 WAF challenge，未绕过验证")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("天弘基金目录 API 返回非 JSON") from exc
        funds = self.parse_catalogue_payload(payload, response.url or self.catalogue_api_url)
        if not funds:
            raise RuntimeError("天弘基金目录 API 未返回基金列表")
        return funds

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self._get_public(url, self.home_url)
        if self.is_waf_challenge(response.content):
            raise PermissionError("天弘基金产品页当前返回阿里云 WAF challenge，未绕过验证")
        return self.parse_product_html(response.content, fund.code, response.url or url, self._observed_at())

    @staticmethod
    def _status_bool(value: str) -> Optional[bool]:
        if re.search(r"正常|开放", value or ""):
            return True
        if re.search(r"暂停|停止", value or ""):
            return False
        return None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        status = self._status_bool(product.trade_status)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="天弘基金官网产品页",
            subscription=status,
            redemption=None,
            sip=status,
            quota_remark="产品页状态来自天弘官网；当前公开页若遇 WAF challenge 会明确返回边界。",
            raw=product.fields,
        )
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
    def parse_announcement_text(
        cls, text: str, code: str, title: str = "", announcement_date: str = "", source_url: str = ""
    ) -> Optional[Dict[str, str]]:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if str(code) not in normalized:
            return None
        title_text = cls._clean(title)
        direct = re.search(
            r"直销机构.*?(?:单日累计申购|单日.*?金额不得超过|金额限制).*?([\d,.]+)\s*(亿元|亿|万元|万|元)",
            normalized,
            re.S,
        )
        limit = _amount("".join(direct.groups())) if direct else None
        remark = ""
        if limit:
            remark = "天弘官网公告明确直销机构个人投资者单日累计申购/定投金额。"
        if limit is None and re.search(r"暂停申购(?:及定期定额投资)?|暂停大额申购", title_text + normalized):
            limit, remark = "暂停", "天弘官网公告明确该份额暂停申购/定期定额投资。"
        if limit is None and re.search(r"恢复申购|恢复大额申购", title_text + normalized):
            limit, remark = "不限", "天弘官网公告明确恢复申购/定期定额投资，未列金额上限。"
        if limit is None:
            return None
        return {
            "limit": limit,
            "status": "ok",
            "quota_type": "直销机构单个基金账户单日累计申购及定期定额投资",
            "quota_remark": remark,
            "announcement_title": title_text,
            "announcement_date": cls._normalize_date(announcement_date),
            "source_url": source_url,
        }

    def _fetch_notice(self, code: str) -> Optional[Dict[str, str]]:
        url = self.notice_urls.get(code)
        if not url:
            return None
        response = self._get_public(url, self.product_url_template.format(code=code))
        if not response.content.startswith(b"%PDF"):
            return None
        try:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
        except (PdfReadError, ValueError, OSError):
            return None
        # 标题和日期均从正文保留；日期正则同时支持中文年月日和空格。
        date_match = re.search(r"公告送出日期\s*[:：]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
        title = self._clean(text.splitlines()[0] if text.splitlines() else "天弘基金官网公告")
        return self.parse_announcement_text(text, code, title, date_match.group(1) if date_match else "", url)

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        try:
            notice = self._fetch_notice(fund.code)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="天弘基金官网公告 CDN PDF", limit=None,
                status="official_interface_requires_auth", quota_remark=str(exc),
                source_url=self.product_url_template.format(code=fund.code), observed_at=self._observed_at(),
                raw={"error": str(exc)},
            )
        if notice is None:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="天弘基金官网产品页公告 CDN PDF", limit=None,
                status="official_api_no_data",
                quota_remark="天弘产品页/公告目录当前受 WAF 或尚未登记该代码的官方 CDN 附件影响，未猜测限额。",
                source_url=self.product_url_template.format(code=fund.code), observed_at=self._observed_at(), raw={},
            )
        return DirectLimitSnapshot(
            manager_id=self.manager_id, code=fund.code, customer_type="individual",
            channel="天弘基金官网公告 CDN PDF", limit=notice["limit"], status=notice["status"],
            quota_type=notice["quota_type"], quota_remark=notice["quota_remark"],
            source_url=notice["source_url"], observed_at=self._observed_at(), raw=notice,
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(snapshot.limit or "未获取", "天弘基金官网产品页公告 CDN PDF", snapshot.source_url,
                       snapshot.quota_remark, status=snapshot.status)

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public_with_waf_boundary",
            reason="天弘目录 API 和产品页公开入口当前返回阿里云 WAF JavaScript challenge；Adapter 不绕过 challenge。官方 CDN 公告 PDF 可匿名读取。",
        )
