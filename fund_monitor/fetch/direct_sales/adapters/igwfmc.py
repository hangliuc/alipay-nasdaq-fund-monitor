"""景顺长城基金官网独立 Adapter。

景顺长城官网的基金目录和单只产品页公开提供基金/份额名称、代码、类型、
风险、净值、成立日期以及申购/赎回状态；产品页的基金公告区会列出官方公告
详情或 PDF。适配器只读取 ``igwfmc.com`` 及其官方静态资源，不登录、不提交
交易，也不从第三方平台补齐字段。

官网部分公告列表由前端动态加载，且公告可能只在详情页或 PDF 中出现。因而
本模块把“找不到当前可验证公告”和“官网返回认证边界”区分开来：前者是
``official_api_no_data``，后者是 ``official_interface_requires_auth``。起购
金额不是大额申购限额；页面缺少限额时不会据此推断“不限”。
"""

from datetime import datetime, timezone
from io import BytesIO
import re
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

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


class IgwfmcAdapter:
    """景顺长城基金官网目录、产品页和公告 Adapter。"""

    manager_id = "景顺长城"
    home_url = "https://www.igwfmc.com/"
    catalogue_url = "https://www.igwfmc.com/main/jjcp/product.html"
    product_url_template = "https://www.igwfmc.com/main/jjcp/product/{code}/detail.html"
    notice_url = "https://www.igwfmc.com/main/zxzx/info-4.html"
    login_url = "https://www.igwfmc.com/fundorg/login/login.html"

    source_type_catalogue = "igwfmc_official_fund_catalogue_page"
    source_type_product = "igwfmc_official_product_page"
    source_type_notice = "igwfmc_official_notice"
    source_type_notice_pdf = "igwfmc_official_notice_pdf"

    relevant_notice_terms = (
        "大额申购",
        "大额定投",
        "定期定额投资",
        "限制申购",
        "暂停申购",
        "恢复申购",
    )
    notice_state_terms = ("暂停", "调整", "恢复", "限制")

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._catalogue_rows: Optional[Dict[str, Dict[str, str]]] = None
        self._last_product_html: Optional[bytes] = None
        self._last_product_code: str = ""
        self._last_product_url: str = ""

    def _observed_at(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def _headers(cls, referer: str = "") -> Dict[str, str]:
        return {**HEADERS, "Referer": referer or cls.home_url}

    @staticmethod
    def _is_official_url(url: str) -> bool:
        hostname = (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
        return hostname == "igwfmc.com" or hostname.endswith(".igwfmc.com")

    @staticmethod
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").replace("\xa0", " ").split())

    @staticmethod
    def _share_class(name: str) -> str:
        # 产品简称既可能使用“股票A”，也可能使用“股票A类人民币”或“人民币C”。
        match = re.search(r"([A-Z])(?:类)?(?:人民币|美元(?:现汇|现钞)?)?$", str(name or "").strip())
        return match.group(1) if match else ""

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
    def _code_from_href(cls, href: str) -> str:
        match = re.search(r"/product/(\d{6})/detail(?:\.html)?(?:[?#]|$)", str(href or ""), re.I)
        if match:
            return match.group(1)
        match = re.search(r"(?:fundCode|fundcode|code)[=/:-]?(\d{6})", str(href or ""), re.I)
        return match.group(1) if match else ""

    @classmethod
    def _name_from_text(cls, text: str, code: str) -> str:
        value = cls._clean(text)
        value = re.sub(rf"(?:基金)?(?:代码|编号)\s*[：:]?\s*{re.escape(code)}", "", value)
        value = re.sub(rf"\(?\s*{re.escape(code)}\s*\)?", "", value)
        value = re.sub(r"^[|｜:：\-\s]+|[|｜:：\-\s]+$", "", value)
        if value in {"购买", "详情", "查看详情", "立即购买", ""}:
            return ""
        return value

    @classmethod
    def _type_from_text(cls, text: str) -> str:
        # 顺序较具体的类型在前，避免把 QDII 型先截成“型”。
        match = re.search(
            r"(QDII(?:-LOF)?型?|FOF型?|货币型|债券型|混合型|股票型|指数型|ETF(?:联接)?型?|商品型|REITs?)",
            str(text or ""),
            re.I,
        )
        return cls._clean(match.group(1)) if match else ""

    @classmethod
    def _catalogue_row_map(cls, html: bytes, source_url: str = "") -> Dict[str, Dict[str, str]]:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        rows: Dict[str, Dict[str, str]] = {}
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            code = cls._code_from_href(href)
            if not code:
                continue
            row_node = anchor.find_parent(["tr", "li"]) or anchor.parent
            row_text = cls._clean(row_node.get_text(" ", strip=True) if row_node else anchor.get_text(" ", strip=True))
            name = cls._name_from_text(anchor.get_text(" ", strip=True), code)
            if not name:
                # 某些官网表格把名称放到链接外的同一行，使用代码前的文本作为兜底。
                before_code = re.split(rf"(?:基金)?(?:代码|编号)\s*[：:]?\s*{code}", row_text, maxsplit=1)[0]
                name = cls._name_from_text(before_code, code)
            if not name:
                continue
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row_node.find_all(["th", "td"])] if row_node else []
            date_match = re.search(r"(?:20\d{2}[年/.-]\d{1,2}[月/.-]\d{1,2}|20\d{6})", row_text)
            status_match = re.search(
                r"(?:申购状态|交易状态)\s*[：:]?\s*([^|｜;；]+)|\b(正常|开放|暂停申购|暂停|关闭)\b",
                row_text,
            )
            rows[code] = {
                "name": name,
                "fund_type": cls._type_from_text(row_text),
                "risk_level": cls._clean(re.search(r"R[1-5][^|｜;；]*|(?:低|中低|中|中高|高)风险", row_text, re.I).group(0) if re.search(r"R[1-5][^|｜;；]*|(?:低|中低|中|中高|高)风险", row_text, re.I) else ""),
                "nav_date": cls._normalize_date(date_match.group(0)) if date_match else "",
                "trade_status": cls._clean((status_match.group(1) or status_match.group(2)) if status_match else ""),
                "row_text": row_text,
                "cells": " | ".join(cells),
                "source_url": urljoin(source_url or cls.catalogue_url, href),
            }
        return rows

    @classmethod
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        """解析景顺长城官网产品目录中的当前公开基金/份额。"""
        rows = cls._catalogue_row_map(html, source_url)
        if not rows:
            # 兼容官网 SSR/搜索结果只输出“名称 基金代码：六位代码”的页面。
            text = cls._clean(BeautifulSoup(bytes(html or b""), "html.parser").get_text(" ", strip=True))
            for match in re.finditer(r"([^|｜;；]{2,100}?)(?:基金)?(?:代码|编号)\s*[：:]\s*(\d{6})", text):
                code = match.group(2)
                name = cls._name_from_text(match.group(1), code)
                if name:
                    rows[code] = {
                        "name": name,
                        "fund_type": cls._type_from_text(match.group(1)),
                        "risk_level": "",
                        "nav_date": "",
                        "trade_status": "",
                        "row_text": cls._clean(match.group(0)),
                        "cells": "",
                        "source_url": cls.product_url_template.format(code=code),
                    }
        return [
            FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=row["name"],
                fund_type=row.get("fund_type", ""),
                share_class=cls._share_class(row["name"]),
                source_url=row.get("source_url") or cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
            for code, row in sorted(rows.items())
        ]

    def discover_funds(self) -> List[FundIdentity]:
        """从景顺长城官网公开目录发现全部当前基金/份额。"""
        response = self.session.get(
            self.catalogue_url,
            headers=self._headers(self.home_url),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("景顺长城基金目录页需要认证")
        response.raise_for_status()
        actual_url = response.url or self.catalogue_url
        self._catalogue_rows = self._catalogue_row_map(response.content, actual_url)
        funds = self.parse_catalogue_html(response.content, actual_url)
        if not funds:
            raise RuntimeError("景顺长城基金目录页未找到公开基金产品链接或代码")
        return funds

    @classmethod
    def _label_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
        """从产品页表格/dl 提取键值，同时保留公司特有的原始字段。"""
        fields: Dict[str, str] = {}
        for row in soup.find_all("tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            for index in range(0, len(cells) - 1, 2):
                key = cells[index].rstrip("：:").strip()
                if key and cells[index + 1]:
                    fields[key] = cells[index + 1]
        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            key = cls._clean(dt.get_text(" ", strip=True)).rstrip("：:").strip()
            value = cls._clean(dd.get_text(" ", strip=True) if dd else "")
            if key and value:
                fields[key] = value
        return fields

    @classmethod
    def _text_field(cls, text: str, labels: Iterable[str]) -> str:
        for label in labels:
            match = re.search(rf"{re.escape(label)}\s*[：:]\s*([^|｜;；\n]+)", text)
            if match:
                return cls._clean(match.group(1))
        return ""

    @classmethod
    def _status_text(cls, text: str, label: str) -> str:
        match = re.search(
            rf"{re.escape(label)}\s*[：:]\s*(开放|正常|可申购|可购买|暂停申购|暂停赎回|暂停|关闭|封闭|不可用)",
            text,
        )
        return cls._clean(match.group(1)) if match else ""

    @classmethod
    def _status_value(cls, value: str) -> str:
        match = re.search(r"(开放|正常|可申购|可购买|暂停申购|暂停赎回|暂停|关闭|封闭|不可用)", str(value or ""))
        return cls._clean(match.group(1)) if match else ""

    @staticmethod
    def _status_bool(value: str) -> Optional[bool]:
        text = str(value or "")
        if any(term in text for term in ("暂停", "关闭", "封闭", "不可")):
            return False
        if any(term in text for term in ("开放", "正常", "可申购", "可购买", "可用")):
            return True
        return None

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        """解析产品页公开资料、净值和申购/赎回状态。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._label_fields(soup)
        page_text = cls._clean(soup.get_text(" ", strip=True))

        title_nodes = soup.select("h1, .fund_title, .fund-title, .fundName, .fund_name")
        title = cls._clean(title_nodes[0].get_text(" ", strip=True) if title_nodes else "")
        title = re.sub(r"\s*[（(]\s*基金代码\s*[：:]?\s*\d{6}\s*[）)]", "", title).strip()
        name = title or cls._clean(fields.get("基金简称") or fields.get("基金名称")) or str(code)
        full_name = cls._clean(fields.get("基金全称") or fields.get("法定名称"))
        fund_type = cls._clean(fields.get("基金类型") or fields.get("基金类别")) or cls._type_from_text(page_text)
        risk = cls._clean(fields.get("风险等级") or fields.get("基金风险等级") or fields.get("风险特征"))
        inception_raw = fields.get("成立日期") or fields.get("基金合同生效日") or fields.get("基金份额生效日")
        inception = cls._normalize_date(inception_raw or cls._text_field(page_text, ("成立日期", "基金合同生效日")))
        asset_scale = cls._clean(fields.get("基金规模") or fields.get("最新规模") or fields.get("资产规模"))

        nav_date = cls._normalize_date(fields.get("净值日期") or fields.get("最新净值日期") or fields.get("单位净值日期"))
        nav_date_match = re.search(r"净值日期\s*[：:]\s*((?:20\d{2}[年/.-]\d{1,2}[月/.-]\d{1,2})|(?:20\d{6}))", page_text)
        if nav_date_match:
            nav_date = cls._normalize_date(nav_date_match.group(1))
        nav_match = re.search(r"(?:最新净值|单位净值)[^\d]{0,30}(\d+\.\d+)", page_text)
        if nav_match:
            fields["最新净值"] = nav_match.group(1)
        if nav_date:
            fields["最新净值日期"] = nav_date

        subscription = cls._status_value(fields.get("申购状态", "")) or cls._status_text(page_text, "申购状态")
        redemption = cls._status_value(fields.get("赎回状态", "")) or cls._status_text(page_text, "赎回状态")
        if subscription:
            fields["申购状态"] = subscription
        if redemption:
            fields["赎回状态"] = redemption
        fields["基金代码"] = str(code)
        status_parts = []
        if subscription:
            status_parts.append(f"申购{subscription}")
        if redemption:
            status_parts.append(f"赎回{redemption}")
        trade_status = "；".join(status_parts) or "官网产品页未公开当前交易状态"
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=fund_type,
            risk_level=risk,
            inception_date=inception,
            asset_scale=asset_scale,
            net_value_date=nav_date,
            trade_status=trade_status,
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(self.catalogue_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("景顺长城基金产品页需要认证")
        response.raise_for_status()
        self._last_product_html = response.content
        self._last_product_code = fund.code
        self._last_product_url = response.url or url
        return self.parse_product_html(response.content, fund.code, response.url or url, self._observed_at())

    @classmethod
    def parse_notice_list_html(cls, html: bytes, source_url: str = "") -> List[Dict[str, str]]:
        """解析官网公告列表/产品页中指向公告详情或 PDF 的链接。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        candidates: List[Dict[str, str]] = []
        seen = set()
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            if not href or href.lower().startswith("javascript:"):
                continue
            title = cls._clean(anchor.get("title") or anchor.get_text(" ", strip=True))
            parent = anchor.find_parent(["li", "tr", "article", "div"]) or anchor.parent
            parent_text = cls._clean(parent.get_text(" ", strip=True) if parent else title)
            title = title or parent_text
            if not any(term in title or term in parent_text for term in cls.relevant_notice_terms):
                continue
            if not any(term in title or term in parent_text for term in cls.notice_state_terms):
                continue
            url = urljoin(source_url or cls.notice_url, href)
            if url in seen:
                continue
            seen.add(url)
            date_match = re.search(r"(?:20\d{2}[年/.-]\d{1,2}[月/.-]\d{1,2})|(?:20\d{6})", parent_text)
            candidates.append({
                "title": title,
                "date": cls._normalize_date(date_match.group(0) if date_match else ""),
                "url": url,
                "source_type": cls.source_type_notice_pdf if re.search(r"\.pdf(?:$|[?#])", url, re.I) else cls.source_type_notice,
            })
        candidates.sort(key=lambda row: row.get("date", ""), reverse=True)
        return candidates

    @classmethod
    def _document_text(cls, content: bytes, source_url: str) -> str:
        if str(source_url).lower().split("?", 1)[0].endswith(".pdf") or bytes(content or b"").startswith(b"%PDF"):
            try:
                return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
            except (PdfReadError, ValueError, OSError):
                return ""
        return cls._clean(BeautifulSoup(bytes(content or b""), "html.parser").get_text(" ", strip=True))

    @classmethod
    def _find_pdf_url(cls, content: bytes, source_url: str) -> str:
        soup = BeautifulSoup(bytes(content or b""), "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            if re.search(r"\.pdf(?:$|[?#])", href, re.I):
                return urljoin(source_url, href)
        match = re.search(r"[\"']([^\"']+\.pdf(?:\?[^\"']*)?)[\"']", bytes(content or b"").decode("utf-8", errors="ignore"), re.I)
        return urljoin(source_url, match.group(1)) if match else ""

    @classmethod
    def _aligned_code_limit(cls, text: str, code: str) -> Optional[str]:
        """按官网公告的“交易代码/限制申购金额”行对齐人民币限额。"""
        code_match = re.search(r"(?:下属分级基金的交易代码|基金代码|交易代码)\s*((?:\d{6}\s*)+)", text)
        amount_match = re.search(
            r"(?:下属分级基金的限制申购金额|限制申购金额|限制金额)"
            r"(?:\s*[（(](?P<header>[^）)]{0,30})[）)])?\s*"
            r"(?P<values>(?:[\d,.]+\s*(?:亿元|亿|万元|万|元|美元)?\s*)+)",
            text,
        )
        if not code_match or not amount_match:
            return None
        codes = re.findall(r"\d{6}", code_match.group(1))
        values = amount_match.group("values")
        numbers = re.findall(r"[\d,.]+", values)
        units = re.findall(r"(?:亿元|亿|万元|万|元|美元)", values)
        header = amount_match.group("header") or ""
        default_unit = "美元" if "美元" in header else "元" if "人民币" in header or "元" in header else ""
        if not units and not default_unit:
            return None
        if len(units) < len(numbers):
            units.extend([default_unit] * (len(numbers) - len(units)))
        if code not in codes:
            return None
        index = codes.index(code)
        if index >= len(numbers) or index >= len(units):
            return None
        # 当前项目的标准金额为人民币元；美元份额不能静默换算成人民币。
        if units[index] == "美元":
            return None
        return _amount(f"{numbers[index]}{units[index]}")

    @classmethod
    def parse_announcement_text(
        cls,
        text: str,
        code: str,
        title: str = "",
        announcement_date: str = "",
        source_url: str = "",
    ) -> Optional[Dict[str, str]]:
        """解析景顺长城官方公告中明确属于目标份额的限额/暂停状态。"""
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        title_text = cls._clean(title)
        if code and code not in normalized and code not in title_text:
            return None

        limit: Optional[str] = None
        remark = ""
        quota_type = "单日单个基金账户累计申购及定期定额投资"

        # 直销中心、直销渠道、网上直销的句段优先于泛化金额；最后一个金额通常
        # 是“由旧值调整为新值”的新上限。
        direct_segments = re.findall(
            r"(?:本公司)?(?:直销中心|直销渠道|网上直销|直销柜台)[^。；;]{0,420}", normalized
        )
        for segment in direct_segments:
            if "起购金额" in segment and not re.search(
                r"上限|限额|限制|累计申购|大额|申请金额|不超过|小于|等于", segment
            ):
                continue
            amounts = re.findall(r"[\d,.]+\s*(?:亿元|亿|万元|万|元)", segment)
            if amounts:
                limit = _amount(amounts[-1])
                remark = "景顺长城官网公告正文明确本公司直销中心/直销渠道的申购（含定投）金额上限。"
                quota_type = "直销渠道单日单个基金账户累计申购及定期定额投资上限"
                break

        if limit is None:
            limit = cls._aligned_code_limit(normalized, code)
            if limit:
                remark = "景顺长城官网公告按基金交易代码和限制申购金额列出目标份额上限；公告未单列渠道。"

        if limit is None:
            # “所有销售机构（含各代销机构及本公司直销中心）”明确覆盖直销，
            # 但不把普通费率/起购金额误当作限额。
            all_channel = re.findall(
                r"(?:所有销售机构|各销售机构|含各代销机构)[^。；;]{0,420}", normalized
            )
            for segment in all_channel:
                if "直销" not in segment:
                    continue
                amounts = re.findall(r"[\d,.]+\s*(?:亿元|亿|万元|万|元)", segment)
                if amounts:
                    limit = _amount(amounts[-1])
                    remark = "景顺长城官网公告明确所有销售机构（含本公司直销中心）的申购限制。"
                    quota_type = "所有销售机构（含直销中心）单日累计申购及定期定额投资上限"
                    break

        if limit is None:
            generic_segments = re.findall(r"单日[^。；;]{0,260}", normalized)
            generic_amounts: List[str] = []
            for segment in generic_segments:
                if not re.search(r"累计|申请金额|申购金额", segment):
                    continue
                generic_amounts.extend(re.findall(r"[\d,.]+\s*(?:亿元|亿|万元|万|元)", segment))
            if generic_amounts:
                limit = _amount(generic_amounts[-1])
                remark = "景顺长城官网限额公告正文明确单日累计申购金额；公告未单列直销/代销渠道。"

        if limit is None and re.search(r"恢复(?:大额申购|申购|定期定额投资)", title_text + normalized):
            limit, remark = "不限", "景顺长城官网公告明确恢复大额申购/定投业务，未列出金额上限。"
        if limit is None and re.search(r"暂停(?:大额申购|申购|定期定额投资)", title_text + normalized):
            limit, remark = "暂停", "景顺长城官网公告明确暂停大额申购/定投业务，未解析到金额上限。"
        if limit is None:
            return None
        return {
            "limit": limit,
            "status": "ok",
            "quota_type": quota_type,
            "quota_remark": remark,
            "announcement_title": title_text,
            "announcement_date": cls._normalize_date(announcement_date),
            "source_url": source_url,
        }

    def _notice_candidates(self, code: str, product_html: Optional[bytes] = None, product_url: str = "") -> List[Dict[str, str]]:
        if product_html is None and self._last_product_code == code and self._last_product_html is not None:
            product_html = self._last_product_html
            product_url = product_url or self._last_product_url
        if product_html is None:
            url = self.product_url_template.format(code=code)
            response = self.session.get(url, headers=self._headers(self.catalogue_url), timeout=self.timeout)
            if response.status_code in {401, 403}:
                raise PermissionError("景顺长城基金产品页需要认证")
            response.raise_for_status()
            product_html, product_url = response.content, response.url or url
        return self.parse_notice_list_html(product_html, product_url or self.product_url_template.format(code=code))

    def _fetch_notice(self, code: str, product_html: Optional[bytes] = None, product_url: str = "") -> Optional[Dict[str, str]]:
        candidates = self._notice_candidates(code, product_html, product_url)
        # 产品页公告可能由前端加载；仅在产品页确实含公告入口时访问官方公告目录，
        # 不凭空抓取全站最新公告以免把别的基金的限额归给目标代码。
        if not candidates and product_html and re.search(r"基金公告|info-4\.html|公告查询", product_html.decode("utf-8", errors="ignore")):
            response = self.session.get(
                self.notice_url,
                params={"fundCode": code},
                headers=self._headers(product_url or self.catalogue_url),
                timeout=self.timeout,
            )
            if response.status_code in {401, 403}:
                raise PermissionError("景顺长城基金公告查询需要认证")
            response.raise_for_status()
            candidates = [row for row in self.parse_notice_list_html(response.content, response.url or self.notice_url)
                          if code in row.get("title", "")]
        for row in candidates:
            detail_url = row["url"]
            if not self._is_official_url(detail_url):
                continue
            response = self.session.get(detail_url, headers=self._headers(product_url or self.home_url), timeout=max(self.timeout, 25))
            if response.status_code in {401, 403}:
                raise PermissionError("景顺长城基金公告详情需要认证")
            response.raise_for_status()
            actual_url = response.url or detail_url
            if not self._is_official_url(actual_url):
                continue
            text = self._document_text(response.content, actual_url)
            parsed = self.parse_announcement_text(text, code, row.get("title", ""), row.get("date", ""), actual_url)
            if parsed:
                parsed["announcement_text"] = text
                parsed["source_type"] = self.source_type_notice_pdf if actual_url.lower().endswith(".pdf") else self.source_type_notice
                return parsed
            pdf_url = self._find_pdf_url(response.content, actual_url)
            if not pdf_url or not self._is_official_url(pdf_url):
                continue
            pdf_response = self.session.get(pdf_url, headers=self._headers(detail_url), timeout=max(self.timeout, 30))
            if pdf_response.status_code in {401, 403}:
                raise PermissionError("景顺长城基金公告 PDF 需要认证")
            pdf_response.raise_for_status()
            pdf_text = self._document_text(pdf_response.content, pdf_response.url or pdf_url)
            parsed = self.parse_announcement_text(pdf_text, code, row.get("title", ""), row.get("date", ""), pdf_response.url or pdf_url)
            if parsed:
                parsed["announcement_text"] = pdf_text
                parsed["source_type"] = self.source_type_notice_pdf
                return parsed
        return None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        raw = product.fields
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="景顺长城官网产品页",
            subscription=self._status_bool(raw.get("申购状态", "")),
            redemption=self._status_bool(raw.get("赎回状态", "")),
            quota_remark="交易状态来自景顺长城官网产品页公开的申购/赎回状态；未访问需要登录的交易提交入口。",
            raw=raw,
        )
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel],
            api_status=0,
            message=product.trade_status,
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw=raw,
        )

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        observed = self._observed_at()
        try:
            product = self.fetch_product(fund)
            # 只传 code 也便于测试或下游实现替换 _fetch_notice；刚抓到的产品页
            # 会在实例缓存中复用，避免重复请求同一官方页面。
            notice = self._fetch_notice(fund.code)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="景顺长城官网产品页/公告",
                limit=None,
                status="official_interface_requires_auth",
                quota_type="直销申购/定投限额",
                quota_remark=str(exc),
                source_url=self.product_url_template.format(code=fund.code),
                observed_at=observed,
                raw={"error": str(exc)},
            )
        if notice is None:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="景顺长城官网公告",
                limit=None,
                status="official_api_no_data",
                quota_type="直销申购/定投限额",
                quota_remark="官网产品页可访问，但没有找到可按当前基金代码验证的最新限额公告；不以起购金额代替，也不猜测为不限。",
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
            channel="景顺长城官网直销公告" if "直销" in notice.get("quota_type", "") else "景顺长城官网限额公告",
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
            snapshot.channel,
            snapshot.source_url,
            snapshot.quota_remark,
            status=snapshot.status,
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public_with_trade_auth_boundary",
            reason="景顺长城基金目录、产品页和公开公告页面可匿名访问；网上交易登录页要求账户登录并显示图形验证码。适配器不访问登录/下单接口，不绕过验证码、设备签名或银行卡绑定。",
            requires_login=True,
            requires_captcha=True,
        )


# 兼容代码中常见的全大写缩写命名。
IGWFMCAdapter = IgwfmcAdapter
