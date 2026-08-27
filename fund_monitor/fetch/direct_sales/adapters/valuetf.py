"""汇添富基金（99fund.com）官方 Adapter。

汇添富的公募基金目录是服务端渲染的全量表格，单只基金的
``fundinfo.shtml``/``fundgk.shtml`` 页面公开基金资料、最新净值和网上
交易按钮；申购限额通过同一产品的官网公告页 ``fundgg.shtml`` 及其公告
PDF 发布。公告 PDF 中的交易代码、暂停状态和限制申购金额按份额列出，
适配器按代码定位，避免把同一主基金的其他份额误当成当前份额。

这里只访问汇添富官网公开页面和附件，不登录、不进入交易提交页、不绕过
验证码，也不使用第三方平台数据。
"""

from datetime import datetime, timezone
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..common import _amount, _extract_office_text, _record
from ..registry import HEADERS
from .base import (
    AuthBoundary,
    ChannelTradeStatus,
    DirectLimitSnapshot,
    FundIdentity,
    ProductSnapshot,
    TradeSnapshot,
)


class ValuetfAdapter:
    """汇添富官网基金目录、产品页、公告 PDF Adapter。"""

    manager_id = "汇添富"
    home_url = "https://www.99fund.com/"
    catalogue_url = "https://www.99fund.com/main/products/jijinhb/index.shtml"
    product_url_template = "https://www.99fund.com/main/products/pofund/{code}/fundgk.shtml"
    info_url_template = "https://www.99fund.com/main/products/pofund/{code}/fundinfo.shtml"
    notice_url_template = "https://www.99fund.com/main/products/pofund/{code}/fundgg.shtml"
    source_type_catalogue = "valuetf_official_fund_catalogue_html"
    source_type_product = "valuetf_official_product_page"
    source_type_notice = "valuetf_official_notice_pdf"

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
        return " ".join(str(value or "").replace("\xa0", " ").split())

    @staticmethod
    def _decode(html: bytes) -> str:
        raw = bytes(html or b"")
        # 99fund 页面响应头/HTML 均以 GBK/GB2312 为主；少量 fixture 是 UTF-8。
        try:
            text = raw.decode("gb18030")
            if "�" not in text:
                return text
        except UnicodeDecodeError:
            pass
        return raw.decode("utf-8", errors="ignore")

    @staticmethod
    def _share_class(name: str) -> str:
        match = re.search(r"([A-Z])(?:人民币|美元(?:现汇|现钞)?)?$", str(name or "").strip())
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
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        """解析汇添富基金产品中心的全量 ``tr#基金代码`` 目录。"""
        soup = BeautifulSoup(cls._decode(html), "html.parser")
        found: Dict[str, FundIdentity] = {}
        for row in soup.find_all("tr", id=True):
            id_code = cls._clean(row.get("id"))
            code_match = re.fullmatch(r"\d{6}", id_code)
            if not code_match:
                code_node = row.find("a", href=re.compile(r"/pofund/\d{6}/fundgk\.shtml"))
                code_match = re.search(r"/pofund/(\d{6})/fundgk\.shtml", str(code_node.get("href")) if code_node else "")
            if not code_match:
                continue
            code = id_code if re.fullmatch(r"\d{6}", id_code) else code_match.group(1)
            anchors = row.find_all("a", href=True)
            product_pattern = re.compile(rf"/pofund/{code}/fundgk\.shtml")
            product_anchors = [a for a in anchors if product_pattern.search(str(a.get("href")))]
            name = next((cls._clean(a.get_text(" ", strip=True)) for a in product_anchors if cls._clean(a.get_text(" ", strip=True)) and cls._clean(a.get_text(" ", strip=True)) != code), code)
            cells = [cls._clean(x.get_text(" ", strip=True)) for x in row.find_all("td")]
            status = cells[-2] if len(cells) >= 2 else ""
            trade = cells[-1] if cells else ""
            # The product-center tables have no stable type column across categories;
            # retain category/type in fields of snapshots instead of guessing here.
            found[code] = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                share_class=cls._share_class(name),
                source_url=cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def parse_catalogue_rows(cls, html: bytes) -> List[Dict[str, str]]:
        """返回目录中可审计的原始行字段。"""
        soup = BeautifulSoup(cls._decode(html), "html.parser")
        output: List[Dict[str, str]] = []
        for row in soup.find_all("tr", id=True):
            code = cls._clean(row.get("id"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            cells = [cls._clean(x.get_text(" ", strip=True)) for x in row.find_all("td")]
            anchors = row.find_all("a", href=True)
            name = next((cls._clean(a.get_text(" ", strip=True)) for a in anchors if cls._clean(a.get_text(" ", strip=True)) != code), "")
            output.append({
                "基金代码": code,
                "基金名称": name,
                "更新日期": cells[2] if len(cells) > 2 else "",
                "单位净值": cells[3] if len(cells) > 3 else "",
                "累计净值": cells[4] if len(cells) > 4 else "",
                "当前状态": cells[-2] if len(cells) >= 2 else "",
                "网上交易": cells[-1] if cells else "",
            })
        return output

    def discover_funds(self) -> List[FundIdentity]:
        response = self.session.get(self.catalogue_url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("汇添富基金目录页需要认证")
        response.raise_for_status()
        funds = self.parse_catalogue_html(response.content, response.url or self.catalogue_url)
        if not funds:
            raise RuntimeError("汇添富基金目录页未找到当前基金行")
        return funds

    @classmethod
    def _label_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for row in soup.select("table.infotable tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            if len(cells) >= 2 and cells[0]:
                fields[cells[0].rstrip("：:").strip()] = cells[1]
        return fields

    @classmethod
    def _page_name(cls, soup: BeautifulSoup, code: str) -> str:
        h1 = soup.select_one("h1.H1Pro")
        if h1:
            span = h1.select_one(".fundNum")
            if span:
                span.extract()
            name = cls._clean(h1.get_text(" ", strip=True))
            if name:
                return name
        meta = soup.select_one("meta[name='Keywords']")
        if meta and meta.get("content"):
            return cls._clean(str(meta["content"]).split(",")[0])
        return str(code)

    @classmethod
    def _overview_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for table in soup.find_all("table"):
            headers = [cls._clean(x.get_text(" ", strip=True)) for x in table.find_all("th")]
            rows = table.find_all("tr")
            if len(headers) < 2 or not rows:
                continue
            values = [cls._clean(x.get_text(" ", strip=True)) for x in rows[-1].find_all(["td", "th"])]
            if len(values) >= len(headers) and "基金类型" in headers and "净值日期" in headers:
                fields.update({headers[i]: values[i] for i in range(len(headers)) if values[i]})
                break
        return fields

    @classmethod
    def _button_state(cls, node: Any) -> str:
        if node is None:
            return "unknown"
        href = str(node.get("href") or "")
        classes = set(node.get("class") or [])
        if "disabled" in classes or "javascript:void" in href.lower():
            # Some pages use javascript:void(0) with onclick=gotoURL; that is still open.
            if node.get("onclick") and "gotoURL" in str(node.get("onclick")):
                return "open"
            return "closed"
        return "open" if href else "closed"

    @classmethod
    def _trade_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        buy = soup.select_one("#buy a")
        aip = soup.select_one("#aip a") or soup.select_one("a[href*='operateType=1']")
        # Product pages typically expose only #buy; catalogue pages expose both links.
        fields["购买按钮状态"] = cls._button_state(buy)
        fields["定投按钮状态"] = cls._button_state(aip)
        if buy and buy.get("href"):
            fields["购买入口"] = urljoin(cls.home_url, str(buy["href"]))
        if aip and aip.get("href"):
            fields["定投入口"] = urljoin(cls.home_url, str(aip["href"]))
        return fields

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        soup = BeautifulSoup(cls._decode(html), "html.parser")
        fields = cls._label_fields(soup)
        fields.update(cls._overview_fields(soup))
        fields.update(cls._trade_fields(soup))
        fields["基金代码"] = str(code)
        name = fields.get("基金简称") or cls._page_name(soup, code)
        full_name = fields.get("基金全称", "")
        fund_type = fields.get("基金类型", "")
        risk = fields.get("产品风险等级", "") or fields.get("风险收益特征", "")
        inception = cls._normalize_date(fields.get("成立日期", ""))
        nav_date = cls._normalize_date(fields.get("净值日期", ""))
        if fields.get("基金净值"):
            fields["最新净值"] = fields["基金净值"]
        if nav_date:
            fields["最新净值日期"] = nav_date
        statuses: List[str] = []
        for key, label in (("购买按钮状态", "购买"), ("定投按钮状态", "定投")):
            if fields.get(key) in {"open", "closed"}:
                statuses.append(f"{label}{'可用' if fields[key] == 'open' else '不可用'}")
        trade_status = "；".join(statuses) or fields.get("当前状态", "") or "官网产品页未公开当前交易状态"
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=fund_type,
            risk_level=risk,
            inception_date=inception,
            asset_scale=fields.get("基金规模", ""),
            net_value_date=nav_date,
            trade_status=trade_status,
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        info_url = self.info_url_template.format(code=fund.code)
        overview_url = self.product_url_template.format(code=fund.code)
        response = self.session.get(info_url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("汇添富基金产品资料页需要认证")
        response.raise_for_status()
        observed = self._observed_at()
        product = self.parse_product_html(response.content, fund.code, response.url or info_url, observed)
        try:
            overview = self.session.get(overview_url, headers=self._headers(info_url), timeout=self.timeout)
            if overview.status_code not in {401, 403}:
                overview.raise_for_status()
                extra = self.parse_product_html(overview.content, fund.code, overview.url or overview_url, observed)
                fields = dict(product.fields)
                fields.update({k: v for k, v in extra.fields.items() if v})
                product = ProductSnapshot(
                    manager_id=product.manager_id,
                    code=product.code,
                    name=product.name or extra.name,
                    full_name=product.full_name or extra.full_name,
                    fund_type=product.fund_type or extra.fund_type,
                    risk_level=product.risk_level or extra.risk_level,
                    inception_date=product.inception_date or extra.inception_date,
                    asset_scale=product.asset_scale or extra.asset_scale,
                    net_value_date=product.net_value_date or extra.net_value_date,
                    trade_status=extra.trade_status if extra.trade_status != "官网产品页未公开当前交易状态" else product.trade_status,
                    source_url=product.source_url,
                    observed_at=observed,
                    fields=fields,
                )
        except (requests.RequestException, RuntimeError, ValueError):
            pass
        return product

    @staticmethod
    def _status_bool(value: str) -> Optional[bool]:
        return True if value == "open" else False if value == "closed" else None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="汇添富官网产品页",
            subscription=self._status_bool(product.fields.get("购买按钮状态", "")),
            redemption=None,
            sip=self._status_bool(product.fields.get("定投按钮状态", "")),
            quota_remark="交易状态来自汇添富官网产品页公开按钮；未访问需要登录的交易提交页。",
            raw=product.fields,
        )
        return TradeSnapshot(
            manager_id=self.manager_id, code=fund.code, channels=[channel], api_status=0,
            message=product.trade_status, source_url=product.source_url,
            observed_at=product.observed_at, raw=product.fields,
        )

    @classmethod
    def parse_notice_list_html(cls, html: bytes, source_url: str = "") -> List[Tuple[str, str, str]]:
        """解析单基金公告页中与申购限制相关的公告。"""
        soup = BeautifulSoup(cls._decode(html), "html.parser")
        candidates: List[Tuple[str, str, str]] = []
        for row in soup.select("table tr"):
            cells = [cls._clean(x.get_text(" ", strip=True)) for x in row.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            title = cells[0].lstrip("· ")
            date_text = cls._normalize_date(cells[1])
            compact = title.replace(" ", "")
            if not any(word in compact for word in ("大额申购", "大额定投", "定期定额投资", "限制申购", "暂停申购", "恢复申购")):
                continue
            anchor = row.find("a", href=True)
            if anchor is None:
                continue
            href = str(anchor.get("href"))
            if not (".pdf" in href.lower() or "附件" in cls._clean(anchor.get_text(" ", strip=True)) or "下载" in cls._clean(anchor.get_text(" ", strip=True))):
                # The title link to the HTML announcement itself is not the attachment.
                links = row.find_all("a", href=True)
                href = next((str(a.get("href")) for a in links if ".pdf" in str(a.get("href")).lower()), href)
            candidates.append((title, date_text, urljoin(source_url, href)))
        return candidates

    @classmethod
    def _sequence_after(cls, text: str, label: str, pattern: str) -> List[str]:
        # pypdf often inserts a line break between each Chinese table header word
        # (e.g. ``下属基金份\n额的交易代码``); allow whitespace between characters.
        flexible_label = r"\s*".join(re.escape(char) for char in label if not char.isspace())
        match = re.search(
            flexible_label + r"\s*" + r"((?:(?:" + pattern + r")\s*)+)",
            text,
            re.I,
        )
        return re.findall(pattern, match.group(1)) if match else []

    @classmethod
    def parse_announcement_text(
        cls, text: str, code: str, title: str = "", announcement_date: str = "", source_url: str = ""
    ) -> Optional[Dict[str, str]]:
        """解析汇添富公告 PDF 的按份额代码排列的限额/暂停表。"""
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        compact_title = cls._clean(title).replace(" ", "")
        codes = cls._sequence_after(normalized, "下属基金份额的交易代码", r"\d{6}")
        amounts = cls._sequence_after(normalized, "下属基金份额的限制申购金额", r"[\d,.]+")
        pause_values = cls._sequence_after(normalized, "该基金份额是否暂停上述业务", r"是|否")
        units = cls._sequence_after(normalized, "金额单位", r"人民币元|美元")
        limit: Optional[str] = None
        status = "ok"
        remark = ""
        if code in codes:
            index = codes.index(code)
            if index < len(amounts):
                unit = units[index] if index < len(units) else "人民币元"
                if unit == "美元":
                    number = float(amounts[index].replace(",", ""))
                    rendered = f"{number:.2f}".rstrip("0").rstrip(".")
                    limit = f"{rendered}美元"
                else:
                    limit = _amount(f"{amounts[index]}元")
                remark = "汇添富官网公告按交易代码列出限制申购金额。"
            if index < len(pause_values) and pause_values[index] == "是" and limit is None:
                limit, status = "暂停", "paused"
                remark = "汇添富官网公告明确该份额暂停申购/定期定额投资。"
        if limit is None and re.search(r"暂停(?:申购|大额申购|定期定额投资)", compact_title):
            limit, status = "暂停", "paused"
            remark = "汇添富官网公告标题明确暂停申购/定期定额投资。"
        if limit is None and re.search(r"恢复(?:申购|大额申购)", compact_title) and not amounts:
            limit, remark = "不限", "汇添富官网公告明确恢复申购/大额申购，未列出金额上限。"
        if limit is None:
            return None
        return {
            "limit": limit,
            "status": status,
            "quota_type": "官网公告限制申购金额",
            "quota_remark": remark,
            "announcement_title": cls._clean(title),
            "announcement_date": cls._normalize_date(announcement_date),
            "source_url": source_url,
        }

    def _fetch_notice(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        list_url = self.notice_url_template.format(code=fund.code)
        response = self.session.get(list_url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("汇添富基金公告页需要认证")
        response.raise_for_status()
        candidates = self.parse_notice_list_html(response.content, response.url or list_url)
        candidates.sort(key=lambda item: item[1], reverse=True)
        for title, date_text, pdf_url in candidates:
            try:
                attachment = self.session.get(pdf_url, headers=self._headers(list_url), timeout=max(self.timeout, 25))
                if attachment.status_code in {401, 403}:
                    raise PermissionError("汇添富基金公告附件需要认证")
                attachment.raise_for_status()
                text = _extract_office_text(attachment.content, pdf_url)
            except PermissionError:
                raise
            except (requests.RequestException, RuntimeError, ValueError, OSError):
                continue
            parsed = self.parse_announcement_text(text, fund.code, title, date_text, pdf_url)
            if parsed:
                parsed["announcement_text"] = text
                return parsed
        return None

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        observed = self._observed_at()
        try:
            notice = self._fetch_notice(fund)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="汇添富基金官网公告 PDF", limit=None, status="official_interface_requires_auth",
                quota_remark=str(exc), source_url=self.notice_url_template.format(code=fund.code),
                observed_at=observed, raw={"error": str(exc)},
            )
        if notice:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="汇添富基金官网最新公告 PDF", limit=notice["limit"], status=notice["status"],
                quota_type=notice["quota_type"], quota_remark=notice["quota_remark"],
                source_url=notice["source_url"], observed_at=observed, raw=notice,
            )
        return DirectLimitSnapshot(
            manager_id=self.manager_id, code=fund.code, customer_type="individual",
            channel="汇添富基金官网公告 PDF", limit=None, status="official_api_no_data",
            quota_type="", quota_remark="汇添富官网当前基金公告页未解析到申购限制/暂停公告，不以历史或第三方数据代替。",
            source_url=self.notice_url_template.format(code=fund.code), observed_at=observed, raw={},
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(
            snapshot.limit or "未获取", "汇添富基金官网最新公告 PDF", snapshot.source_url,
            snapshot.quota_remark, status=snapshot.status,
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="汇添富官网全量目录、产品资料/净值页和单基金公告页/PDF均可匿名访问；申购、定投交易提交入口位于 trade.99fund.com，可能要求登录、验证码或绑定银行卡，Adapter 不访问或绕过该边界。",
        )
