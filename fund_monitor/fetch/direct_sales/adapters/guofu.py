"""国富基金官网基金超市、产品页和信息披露 Adapter。

国富官网基金超市按基金类型提供公开 HTML 列表；单只产品页公开基金资料和
当前交易状态；产品页的 ``xxplload`` 接口公开最新信息披露 PDF。Adapter 只读
这些官方页面和附件，不登录、不提交交易，也不使用历史核验值替代当前结果。
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


class GuofuAdapter:
    """国富官网基金目录、产品页交易状态和限额公告 Adapter。"""

    manager_id = "国富"
    home_url = "https://www.ftsfund.com/"
    catalogue_api_url = "https://www.ftsfund.com/index/jjcs"
    product_url_template = "https://www.ftsfund.com/qxjj/jjxq/{code}"
    disclosure_url_template = "https://www.ftsfund.com/qxjj/jjxq/xxplload?fundCode={code}&page={page}"
    source_type_catalogue = "guofu_official_fund_supermarket"
    source_type_product = "guofu_official_product_page"
    source_type_notice = "guofu_official_product_notice_pdf"
    category_types = {
        "201008": "QDII",
        "201005": "货币型",
        "201004": "债券型",
        "201001": "指数型",
        "201003": "混合型",
        "201007": "股票型",
        "201009": "FOF",
    }

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._catalogue: Optional[Dict[str, FundIdentity]] = None

    def _observed_at(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def _headers(cls, referer: str = "") -> Dict[str, str]:
        return {**HEADERS, "Referer": referer or cls.home_url}

    @staticmethod
    def _share_class(name: str) -> str:
        match = re.search(r"([A-Z])$", str(name or "").strip())
        return match.group(1) if match else ""

    @staticmethod
    def _normalize_date(value: Any) -> str:
        text = str(value or "").strip()
        match = re.fullmatch(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", text)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        return text

    @staticmethod
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").split())

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
    def parse_catalogue_html(
        cls,
        html: bytes,
        category_type: str = "",
        source_url: str = "",
    ) -> List[FundIdentity]:
        """解析一个基金超市分类接口的 HTML 表格。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        found: Dict[str, FundIdentity] = {}
        category_name = cls.category_types.get(str(category_type), "")
        for row in soup.find_all("tr"):
            anchor = row.find("a", href=re.compile(r"/qxjj/jjxq/\d{6}"))
            if anchor is None:
                continue
            href = str(anchor.get("href") or "")
            match = re.search(r"/qxjj/jjxq/(\d{6})", href)
            if not match:
                continue
            code = match.group(1)
            name = cls._clean(anchor.get_text(" ", strip=True))
            if not name:
                continue
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            identity = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=category_name,
                share_class=cls._share_class(name),
                source_url=urljoin(source_url or cls.home_url, href),
                source_type=cls.source_type_catalogue,
            )
            old = found.get(code)
            if old is None or len(identity.name) > len(old.name) or (not old.fund_type and identity.fund_type):
                found[code] = identity
        return sorted(found.values(), key=lambda item: item.code)

    def _fetch_catalogue_category(self, category_type: str) -> List[FundIdentity]:
        params = {
            "fund_type": category_type,
            "search_value": "",
            "sortField": "",
            "sortMode": "",
        }
        response = self.session.get(
            self.catalogue_api_url,
            params=params,
            headers=self._headers(self.home_url),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("国富基金超市需要认证")
        response.raise_for_status()
        return self.parse_catalogue_html(response.content, category_type, response.url or self.catalogue_api_url)

    def discover_funds(self) -> List[FundIdentity]:
        """合并官网基金超市各分类，发现当前公开基金及份额。"""
        found: Dict[str, FundIdentity] = {}
        for category_type in self.category_types:
            for fund in self._fetch_catalogue_category(category_type):
                old = found.get(fund.code)
                if old is None or len(fund.name) > len(old.name) or (not old.fund_type and fund.fund_type):
                    found[fund.code] = fund
        self._catalogue = found
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def _label_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        known = {
            "基金全称", "基金简称", "基金类型", "基金代码", "基金经理", "成立时间",
            "管理人", "交易状态", "托管人", "投资目标", "投资范围", "业绩比较基准",
        }
        for row in soup.find_all("tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            for index in range(0, len(cells) - 1, 2):
                key = cells[index]
                value = cells[index + 1]
                if key in known:
                    fields[key] = value
        return fields

    @classmethod
    def parse_product_html(
        cls,
        html: bytes,
        code: str,
        source_url: str,
        observed_at: str,
    ) -> ProductSnapshot:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        text = cls._clean(soup.get_text(" ", strip=True))
        fields = cls._label_fields(soup)
        code_match = re.search(r"([^\s]+)\s*\(%s\)" % re.escape(code), text)
        name = fields.get("基金简称") or (code_match.group(1) if code_match else code)
        full_name = fields.get("基金全称", "")
        risk_match = re.search(r"风险等级\s*[：:]\s*([^\s]+风险)", text)
        risk = risk_match.group(1) if risk_match else ""
        date_match = re.search(r"成立日期\s*[：:]\s*(\d{4}-\d{2}-\d{2})", text)
        nav_node = soup.select_one("#jijin")
        nav = cls._clean(nav_node.get_text(" ", strip=True)) if nav_node else ""
        if nav:
            fields["最新净值"] = nav
        if risk:
            fields["风险等级"] = risk
        if date_match:
            fields["成立日期"] = date_match.group(1)
        fields["基金代码"] = str(code)
        fields["产品页文本交易状态"] = fields.get("交易状态", "")
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=fields.get("基金类型", ""),
            risk_level=risk,
            inception_date=cls._normalize_date(fields.get("成立时间") or fields.get("成立日期")),
            asset_scale="",
            net_value_date="",
            trade_status=fields.get("交易状态", "") or "未知",
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("国富基金产品页需要认证")
        response.raise_for_status()
        return self.parse_product_html(response.content, fund.code, response.url or url, self._observed_at())

    @staticmethod
    def _subscription_status(text: str) -> Optional[bool]:
        if any(token in text for token in ("暂停申购", "暂停交易", "基金终止")):
            return False
        if any(token in text for token in ("正常开放", "开放申购")):
            return True
        return None

    @staticmethod
    def _redemption_status(text: str) -> Optional[bool]:
        if any(token in text for token in ("暂停赎回", "暂停交易", "基金终止")):
            return False
        if any(token in text for token in ("正常开放", "暂停申购", "开放赎回")):
            return True
        return None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="国富官网产品页",
            subscription=self._subscription_status(product.trade_status),
            redemption=self._redemption_status(product.trade_status),
            quota_remark="产品页公开当前交易状态；限额需读取产品页信息披露公告。",
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

    @staticmethod
    def _format_amount(number: str, unit: str) -> Optional[str]:
        if unit == "元":
            return _amount(f"{number}元")
        try:
            value = float(str(number).replace(",", ""))
        except (TypeError, ValueError):
            return None
        return f"{int(value) if value.is_integer() else value:g}{unit}"

    @classmethod
    def parse_announcement_text(
        cls,
        text: str,
        code: str,
        title: str = "",
        announcement_date: str = "",
        source_url: str = "",
    ) -> Optional[Dict[str, str]]:
        """解析国富限额公告；直销金额优先于非直销/通用金额。"""
        normalized = re.sub(r"[ \t\r\f\v]+", " ", str(text or ""))
        title_text = cls._clean(title)
        target = normalized
        direct_match = re.search(
            r"通过直销机构[^。]{0,1000}?(?:等于或低于|不超过|高于)\s*([\d,.]+)\s*(元|美元)",
            target,
            re.S,
        )
        limit: Optional[str] = None
        quota_remark = ""
        if direct_match:
            limit = cls._format_amount(direct_match.group(1), direct_match.group(2))
            quota_remark = "公告明确区分直销机构，优先采用直销限额。"
        else:
            code_match = re.search(r"下属分级基金的交易代码\s*((?:\d{6}\s*)+)", normalized, re.S)
            amount_match = re.search(
                r"下属分级基金的限制申购金额\s*((?:(?:[\d,.]+)\s*(?:元|美元)\s*)+)",
                normalized,
                re.S,
            )
            if code_match and amount_match:
                codes = re.findall(r"\d{6}", code_match.group(1))
                amounts = re.findall(r"[\d,.]+\s*(?:元|美元)", amount_match.group(1))
                if str(code) in codes:
                    index = codes.index(str(code))
                    if index < len(amounts):
                        amount_parts = re.match(r"([\d,.]+)\s*(元|美元)", amounts[index])
                        if amount_parts:
                            limit = cls._format_amount(amount_parts.group(1), amount_parts.group(2))
                            quota_remark = "公告按下属分级基金交易代码列出限制申购金额。"
            if limit is None:
                generic = re.search(r"限制申购金额\s*([\d,.]+)\s*(元|美元)", normalized, re.S)
                if generic:
                    limit = cls._format_amount(generic.group(1), generic.group(2))
                    quota_remark = "公告未单列直销渠道，按官网一手公告口径归入直销结果。"
        if limit is None and ("恢复大额申购" in title_text or "恢复大额申购" in normalized):
            limit = "不限"
            quota_remark = "公告明确恢复大额申购/定投业务，未列出金额上限。"
        if limit is None and ("暂停大额申购" in title_text or "暂停大额申购" in normalized):
            limit = "暂停"
            quota_remark = "公告明确暂停大额申购/定投业务，未列出金额上限。"
        if limit is None:
            return None
        return {
            "limit": limit,
            "status": "ok",
            "quota_type": "单日每个基金账户累计申购及定期定额投资",
            "quota_remark": quota_remark or "国富官网限额公告。",
            "announcement_title": title_text,
            "announcement_date": cls._normalize_date(announcement_date),
            "source_url": source_url,
        }

    def _notice_candidates(self, code: str) -> List[Tuple[str, str, str]]:
        list_url = self.disclosure_url_template.format(code=code, page=1)
        response = self.session.get(list_url, headers=self._headers(self.product_url_template.format(code=code)), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("国富基金信息披露需要认证")
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        candidates: List[Tuple[str, str, str]] = []
        for item in soup.select("li.news_item"):
            anchor = item.find("a", href=True)
            if anchor is None:
                continue
            title_node = item.select_one(".title")
            date_node = item.select_one(".date")
            title = self._clean(title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True))
            if "大额申购" not in title and "大额定投" not in title:
                continue
            if not any(word in title for word in ("暂停", "调整", "恢复")):
                continue
            date_text = self._clean(date_node.get_text(" ", strip=True) if date_node else "")
            candidates.append((title, date_text, urljoin(list_url, str(anchor.get("href")))))
        return candidates

    def _fetch_notice(self, code: str) -> Optional[Dict[str, str]]:
        for title, announcement_date, pdf_url in self._notice_candidates(code):
            response = self.session.get(pdf_url, headers=self._headers(self.product_url_template.format(code=code)), timeout=max(self.timeout, 25))
            if response.status_code in {401, 403}:
                raise PermissionError("国富基金公告附件需要认证")
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                continue
            try:
                text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
            except (PdfReadError, ValueError, OSError):
                continue
            parsed = self.parse_announcement_text(text, code, title, announcement_date, pdf_url)
            if parsed:
                parsed["announcement_text"] = text
                return parsed
        return None

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        try:
            product = self.fetch_product(fund)
            notice = self._fetch_notice(fund.code)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="国富官网产品页/信息披露",
                limit=None,
                status="official_interface_requires_auth",
                quota_remark=str(exc),
                source_url=self.product_url_template.format(code=fund.code),
                observed_at=self._observed_at(),
                raw={"error": str(exc)},
            )
        if notice is None:
            paused = self._subscription_status(product.trade_status) is False
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="国富官网产品页/信息披露",
                limit="暂停" if paused else None,
                status="ok" if paused else "official_api_no_data",
                quota_remark="产品页交易状态为暂停申购，但信息披露列表未解析到金额公告。" if paused else "产品页可访问，但未解析到最新大额申购公告。",
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
            channel="国富官网产品页信息披露公告",
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
            "国富基金官网产品页信息披露公告 PDF",
            snapshot.source_url,
            snapshot.quota_remark,
            status=snapshot.status,
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="国富基金超市、产品页和信息披露接口可匿名访问；公告附件为官网公开 PDF。未观察到登录、验证码、设备签名或银行卡绑定要求。",
        )
