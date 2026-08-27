"""广发基金官网独立 Adapter。

广发官网的基金超市是服务端渲染的全量目录；单只产品页公开基金资料和
购买/定投入口，页面脚本进一步调用 ``JsonService`` 基金资料接口以及
``fund-person-limit.shtml`` 个人限额接口。限额接口是当前产品页展示所用
的一手数据，因此优先使用；接口没有限额时，再追踪官网临时公告 PDF。

本模块只访问 ``gffunds.com.cn`` 及其 ``trade.gffunds.com.cn`` 的公开页面，
不登录、不提交交易、不绕过验证码，也不使用第三方平台数据。
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


class GuangfaAdapter:
    """广发官网基金目录、产品页、实时限额 API 和公告追踪 Adapter。"""

    manager_id = "广发"
    home_url = "https://www.gffunds.com.cn/"
    catalogue_url = "https://www.gffunds.com.cn/funds/"
    product_url_template = "https://www.gffunds.com.cn/funds/?fundcode={code}"
    person_limit_url = "https://www.gffunds.com.cn/api/v1/funds/fund-person-limit.shtml"
    org_limit_url = "https://www.gffunds.com.cn/api/v1/funds/fund-org-limit.shtml"
    fund_info_api_url = "https://www.gffunds.com.cn/apistore/JsonService"
    notice_list_url_template = "https://www.gffunds.com.cn/jjgg/zdsj/{suffix}"
    source_type_catalogue = "guangfa_official_fund_catalogue_html"
    source_type_product = "guangfa_official_product_page"
    source_type_api = "guangfa_official_json_service"
    source_type_limit = "guangfa_official_person_limit_api"
    source_type_notice = "guangfa_official_notice_pdf"
    notice_max_pages = 28

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
        match = re.search(r"([A-Z])(?:人民币|美元(?:现汇|现钞)?)?$", str(name or "").strip())
        return match.group(1) if match else ""

    @staticmethod
    def _normalize_date(value: Any) -> str:
        text = str(value or "").strip()
        match = re.fullmatch(r"(\d{4})[年/-]?(\d{1,2})[月/-]?(\d{1,2})日?", text)
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
        """解析官网 ``tbody#all-funds`` 的当前全量基金/份额目录。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        found: Dict[str, FundIdentity] = {}
        for row in soup.select("tbody#all-funds tr[data-fundcode]"):
            code = cls._clean(row.get("data-fundcode"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            selector = row.select_one(".js-select-fund[data-fundname]")
            anchor = row.select_one("td:nth-of-type(3) a")
            name = cls._clean(selector.get("data-fundname") if selector else "")
            name = name or cls._clean(anchor.get_text(" ", strip=True) if anchor else code)
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            risk = cells[3] if len(cells) > 3 else ""
            type_match = re.search(r"fund-typeid-([\w-]+)", " ".join(row.get("class") or []))
            found[code] = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=f"官网类型码:{type_match.group(1)}" if type_match else "",
                share_class=cls._share_class(name),
                source_url=cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def _catalogue_rows_from_html(cls, html: bytes) -> Dict[str, Dict[str, str]]:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        rows: Dict[str, Dict[str, str]] = {}
        headers = [cls._clean(x.get_text(" ", strip=True)) for x in soup.select("thead th")]
        for row in soup.select("tbody#all-funds tr[data-fundcode]"):
            code = cls._clean(row.get("data-fundcode"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            values = {headers[i]: cells[i] for i in range(min(len(headers), len(cells))) if headers[i]}
            values.update({"基金代码": code, "基金简称": cls._clean(row.select_one(".js-select-fund").get("data-fundname") if row.select_one(".js-select-fund") else "")})
            values["原始class"] = " ".join(row.get("class") or [])
            rows[code] = values
        return rows

    def _load_catalogue_rows(self) -> Dict[str, Dict[str, str]]:
        if self._catalogue_rows is not None:
            return self._catalogue_rows
        response = self.session.get(self.catalogue_url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("广发基金目录页需要认证")
        response.raise_for_status()
        rows = self._catalogue_rows_from_html(response.content)
        if not rows:
            raise RuntimeError("广发基金目录页未找到 tbody#all-funds")
        self._catalogue_rows = rows
        return rows

    def discover_funds(self) -> List[FundIdentity]:
        response = self.session.get(self.catalogue_url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("广发基金目录页需要认证")
        response.raise_for_status()
        return self.parse_catalogue_html(response.content, response.url or self.catalogue_url)

    @classmethod
    def _label_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for row in soup.select(".table04 tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            for index in range(0, len(cells) - 1, 2):
                if cells[index]:
                    fields[cells[index].strip().rstrip("：:").strip()] = cells[index + 1]
        return fields

    @classmethod
    def _inline_vars(cls, html: bytes) -> Dict[str, str]:
        text = bytes(html or b"").decode("utf-8", errors="ignore")
        values: Dict[str, str] = {}
        for key in ("fundCode", "fundName", "fundType", "fCrateDate"):
            match = re.search(r"\b%s\s*=\s*['\"]([^'\"]*)" % key, text)
            if match:
                values[key] = cls._clean(match.group(1))
        return values

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._label_fields(soup)
        inline = cls._inline_vars(html)
        name = fields.get("基金简称") or inline.get("fundName") or str(code)
        full_name = fields.get("基金全称", "")
        fund_type = inline.get("fundType") or fields.get("基金类型", "")
        risk = fields.get("风险等级", "")
        inception = cls._normalize_date(fields.get("成立日期") or inline.get("fCrateDate"))
        fields.update({"基金代码": str(code), "基金简称": name})
        # 产品页按钮是公开页面中唯一不需要进入交易表单的状态信号。
        buttons = soup.select("a.btn.buy")
        buy = next((x for x in buttons if "定投" not in cls._clean(x.get_text())), None)
        sip = next((x for x in buttons if "定投" in cls._clean(x.get_text())), None)

        def button_status(node: Any) -> str:
            if node is None:
                return "unknown"
            style = str(node.get("style") or "").replace(" ", "").lower()
            return "closed" if "display:none" in style or "disabled" in set(node.get("class") or []) else "open"

        fields["购买按钮状态"] = button_status(buy)
        fields["定投按钮状态"] = button_status(sip)
        status_parts = []
        if fields["购买按钮状态"] != "unknown":
            status_parts.append("购买可用" if fields["购买按钮状态"] == "open" else "购买不可用")
        if fields["定投按钮状态"] != "unknown":
            status_parts.append("定投可用" if fields["定投按钮状态"] == "open" else "定投不可用")
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=fund_type,
            risk_level=risk,
            inception_date=inception,
            asset_scale=fields.get("基金规模", ""),
            net_value_date="",
            trade_status="；".join(status_parts) or "官网产品页未公开当前交易状态",
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    @classmethod
    def parse_fund_info_payload(cls, payload: Any, code: str, product: ProductSnapshot) -> ProductSnapshot:
        data = payload.get("data") if isinstance(payload, dict) else None
        item = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        if not item:
            return product
        raw = {str(k): cls._clean(v) for k, v in item.items()}
        fields = dict(product.fields)
        fields.update({f"API_{k}": v for k, v in raw.items()})
        name = raw.get("FUNDNAME") or product.name
        full_name = raw.get("FUNDFULLNAME") or product.full_name
        fund_type = raw.get("CATEGORYNAME") or product.fund_type
        risk = raw.get("FUNDLEVELSHOW") or product.risk_level
        nav_date = cls._normalize_date(raw.get("NAVDATE"))
        if raw.get("NAVUNIT"):
            fields["最新净值"] = raw["NAVUNIT"]
        if nav_date:
            fields["最新净值日期"] = nav_date
        if raw.get("WEBISOPEN"):
            fields["购买按钮状态"] = "open" if raw["WEBISOPEN"] == "Y" else "closed"
        if raw.get("WEBFIXOPEN"):
            fields["定投按钮状态"] = "open" if raw["WEBFIXOPEN"] == "Y" else "closed"
        status = raw.get("FUNDSTATUS") or product.trade_status
        return ProductSnapshot(
            manager_id=product.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=fund_type,
            risk_level=risk,
            inception_date=cls._normalize_date(raw.get("CREATEDATE") or product.inception_date),
            asset_scale=raw.get("FUNDASSETSCALE") or product.asset_scale,
            net_value_date=nav_date or product.net_value_date,
            trade_status=status,
            source_url=product.source_url,
            observed_at=product.observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("广发基金产品页需要认证")
        response.raise_for_status()
        product = self.parse_product_html(response.content, fund.code, response.url or url, self._observed_at())
        try:
            info = self.session.get(
                self.fund_info_api_url,
                params={"service": "BaseInfo", "method": "Fund", "op": "queryFundByGFFundcode", "fundcode": fund.code},
                headers=self._headers(url), timeout=self.timeout,
            )
            if info.status_code in {401, 403}:
                raise PermissionError("广发基金资料接口需要认证")
            info.raise_for_status()
            product = self.parse_fund_info_payload(info.json(), fund.code, product)
        except PermissionError:
            raise
        except (requests.RequestException, ValueError, TypeError, KeyError):
            # 产品页本身仍是有效公开来源；JsonService 暂时失败不抹掉产品快照。
            pass
        return product

    @staticmethod
    def _status_bool(value: str) -> Optional[bool]:
        return True if value == "open" else False if value == "closed" else None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="广发官网产品页/JsonService",
            subscription=self._status_bool(product.fields.get("购买按钮状态", "")),
            redemption=None,
            sip=self._status_bool(product.fields.get("定投按钮状态", "")),
            quota_remark="交易状态来自广发官网产品页按钮和公开 JsonService；未访问需要登录的交易提交页面。",
            raw=product.fields,
        )
        return TradeSnapshot(
            manager_id=self.manager_id, code=fund.code, channels=[channel], api_status=0,
            message=product.trade_status, source_url=product.source_url,
            observed_at=product.observed_at, raw=product.fields,
        )

    @classmethod
    def parse_limit_payload(cls, payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        value = payload.get("MAX_ALLOT_BALA")
        if value in (None, "", 0, "0"):
            return None
        text = cls._clean(value)
        if re.search(r"不限|无限额|不设上限", text):
            return "不限"
        return _amount(f"{text}元")

    @classmethod
    def _notice_title_matches(cls, fund_name: str, title: str) -> bool:
        compact_name = re.sub(r"[（）()\s\-—]", "", fund_name)
        compact_title = re.sub(r"[（）()\s\-—]", "", title)
        if not compact_name:
            return False
        # 去掉份额及 QDII 等通用后缀，至少用一个有辨识度的中文片段匹配。
        for token in re.findall(r"[\u4e00-\u9fff]{3,}", compact_name):
            if token not in {"人民币", "证券投资基金", "交易型开放式指数证券投资基金联接基金"} and token in compact_title:
                return True
        return False

    @classmethod
    def parse_notice_list_html(cls, html: bytes, source_url: str) -> List[Tuple[str, str, str]]:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        candidates: List[Tuple[str, str, str]] = []
        for item in soup.select("ul.list li"):
            anchor = item.find("a", href=True)
            if anchor is None:
                continue
            title = cls._clean(anchor.get("title") or anchor.get_text(" ", strip=True))
            compact = title.replace(" ", "")
            if not any(word in compact for word in ("大额申购", "大额定投", "定期定额投资")):
                continue
            if not any(word in compact for word in ("暂停", "调整", "恢复")):
                continue
            date_node = item.find("span")
            date_text = cls._normalize_date(date_node.get_text(" ", strip=True) if date_node else "")
            candidates.append((title, date_text, urljoin(source_url, str(anchor.get("href")))))
        return candidates

    def _notice_candidates(self, fund: FundIdentity) -> List[Tuple[str, str, str]]:
        candidates: List[Tuple[str, str, str]] = []
        for page in range(self.notice_max_pages):
            suffix = "" if page == 0 else f"index_{page}.shtml"
            url = self.notice_list_url_template.format(suffix=suffix)
            try:
                response = self.session.get(url, headers=self._headers(self.home_url), timeout=self.timeout)
                if response.status_code in {401, 403}:
                    raise PermissionError("广发基金公告列表需要认证")
                response.raise_for_status()
            except PermissionError:
                raise
            except requests.RequestException:
                continue
            for title, date_text, href in self.parse_notice_list_html(response.content, url):
                if fund.code in title or self._notice_title_matches(fund.name, title):
                    candidates.append((title, date_text, href))
        return candidates

    @classmethod
    def parse_announcement_text(
        cls, text: str, code: str, title: str = "", announcement_date: str = "", source_url: str = ""
    ) -> Optional[Dict[str, str]]:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        compact_title = cls._clean(title).replace(" ", "")
        limit: Optional[str] = None
        remark = ""
        # 表格公告：代码和金额列按位置对齐。
        code_match = re.search(r"(?:交易代码|基金代码)\s*((?:\d{6}\s*)+)", normalized)
        amount_match = re.search(r"(?:业务限额|限制申购金额)[^\d]{0,100}((?:[\d,.]+\s*)+)(?:元|人民币元)", normalized)
        if code_match and amount_match:
            codes = re.findall(r"\d{6}", code_match.group(1))
            amounts = re.findall(r"[\d,.]+", amount_match.group(1))
            if code in codes and codes.index(code) < len(amounts):
                limit = _amount(f"{amounts[codes.index(code)]}元")
                remark = "广发官网公告按基金代码列出业务限额。"
        if limit is None:
            generic = re.search(r"业务限额为\s*([\d,.]+)\s*(?:元|人民币元)", normalized)
            if generic:
                limit = _amount(f"{generic.group(1)}元")
                remark = "广发官网公告正文明确业务限额。"
        if limit is None and ("恢复大额申购" in compact_title or "恢复大额申购" in normalized):
            limit, remark = "不限", "公告明确恢复大额申购/定投业务，未列出金额上限。"
        if limit is None and "暂停大额申购" in compact_title:
            limit, remark = "暂停", "公告明确暂停大额申购/定投业务。"
        if limit is None:
            return None
        return {
            "limit": limit, "status": "ok", "quota_type": "官网公告业务限额",
            "quota_remark": remark, "announcement_title": cls._clean(title),
            "announcement_date": cls._normalize_date(announcement_date), "source_url": source_url,
        }

    def _fetch_notice(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        for title, date_text, pdf_url in self._notice_candidates(fund):
            try:
                response = self.session.get(pdf_url, headers=self._headers(self.home_url), timeout=max(self.timeout, 25))
                if response.status_code in {401, 403}:
                    raise PermissionError("广发基金公告 PDF 需要认证")
                response.raise_for_status()
                text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
            except PermissionError:
                raise
            except (requests.RequestException, PdfReadError, ValueError, OSError):
                continue
            parsed = self.parse_announcement_text(text, fund.code, title, date_text, pdf_url)
            if parsed:
                parsed["announcement_text"] = text
                return parsed
        return None

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        product_url = self.product_url_template.format(code=fund.code)
        observed = self._observed_at()
        try:
            person = self.session.get(self.person_limit_url, params={"fundcode": fund.code}, headers=self._headers(product_url), timeout=self.timeout)
            org = self.session.get(self.org_limit_url, params={"fundcode": fund.code}, headers=self._headers(product_url), timeout=self.timeout)
            if person.status_code in {401, 403} or org.status_code in {401, 403}:
                raise PermissionError("广发基金限额接口需要认证")
            person.raise_for_status(); org.raise_for_status()
            person_payload, org_payload = person.json(), org.json()
            limit = self.parse_limit_payload(person_payload)
            if limit:
                return DirectLimitSnapshot(
                    manager_id=self.manager_id, code=fund.code, customer_type="individual",
                    channel="广发基金官网个人限额 API", limit=limit, status="ok",
                    quota_type="产品页个人客户 MAX_ALLOT_BALA",
                    quota_remark="官网产品页实时调用的 fund-person-limit.shtml；机构接口同时保存用于审计。",
                    source_url=self.person_limit_url + f"?fundcode={fund.code}", observed_at=observed,
                    raw={"person": person_payload, "org": org_payload},
                )
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="广发基金官网限额 API", limit=None, status="official_interface_requires_auth",
                quota_remark=str(exc), source_url=product_url, observed_at=observed, raw={"error": str(exc)},
            )
        except (requests.RequestException, ValueError, TypeError, KeyError):
            person_payload = org_payload = {}

        try:
            notice = self._fetch_notice(fund)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="广发基金官网公告 PDF", limit=None, status="official_interface_requires_auth",
                quota_remark=str(exc), source_url=product_url, observed_at=observed, raw={"error": str(exc)},
            )
        if notice:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="广发基金官网最新公告 PDF", limit=notice["limit"], status=notice["status"],
                quota_type=notice["quota_type"], quota_remark=notice["quota_remark"],
                source_url=notice["source_url"], observed_at=observed,
                raw={"person": person_payload, "org": org_payload, **notice},
            )
        return DirectLimitSnapshot(
            manager_id=self.manager_id, code=fund.code, customer_type="individual",
            channel="广发基金官网产品页/公告 PDF", limit=None, status="official_api_no_data",
            quota_remark="官网限额接口无个人上限，公告追踪也未解析到当前限额；不以起购金额代替。",
            source_url=product_url, observed_at=observed,
            raw={"person": person_payload, "org": org_payload},
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(snapshot.limit or "未获取", "广发基金官网实时个人限额 API/最新公告 PDF", snapshot.source_url, snapshot.quota_remark, status=snapshot.status)

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="广发基金目录、产品页、JsonService 基金资料接口、个人/机构限额接口及公告 PDF 均可匿名访问；购买和定投提交入口可能要求登录/验证码，Adapter 不访问或绕过该边界。",
        )
