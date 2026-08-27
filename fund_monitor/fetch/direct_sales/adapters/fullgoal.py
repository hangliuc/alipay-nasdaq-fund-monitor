"""富国基金官网独立 Adapter。

富国官网公开了基金目录查询接口、单只产品页和产品页公告列表。目录接口
``/ws-business-server/fund/getFundList`` 返回当前公开基金份额及净值摘要；
产品页返回基金资料、净值和购买/定投按钮状态；限额则从产品页按时间倒序
列出的最新“大额申购/定投”公告附件 PDF 中解析。

本模块只访问富国官方域名，不登录、不提交交易、不访问购买链接，也不使用
第三方平台或旧公告值替代当前结果。出现 401/403 时明确返回认证边界。
"""

from datetime import datetime, timezone
from io import BytesIO
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
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


class FullgoalAdapter:
    """富国官网基金目录、产品页和限额公告 Adapter。"""

    manager_id = "富国"
    home_url = "https://www.fullgoal.com.cn/"
    catalogue_url = "https://www.fullgoal.com.cn/main/fund/index.html"
    catalogue_api_url = "https://www.fullgoal.com.cn/ws-business-server/fund/getFundList"
    product_url_template = "https://www.fullgoal.com.cn/fundDetail/{code}/index.html"
    notice_detail_url_template = "https://www.fullgoal.com.cn/noticedetails/{notice_id}/index.html"
    source_type_catalogue = "fullgoal_official_fund_catalogue_api"
    source_type_product = "fullgoal_official_product_page"
    source_type_notice = "fullgoal_official_notice_pdf"

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
        return {**HEADERS, "Referer": referer or cls.home_url}

    @staticmethod
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").split())

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
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
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
    def catalogue_params(cls, page_num: int = 1, page_size: int = 1000) -> Dict[str, Any]:
        """返回富国官网前端 ``getFundList`` 使用的公开查询参数。"""
        return {
            "siteno": "main",
            "merchantId": "",
            "keyword": "",
            "mangerNos": "",
            "productColumns": "",
            "productTypes": "",
            "productTypes2": "",
            "riskcodes": "",
            "fundSizeTypes": "",
            "estYears": "",
            "yilds": "",
            "transactionStatus": "",
            "oderbyStatus": "",
            "performanceStatus": "",
            "isHb": "0",
            "htmlType": "2",
            "pageNum": page_num,
            "pageSize": page_size,
        }

    @classmethod
    def parse_catalogue_payload(cls, payload: Dict[str, Any], source_url: str = "") -> List[FundIdentity]:
        """解析 ``getFundList`` JSON，保留官网当前公开基金/份额目录。"""
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            rows = data.get("list") or []
        else:
            rows = payload.get("list") if isinstance(payload, dict) else []
        found: Dict[str, FundIdentity] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("productCode") or row.get("fundCode") or "").strip()
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = cls._clean(row.get("productAbbr") or row.get("productName") or code)
            identity = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=cls._clean(row.get("productTypeText") or row.get("productType")),
                share_class=cls._share_class(name),
                source_url=cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
            found[code] = identity
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        """HTML 兜底解析；官网 SSR 页可能只渲染一页，优先使用 JSON API。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        found: Dict[str, FundIdentity] = {}
        for anchor in soup.find_all("a", href=True):
            match = re.search(r"/fundDetail/(\d{6})/index\.html", str(anchor.get("href")))
            if not match:
                continue
            code = match.group(1)
            name_node = anchor.select_one(".pub_title_span") or anchor
            name = cls._clean(name_node.get_text(" ", strip=True))
            if not name:
                continue
            row_text = cls._clean(anchor.parent.get_text(" ", strip=True))
            risk_match = re.search(r"(主动股票型|股票指数型|混合型|债券型|FOF|QDII|货币型|商品|REITs)", row_text)
            found[code] = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=risk_match.group(1) if risk_match else "",
                share_class=cls._share_class(name),
                source_url=urljoin(source_url or cls.catalogue_url, str(anchor.get("href"))),
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    def discover_funds(self) -> List[FundIdentity]:
        """通过官网公开 JSON 目录分页发现当前公开的全部基金份额。"""
        page_size = 1000
        found: Dict[str, FundIdentity] = {}
        page_num = 1
        while True:
            response = self.session.get(
                self.catalogue_api_url,
                params=self.catalogue_params(page_num, page_size),
                headers=self._headers(self.catalogue_url),
                timeout=self.timeout,
            )
            if response.status_code in {401, 403}:
                raise PermissionError("富国基金目录接口需要认证")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or str(payload.get("code")) not in {"0", "0.0"}:
                message = payload.get("msg") if isinstance(payload, dict) else ""
                raise RuntimeError(str(message or "富国基金目录接口失败"))
            for fund in self.parse_catalogue_payload(payload, response.url or self.catalogue_api_url):
                found[fund.code] = fund
            data = payload.get("data") or {}
            total = int(data.get("total") or len(found)) if isinstance(data, dict) else len(found)
            pages = int(data.get("pages") or 1) if isinstance(data, dict) else 1
            if page_num >= pages or len(found) >= total or not (data.get("list") if isinstance(data, dict) else None):
                break
            page_num += 1
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def _label_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for row in soup.find_all("tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            for index in range(0, len(cells) - 1, 2):
                if cells[index]:
                    fields[cells[index]] = cells[index + 1]
        return fields

    @classmethod
    def _button_status(cls, node: Any) -> str:
        if node is None:
            return ""
        classes = set(node.get("class") or [])
        if "disabled" in classes:
            return "closed"
        href = str(node.get("href") or "")
        return "open" if href and not href.lower().startswith("javascript:") else "closed"

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._label_fields(soup)
        name = cls._clean((soup.select_one("#fundAbbr") or {}).get_text(" ", strip=True) if soup.select_one("#fundAbbr") else "") or str(code)
        full_name = cls._clean((soup.select_one("#fundName") or {}).get_text(" ", strip=True) if soup.select_one("#fundName") else "")
        fund_type_node = soup.select_one(".fund_tag_01")
        risk_node = soup.select_one(".fund_tag_02")
        fund_type = cls._clean(fund_type_node.get_text(" ", strip=True) if fund_type_node else fields.get("基金类型"))
        risk = cls._clean(risk_node.get_text(" ", strip=True) if risk_node else fields.get("基金风险等级"))
        inception = cls._normalize_date(fields.get("基金份额生效日") or fields.get("基金合同生效日") or fields.get("成立日期"))
        nav_node = soup.select_one(".item .num strong")
        nav = cls._clean(nav_node.get_text(" ", strip=True) if nav_node else "")
        text = cls._clean(soup.get_text(" ", strip=True))
        nav_date_match = re.search(r"单位净值\((\d{4}-\d{2}-\d{2})\)", text)
        nav_date = nav_date_match.group(1) if nav_date_match else ""
        if nav:
            fields["最新净值"] = nav
        if nav_date:
            fields["最新净值日期"] = nav_date
        fields["基金代码"] = str(code)
        buy_status = cls._button_status(soup.select_one(".btn_wrap .btn_1"))
        sip_status = cls._button_status(soup.select_one(".btn_wrap .btn_2"))
        fields["购买按钮状态"] = buy_status
        fields["定投按钮状态"] = sip_status
        status_parts = []
        if buy_status:
            status_parts.append("购买可用" if buy_status == "open" else "购买不可用")
        if sip_status:
            status_parts.append("定投可用" if sip_status == "open" else "定投不可用")
        trade_status = "；".join(status_parts) or "官网产品页未公开当前交易状态"
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=fund_type,
            risk_level=risk,
            inception_date=inception,
            asset_scale=fields.get("资产规模", ""),
            net_value_date=nav_date,
            trade_status=trade_status,
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def _fetch_product_html(self, code: str) -> Tuple[bytes, str]:
        url = self.product_url_template.format(code=code)
        response = self.session.get(url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("富国基金产品页需要认证")
        response.raise_for_status()
        return response.content, response.url or url

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        content, source_url = self._fetch_product_html(fund.code)
        return self.parse_product_html(content, fund.code, source_url, self._observed_at())

    @classmethod
    def _status_bool(cls, value: str) -> Optional[bool]:
        if value == "open":
            return True
        if value == "closed":
            return False
        return None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="富国官网产品页",
            subscription=self._status_bool(product.fields.get("购买按钮状态", "")),
            redemption=None,
            sip=self._status_bool(product.fields.get("定投按钮状态", "")),
            quota_remark="购买/定投状态来自产品页公开按钮状态；未访问需要登录的交易入口，直销限额来自官方公告 PDF。",
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
    def parse_notice_list_html(cls, html: bytes, source_url: str) -> List[Tuple[str, str, str]]:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        candidates: List[Tuple[str, str, str]] = []
        for item in soup.select("ul.notice_list li"):
            anchor = item.find("a", href=True)
            if anchor is None:
                continue
            title_node = item.find("p")
            title = cls._clean(title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True))
            if not any(word in title for word in ("大额申购", "大额定投", "定期定额投资")):
                continue
            if not any(word in title for word in ("暂停", "调整", "恢复")):
                continue
            date_node = item.select_one(".time")
            date_text = cls._clean(date_node.get_text(" ", strip=True) if date_node else "")
            candidates.append((title, date_text, urljoin(source_url, str(anchor.get("href")))))
        return candidates

    def _notice_candidates(self, code: str, product_html: Optional[bytes] = None, product_url: str = "") -> List[Tuple[str, str, str]]:
        if product_html is None:
            product_html, product_url = self._fetch_product_html(code)
        return self.parse_notice_list_html(product_html, product_url or self.product_url_template.format(code=code))

    @classmethod
    def _format_amount(cls, number: str, unit: str = "元") -> Optional[str]:
        return _amount(f"{number}{unit}") if unit == "元" else f"{float(str(number).replace(',', '')):g}{unit}"

    @classmethod
    def parse_announcement_text(
        cls,
        text: str,
        code: str,
        title: str = "",
        announcement_date: str = "",
        source_url: str = "",
    ) -> Optional[Dict[str, str]]:
        """按公告的份额代码列与限制申购金额列对齐解析目标份额。"""
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        title_text = cls._clean(title)
        code_match = re.search(r"下属分级基金的交易代码\s*((?:\d{6}\s*)+)", normalized)
        amount_match = re.search(
            r"下属分级基金的限制申购金额\s*[（(]?单位[：:]\s*(元|美元)[）)]?\s*((?:[\d,.]+\s*)+?)(?=\s*下属分级基金的限制定期)",
            normalized,
        )
        limit: Optional[str] = None
        remark = ""
        if code_match and amount_match:
            codes = re.findall(r"\d{6}", code_match.group(1))
            amounts = re.findall(r"[\d,.]+", amount_match.group(2))
            if str(code) in codes:
                index = codes.index(str(code))
                if index < len(amounts):
                    unit = amount_match.group(1)
                    limit = cls._format_amount(amounts[index], unit)
                    remark = "富国官网公告按下属分级基金交易代码列出限制申购金额；未单列渠道，按官网一手来源归入直销结果。"
        if limit is None:
            generic = re.search(r"(?:日累计金额|限制申购金额)[^\d]{0,80}([\d,.]+)\s*(元|美元)", normalized)
            if generic:
                limit = cls._format_amount(generic.group(1), generic.group(2))
                remark = "公告未单列目标份额表格，按公告明确的日累计申购金额归入直销结果。"
        if limit is None and ("恢复大额申购" in title_text or "恢复大额申购" in normalized):
            limit, remark = "不限", "公告明确恢复大额申购/定投业务，未列出金额上限。"
        if limit is None and ("暂停大额申购" in title_text or "暂停大额申购" in normalized):
            limit, remark = "暂停", "公告明确暂停大额申购/定投业务，但未解析到金额上限。"
        if limit is None:
            return None
        return {
            "limit": limit,
            "status": "ok",
            "quota_type": "单日单个基金账户累计申购及定期定额投资",
            "quota_remark": remark or "富国官网限额公告。",
            "announcement_title": title_text,
            "announcement_date": cls._normalize_date(announcement_date),
            "source_url": source_url,
        }

    def _fetch_notice(self, code: str, product_html: Optional[bytes] = None, product_url: str = "") -> Optional[Dict[str, str]]:
        for title, announcement_date, detail_url in self._notice_candidates(code, product_html, product_url):
            response = self.session.get(detail_url, headers=self._headers(product_url or self.home_url), timeout=self.timeout)
            if response.status_code in {401, 403}:
                raise PermissionError("富国基金公告详情需要认证")
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            pdf_url = ""
            for anchor in soup.find_all("a", href=True):
                href = str(anchor.get("href") or "")
                if re.search(r"\.pdf(?:$|[?#])", href, re.I):
                    pdf_url = urljoin(detail_url, href)
                    break
            if not pdf_url:
                match = re.search(r"(?:href=[\"'])([^\"']+\.pdf(?:\?[^\"']*)?)", response.content.decode("utf-8", errors="ignore"), re.I)
                if match:
                    pdf_url = urljoin(detail_url, match.group(1))
            if not pdf_url:
                continue
            pdf_response = self.session.get(pdf_url, headers=self._headers(detail_url), timeout=max(self.timeout, 25))
            if pdf_response.status_code in {401, 403}:
                raise PermissionError("富国基金公告 PDF 需要认证")
            pdf_response.raise_for_status()
            try:
                text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf_response.content)).pages)
            except (PdfReadError, ValueError, OSError):
                continue
            parsed = self.parse_announcement_text(text, code, title, announcement_date, pdf_url)
            if parsed:
                parsed["announcement_text"] = text
                return parsed
        return None

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        try:
            product_html, product_url = self._fetch_product_html(fund.code)
            product = self.parse_product_html(product_html, fund.code, product_url, self._observed_at())
            notice = self._fetch_notice(fund.code, product_html, product_url)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="富国官网产品页/公告 PDF",
                limit=None,
                status="official_interface_requires_auth",
                quota_remark=str(exc),
                source_url=self.product_url_template.format(code=fund.code),
                observed_at=self._observed_at(),
                raw={"error": str(exc)},
            )
        if notice is None:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="富国官网产品页/公告 PDF",
                limit=None,
                status="official_api_no_data",
                quota_remark="产品页可访问，但未解析到最新可确认的大额申购/定投公告；不沿用历史值。",
                source_url=product.source_url,
                observed_at=product.observed_at,
                raw=product.fields,
            )
        raw = dict(product.fields)
        raw.update(notice)
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="富国官网产品页公告 PDF",
            limit=notice["limit"],
            status=notice["status"],
            quota_type=notice["quota_type"],
            quota_remark=notice["quota_remark"],
            source_url=notice["source_url"],
            observed_at=product.observed_at,
            raw=raw,
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(
            snapshot.limit or "未获取",
            "富国基金官网产品页最新公告 PDF",
            snapshot.source_url,
            snapshot.quota_remark,
            status=snapshot.status,
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="富国基金目录 API、产品页、公告列表、公告详情和 PDF 均可匿名访问；未访问登录交易入口，不绕过登录、验证码、设备签名或银行卡绑定。",
        )
