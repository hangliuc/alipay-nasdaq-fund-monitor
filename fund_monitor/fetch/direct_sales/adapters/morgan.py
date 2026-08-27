"""摩根基金（cifm.com）官方公开 Adapter。

摩根基金官网基金超市 ``/fund/`` 公开列出当前基金/份额，单只产品页公开
基金资料、购买入口和最新公告；官网搜索页可按产品名称检索完整公告列表，
公告附件通常是 PDF。直销限额优先选择标题或正文明确写出“直销渠道”的
最新限额公告，再回退到该产品最新的通用限额公告。

本模块只访问 ``cifm.com`` 及官网公开的基金电子交易入口，不登录、不提交
交易、不伪造 ``tk-trans-signature``、不绕过验证码/设备签名/银行卡绑定，也
不使用第三方平台。官网图表使用的 ``ecmob.cifm.com`` 接口要求签名，因而
不在匿名 Adapter 中调用；产品页和官网静态净值 XML 足以提供公开快照。
"""

from datetime import datetime, timezone
from io import BytesIO
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin
from xml.etree import ElementTree

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


class MorganAdapter:
    """摩根基金官网基金目录、产品页、公告和直销限额 Adapter。"""

    manager_id = "摩根"
    home_url = "https://www.cifm.com/"
    catalogue_url = "https://www.cifm.com/fund/"
    product_url_template = "https://www.cifm.com/fund/{code}/"
    search_url = "https://www.cifm.com/web/search"
    nav_xml_url = "https://www.cifm.com/images/week/net_value_week.xml"
    app_api_url = "https://ecmob.cifm.com/mall-data-server/fundinfo/queryFundDetail"
    trade_entry_url = "https://etrade.51fund.com/etrading/account/login/init"
    source_type_catalogue = "morgan_official_fund_catalogue_html"
    source_type_product = "morgan_official_product_page"
    source_type_nav = "morgan_official_net_value_xml"
    source_type_notice = "morgan_official_notice_pdf"
    source_type_search = "morgan_official_notice_search"

    relevant_notice_terms = (
        "大额申购",
        "限制申购",
        "暂停申购",
        "恢复申购",
        "定期定额投资",
        "定期定额",
        "直销渠道",
    )

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 25,
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
        return " ".join(str(value or "").replace("\xa0", " ").split())

    @staticmethod
    def _normalize_date(value: Any) -> str:
        text = str(value or "").strip()
        match = re.search(r"(\d{4})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})", text)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        return text

    @staticmethod
    def _share_class(name: str) -> str:
        match = re.search(r"([A-Z])(?:类)?$", str(name or "").strip())
        return match.group(1) if match else ""

    @classmethod
    def _code_pattern(cls) -> str:
        # 370010B 是摩根货币 B 份额的官网特殊代码，其余通常为六位数字。
        return r"\d{6}[A-Z]?"

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
        """解析官网基金超市全部产品/份额链接并按代码去重。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        found: Dict[str, FundIdentity] = {}
        # Preserve links with query/fragment parameters if the CMS adds
        # tracking parameters to its otherwise stable product URLs.
        pattern = re.compile(rf"/fund/({cls._code_pattern()})/?(?:[?#].*)?$", re.I)
        for anchor in soup.find_all("a", href=True):
            match = pattern.search(str(anchor.get("href") or ""))
            if not match:
                continue
            code = match.group(1)
            title = anchor.select_one(".item_title") or anchor.find(["strong", "b"])
            name = cls._clean(title.get_text(" ", strip=True) if title else anchor.get_text(" ", strip=True))
            if not name or name == code:
                continue
            row_text = cls._clean(anchor.parent.get_text(" ", strip=True))
            fund_type_match = re.search(r"(QDII|ETF|FOF|股票型|混合型|债券型|货币型|指数型|REITs)", row_text)
            found[code] = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=fund_type_match.group(1) if fund_type_match else "",
                share_class=cls._share_class(name),
                source_url=urljoin(source_url or cls.catalogue_url, str(anchor.get("href"))),
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    def discover_funds(self) -> List[FundIdentity]:
        response = self.session.get(
            self.catalogue_url,
            headers=self._headers(self.home_url),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("摩根基金超市目录页需要认证")
        response.raise_for_status()
        funds = self.parse_catalogue_html(response.content, response.url or self.catalogue_url)
        if not funds:
            raise RuntimeError("摩根基金超市未找到公开基金目录")
        return funds

    @classmethod
    def _label_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for row in soup.find_all("tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            for index in range(0, len(cells) - 1, 2):
                key = cells[index].rstrip("：:").strip()
                if key:
                    fields[key] = cells[index + 1]
        return fields

    @classmethod
    def _page_variables(cls, html: bytes) -> Dict[str, str]:
        text = bytes(html or b"").decode("utf-8", errors="ignore")
        values: Dict[str, str] = {}
        for key, pattern in {
            "currentFundCode": r"currentFundCode\s*=\s*['\"]([^'\"]+)",
            "currentFundName": r"currentFundName\s*=\s*['\"]([^'\"]+)",
            "clrq": r"clrq\s*=\s*['\"]([^'\"]*)",
        }.items():
            match = re.search(pattern, text)
            if match:
                values[key] = cls._clean(match.group(1))
        return values

    @classmethod
    def parse_nav_xml(cls, xml: bytes, code: str) -> Dict[str, str]:
        """从官网静态净值 XML 中取当前代码最新记录。"""
        try:
            root = ElementTree.fromstring(bytes(xml or b""))
        except (ElementTree.ParseError, ValueError):
            return {}
        rows: List[Dict[str, str]] = []
        for node in root.iter():
            # Accept namespace and lowercase variants from older static files.
            if str(node.tag).rsplit("}", 1)[-1].lower() != "fund":
                continue
            attrs = {str(k).rsplit("}", 1)[-1]: str(v) for k, v in node.attrib.items()}
            lowered = {key.lower(): value for key, value in attrs.items()}
            row_code = lowered.get("fundcode", "")
            if row_code != code and not (
                code in {"370010A", "370010B"}
                and row_code == "370010"
                and lowered.get("fundname", "").endswith(code[-1])
            ):
                continue
            # Keep canonical keys used by the rest of this adapter while
            # retaining any additional fields published in the XML row.
            canonical = {
                "FundCode": lowered.get("fundcode", ""),
                "FundDate": lowered.get("funddate", ""),
                "NetValue": lowered.get("netvalue", ""),
                "TotalNetValue": lowered.get("totalnetvalue", ""),
                "FundType": lowered.get("fundtype", ""),
                "FundState": lowered.get("fundstate", ""),
                "FundName": lowered.get("fundname", ""),
            }
            canonical.update({key: value for key, value in attrs.items() if key.lower() not in {
                "fundcode", "funddate", "netvalue", "totalnetvalue", "fundtype", "fundstate", "fundname"
            }})
            rows.append(canonical)
        if not rows:
            return {}
        return max(rows, key=lambda row: row.get("FundDate", ""))

    @classmethod
    def parse_product_html(
        cls,
        html: bytes,
        code: str,
        source_url: str,
        observed_at: str,
        nav: Optional[Dict[str, str]] = None,
    ) -> ProductSnapshot:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._label_fields(soup)
        variables = cls._page_variables(html)
        fields.update({f"官网变量.{key}": value for key, value in variables.items()})
        name = variables.get("currentFundName") or fields.get("基金简称") or str(code)
        full_name = fields.get("法定名称") or fields.get("基金全称", "")
        fund_type = fields.get("基金类型") or fields.get("类型", "")
        risk = fields.get("风险等级") or fields.get("风险评级", "")
        inception = cls._normalize_date(
            fields.get("成立日期") or fields.get("基金合同生效日") or variables.get("clrq", "")
        )
        fields["基金代码"] = str(code)
        fields["基金简称"] = name
        fields["电子直销交易入口"] = cls.trade_entry_url

        body_text = cls._clean(soup.get_text(" ", strip=True))
        if "暂停交易" in body_text:
            purchase_status = "closed"
        elif soup.select_one("#goumai .buy_btn") or "立即购买" in body_text:
            # 官网按钮实际打开官方电子直销登录页，入口公开，但下单仍需登录。
            purchase_status = "open"
        else:
            purchase_status = "unknown"
        fields["购买按钮状态"] = purchase_status
        fields["定投按钮状态"] = "unknown"

        nav_row = nav or {}
        if nav_row:
            fields["最新净值"] = nav_row.get("NetValue", "")
            fields["最新累计净值"] = nav_row.get("TotalNetValue", "")
            fields["最新净值日期"] = cls._normalize_date(nav_row.get("FundDate", ""))
            fields["官网基金状态"] = cls._clean(nav_row.get("FundState", ""))
            fields["净值数据源URL"] = cls.nav_xml_url
            fund_type = fund_type or cls._clean(nav_row.get("FundType", ""))
        statuses = []
        if purchase_status != "unknown":
            statuses.append("购买入口可用" if purchase_status == "open" else "暂停交易")
        if nav_row.get("FundState"):
            statuses.append(cls._clean(nav_row["FundState"]))
        trade_status = "；".join(dict.fromkeys(statuses)) or "官网产品页未公开当前交易状态"
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=fund_type,
            risk_level=risk,
            inception_date=inception,
            asset_scale=fields.get("基金规模") or fields.get("资产规模") or fields.get("基金资产净值", ""),
            net_value_date=fields.get("最新净值日期", ""),
            trade_status=trade_status,
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def _fetch_product_html(self, fund: FundIdentity) -> Tuple[bytes, str]:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("摩根基金产品页需要认证")
        response.raise_for_status()
        return response.content, response.url or url

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        content, source_url = self._fetch_product_html(fund)
        nav = {}
        # The product page is authoritative.  The static net-value XML is an
        # optional enrichment and can be unavailable independently (for
        # example while its CDN is being refreshed).
        try:
            nav_response = self.session.get(
                self.nav_xml_url,
                headers=self._headers(source_url),
                timeout=self.timeout,
            )
            if nav_response.status_code not in {401, 403}:
                nav_response.raise_for_status()
                nav = self.parse_nav_xml(nav_response.content, fund.code)
        except (requests.RequestException, PermissionError, ValueError, TypeError, AttributeError):
            nav = {}
        return self.parse_product_html(content, fund.code, source_url, self._observed_at(), nav=nav)

    @staticmethod
    def _status_bool(value: str) -> Optional[bool]:
        return True if value == "open" else False if value == "closed" else None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="摩根基金官网电子直销入口",
            subscription=self._status_bool(product.fields.get("购买按钮状态", "")),
            redemption=None,
            sip=None,
            quota_remark="产品页购买入口公开可见；交易提交页要求官方电子直销账户登录，直销限额来自官网限额公告。",
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
    def parse_search_results_html(cls, html: bytes, source_url: str = "") -> List[Dict[str, str]]:
        """解析官网搜索结果中的公告标题、日期和官方 URL。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        result = soup.select_one(".result") or soup
        output: List[Dict[str, str]] = []
        seen: set[str] = set()
        # Current pages use a direct ``div`` list, while older search templates
        # wrap each result in ``li``/``dl``.  Start at title nodes so both forms
        # produce the same normalized records.
        title_nodes = result.select("p.title, .title")
        if not title_nodes:
            title_nodes = [anchor for anchor in result.find_all("a", href=True) if anchor.get_text(" ", strip=True)]
        for title_node in title_nodes:
            anchor = title_node if title_node.name == "a" else title_node.find_parent("a")
            if anchor is None:
                anchor = title_node.find("a", href=True)
            block = title_node.parent
            # A title is sometimes wrapped by its link while the date sits in
            # the enclosing result container; climb until the sibling date is
            # available without assuming one specific CMS template.
            for _ in range(4):
                if block.select_one("p.category"):
                    break
                if block.parent is None:
                    break
                block = block.parent
            if not anchor or not anchor.get("href"):
                continue
            url = urljoin(source_url or cls.search_url, str(anchor.get("href")))
            if url in seen:
                continue
            seen.add(url)
            title = cls._clean(title_node.get_text(" ", strip=True) or anchor.get_text(" ", strip=True))
            category = cls._clean(block.select_one("p.category").get_text(" ", strip=True) if block.select_one("p.category") else "")
            date_match = re.search(r"(\d{4})[-年./](\d{1,2})[-月./](\d{1,2})", category)
            date = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}" if date_match else ""
            output.append({"title": re.sub(r"^\d+[.、]\s*", "", title), "date": date, "url": url})
        return output

    @classmethod
    def _search_keywords(cls, fund: FundIdentity) -> List[str]:
        raw = cls._clean(fund.name)
        candidates = [raw]
        stripped = re.sub(r"(?:人民币|美元|现汇|现钞|[A-Z]类?|（[^）]*）|\([^)]*\))", "", raw).strip()
        if stripped and stripped != raw:
            candidates.append(stripped)
        # 摩根公告标题常用“基金全称/指数”而目录使用短名，保留短名作为第二次检索。
        compact = re.sub(r"\s+", "", stripped or raw)
        if compact and compact not in candidates:
            candidates.append(compact)
        return list(dict.fromkeys(candidates))

    def _search_notices(self, fund: FundIdentity) -> List[Dict[str, str]]:
        all_rows: Dict[str, Dict[str, str]] = {}
        for keyword in self._search_keywords(fund):
            response = self.session.get(
                self.search_url,
                params={
                    "channelid": "207436",
                    "searchword": keyword,
                    "orderby": "-DOCRELTIME",
                    "perpage": "100",
                    "page": "1",
                },
                headers=self._headers(self.home_url),
                timeout=self.timeout,
            )
            if response.status_code in {401, 403}:
                raise PermissionError("摩根基金官网公告搜索需要认证")
            response.raise_for_status()
            for row in self.parse_search_results_html(response.content, response.url or self.search_url):
                all_rows[row["url"]] = row
            if any(any(term in row["title"] for term in self.relevant_notice_terms) for row in all_rows.values()):
                break
        return sorted(all_rows.values(), key=lambda row: row.get("date", ""), reverse=True)

    @classmethod
    def _notice_text(cls, content: bytes, source_url: str) -> Tuple[str, str]:
        """返回公告纯文本及实际附件 URL；支持公告 HTML 内嵌 PDF。"""
        data = bytes(content or b"")
        if source_url.lower().endswith(".pdf") or data.startswith(b"%PDF"):
            try:
                return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages), source_url
            except (PdfReadError, ValueError, OSError):
                return "", source_url
        soup = BeautifulSoup(data, "html.parser")
        pdf = next((a for a in soup.find_all("a", href=True) if str(a.get("href", "")).lower().endswith(".pdf")), None)
        if pdf:
            return cls._clean(soup.get_text(" ", strip=True)), urljoin(source_url, str(pdf["href"]))
        return cls._clean(soup.get_text(" ", strip=True)), source_url

    @staticmethod
    def _format_foreign_amount(value: str, unit: str) -> Optional[str]:
        try:
            number = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None
        if number.is_integer():
            display = str(int(number))
        else:
            display = f"{number:.2f}".rstrip("0").rstrip(".")
        return f"{display}{'美元' if unit == '美元' else '元'}"

    @classmethod
    def _parse_code_sequence(cls, text: str) -> List[str]:
        match = re.search(
            r"交易代码\s*[:：]?\s*((?:\d{6}[A-Z]?\s*(?:[,，、|｜/]\s*)?){1,20})",
            text,
        )
        return re.findall(cls._code_pattern(), match.group(1)) if match else []

    @classmethod
    def _parse_amount_for_code(cls, text: str, code: str, currency_hint: str = "") -> Optional[str]:
        codes = cls._parse_code_sequence(text)
        if code not in codes:
            return None
        index = codes.index(code)
        label = re.search(r"限制申购金额", text)
        if not label:
            return None
        tail = text[label.end():]
        next_label = re.search(r"(?:限制转换转入金额|限制定期定额投资金额|是否暂停大额|注[:：]|2[.、])", tail)
        section = tail[: next_label.start() if next_label else 700]
        # 去掉单位说明，避免把“人民币元”识别为金额 token。
        section = re.sub(r"（[^）]{0,40}）|\([^)]{0,40}\)", " ", section)
        values = re.findall(r"(?<!\d)([\d,.]+|不限|无限制|[-—])\s*(亿元|亿|万元|万|美元|人民币元|人民币|元)?", section)
        if index >= len(values):
            return None
        raw, unit = values[index]
        if raw in {"-", "—"}:
            return None
        if raw in {"不限", "无限制"}:
            return "不限"
        unit = unit or ("美元" if currency_hint == "美元" else "元")
        if unit in {"人民币元", "人民币"}:
            unit = "元"
        if unit in {"亿元", "亿", "万元", "万"}:
            return _amount(f"{raw}{unit}")
        return cls._format_foreign_amount(raw, unit)

    @classmethod
    def parse_announcement_text(
        cls,
        text: str,
        code: str,
        title: str = "",
        announcement_date: str = "",
        source_url: str = "",
        fund_name: str = "",
    ) -> Optional[Dict[str, str]]:
        """按公告中的份额代码列解析直销/通用申购限额。"""
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if code not in cls._parse_code_sequence(normalized):
            return None
        direct = "直销渠道" in normalized or "直销渠道" in str(title or "")
        # 公告注释通常同时出现人民币和美元，不能据此把人民币份额误判为美元。
        # 目录/产品页名称包含“美元”时才使用美元格式化提示。
        currency_hint = "美元" if "美元" in str(fund_name or "") else ""
        limit = cls._parse_amount_for_code(normalized, code, currency_hint=currency_hint)
        pause_match = re.search(r"是否暂停大额(?:申购|申购、大额转换转入、定期定额投资)?\s*((?:(?:是|否)\s*)+)", normalized)
        # Restrict matching to the captured values; searching the full match
        # would also count the ``是否`` characters in the label itself.
        pause_values = re.findall(r"是|否", pause_match.group(1)) if pause_match else []
        code_index = cls._parse_code_sequence(normalized).index(code)
        paused = code_index < len(pause_values) and pause_values[code_index] == "是"
        if not limit and paused:
            limit, status = "暂停", "paused"
        elif not limit and ("恢复大额申购" in normalized or "恢复申购" in normalized or "恢复大额申购" in title):
            limit, status = "不限", "ok"
        elif limit:
            status = "ok"
        else:
            return None
        return {
            "limit": limit,
            "status": status,
            "quota_type": "直销渠道单日申购、定投及转换转入累计限额" if direct else "官网公告限制申购金额",
            "quota_remark": (
                "公告正文明确适用于直销柜台、网上交易、摩根资产管理APP及微信公众号。"
                if direct else "官网最新相关限额公告未单列销售渠道，按官方公告直销口径记录。"
            ),
            "announcement_title": cls._clean(title),
            "announcement_date": cls._normalize_date(announcement_date),
            "source_url": source_url,
        }

    def _fetch_document(self, url: str) -> Tuple[str, str]:
        response = self.session.get(url, headers=self._headers(self.home_url), timeout=max(self.timeout, 30))
        if response.status_code in {401, 403}:
            raise PermissionError("摩根基金官网公告附件需要认证")
        response.raise_for_status()
        text, actual_url = self._notice_text(response.content, response.url or url)
        if actual_url != (response.url or url):
            pdf_response = self.session.get(actual_url, headers=self._headers(url), timeout=max(self.timeout, 30))
            if pdf_response.status_code in {401, 403}:
                raise PermissionError("摩根基金官网公告 PDF 需要认证")
            pdf_response.raise_for_status()
            pdf_text, pdf_url = self._notice_text(pdf_response.content, pdf_response.url or actual_url)
            # Prefer attachment text when available, while keeping HTML text
            # as a fallback for malformed or scanned PDFs.
            if pdf_text:
                text, actual_url = pdf_text, pdf_url
        return text, actual_url

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        observed = self._observed_at()
        try:
            notice_rows = [
                row for row in self._search_notices(fund)
                if any(term in row.get("title", "") for term in self.relevant_notice_terms)
            ]
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="摩根基金官网公告搜索",
                limit=None,
                status="official_interface_requires_auth",
                quota_type="直销申购/定投限额",
                quota_remark=str(exc),
                source_url=self.search_url,
                observed_at=observed,
                raw={"error": str(exc)},
            )
        # 直销专属公告优先于后发布的通用/代销口径公告；同一口径取最新日期。
        notice_rows.sort(key=lambda row: row.get("date", ""), reverse=True)
        notice_rows.sort(key=lambda row: "直销渠道" not in row.get("title", ""))
        auth_error = ""
        for row in notice_rows:
            try:
                text, attachment_url = self._fetch_document(row["url"])
            except PermissionError as exc:
                auth_error = str(exc)
                break
            except (requests.RequestException, ValueError, OSError):
                continue
            parsed = self.parse_announcement_text(
                text,
                fund.code,
                title=row.get("title", ""),
                announcement_date=row.get("date", ""),
                source_url=attachment_url or row["url"],
                fund_name=fund.name,
            )
            if not parsed:
                continue
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="摩根基金官网直销渠道公告" if "直销渠道" in parsed["quota_type"] else "摩根基金官网限额公告",
                limit=parsed["limit"],
                status=parsed["status"],
                quota_type=parsed["quota_type"],
                quota_remark=parsed["quota_remark"],
                source_url=parsed["source_url"],
                observed_at=observed,
                raw=parsed,
            )
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="摩根基金官网公告搜索",
            limit=None,
            status="official_interface_requires_auth" if auth_error else "official_api_no_data",
            quota_type="直销申购/定投限额",
            quota_remark=auth_error or "官网公告搜索未找到可按当前份额代码解析的限额公告；不猜测为不限。",
            source_url=self.search_url,
            observed_at=observed,
            raw={"notice_count": len(notice_rows), **({"error": auth_error} if auth_error else {})},
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(snapshot.limit or "未获取", snapshot.channel, snapshot.source_url, snapshot.quota_remark, status=snapshot.status)

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public_with_trade_auth_boundary",
            reason="摩根基金超市、产品页、公告搜索和 PDF 可匿名访问；电子直销下单页需要账户登录，图表详情接口要求 tk-trans-signature。适配器不调用或绕过认证接口。",
            requires_login=True,
            requires_device_signature=True,
        )
