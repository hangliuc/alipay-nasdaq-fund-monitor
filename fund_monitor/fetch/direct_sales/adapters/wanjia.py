"""万家基金官网独立 Adapter。

万家官网将当前基金/份额目录、净值摘要和产品资料以公开 JavaScript
``/common/index.html.js`` 的 ``FundArr`` 形式提供；每只产品页公开基金
资料、净值、交易入口和公告分类 ID。公告列表由产品页调用的
``/common-web/cms/content/getContents`` 接口返回，公告附件为万家官网 PDF。

本模块只访问 ``wjasset.com`` 官方页面、接口和公告附件，不使用第三方
平台，不登录、不提交交易，也不绕过登录、验证码、设备签名或银行卡绑定。
官网交易状态 JSONP 只在匿名公开返回成功时使用；返回错误页时保留产品页
状态并记录为公开接口不稳定/需认证边界。
"""

from datetime import datetime, timezone
import json
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf.errors import PdfReadError

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


class WanjiaAdapter:
    """万家官网基金目录、产品页、交易状态和限额公告 Adapter。"""

    manager_id = "万家"
    home_url = "https://www.wjasset.com/"
    catalogue_url = "https://www.wjasset.com/common/index.html.js"
    product_url_template = "https://www.wjasset.com/products/{kind}/{code}/index.html"
    trade_state_url = "https://www.wjasset.com/wanjia-web/trade/trade!fundState"
    notice_api_url = "https://www.wjasset.com/common-web/cms/content/getContents"
    trade_entry_url = "https://trade.wjasset.com/etrading/"

    source_type_catalogue = "wanjia_official_fund_catalogue_js"
    source_type_product = "wanjia_official_product_page"
    source_type_trade = "wanjia_official_trade_state_jsonp"
    source_type_notice = "wanjia_official_notice_pdf"

    # These values are intentionally strings: they are the same stable result
    # values used by the other direct-sales adapters and are also suitable for
    # serialising to the existing history JSON.
    STATUS_OK = "ok"
    STATUS_PAUSED = "paused"
    STATUS_NO_DATA = "official_api_no_data"
    STATUS_REQUIRES_AUTH = "official_interface_requires_auth"

    # 官网 products/index.html.js / FundArr 使用的产品目录分组。未能从
    # 页面 URL 判断时，产品页优先使用目录行的 url；这里只是 config 兼容
    # 的保守默认值，实际基金 URL 由官网目录返回。
    _kind_by_fund_type = {
        "货币型": "coin",
        "债券型": "bond",
        "混合型": "stock",
        "股票型": "stock",
        "QDII": "qdii",
        "指数型": "index",
        "FOF": "fof",
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
        self._catalogue_rows: Optional[Dict[str, Dict[str, Any]]] = None

    def _observed_at(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def _headers(cls, referer: str = "", json_request: bool = False) -> Dict[str, str]:
        headers = {**HEADERS, "Referer": referer or cls.home_url}
        if json_request:
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        return headers

    @staticmethod
    def _clean(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return " ".join(str(value or "").replace("\xa0", " ").split())

    @staticmethod
    def _share_class(name: str) -> str:
        # Product names use both ``C`` and ``C类``; QDII names may append a
        # currency label after the class letter.
        match = re.search(r"([A-Z])(?:类)?(?:人民币|美元(?:现汇|现钞)?)?$", str(name or "").strip())
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
    def _date_from_ms(cls, value: Any) -> str:
        text = str(value or "").strip()
        if re.fullmatch(r"\d{10,13}", text):
            try:
                number = int(text)
                if number > 10**11:
                    number //= 1000
                return datetime.fromtimestamp(number, tz=timezone.utc).strftime("%Y-%m-%d")
            except (ValueError, OverflowError, OSError):
                pass
        return cls._normalize_date(text)

    @classmethod
    def identity_from_config(cls, fund: Dict[str, Any]) -> FundIdentity:
        code = str(fund["code"])
        name = str(fund.get("name") or fund.get("display") or code)
        return FundIdentity(
            manager_id=cls.manager_id,
            code=code,
            name=name,
            share_class=cls._share_class(name),
            source_url=cls.product_url_template.format(kind="qdii", code=code),
            source_type="config_compatibility",
        )

    @classmethod
    def _official_url(cls, href: Any, source_url: str = "") -> str:
        """Resolve a link from an official page without changing its host.

        The catalogue script is served from ``/common/`` but its relative
        product links are rooted at the site home (``products/...``).  Using
        ``urljoin(source_url, href)`` would incorrectly produce
        ``/common/products/...`` for those links.
        """
        link = cls._clean(href)
        if not link:
            return ""
        if link.startswith(("http://", "https://", "/")):
            return urljoin(source_url or cls.home_url, link)
        return urljoin(cls.home_url, link)

    @staticmethod
    def _balanced_slice(text: str, opening: str, closing: str, start: int = 0) -> str:
        """返回 JS 字符串/数组中从 opening 到对应 closing 的片段。"""
        begin = text.find(opening, start)
        if begin < 0:
            return ""
        depth = 0
        quote = ""
        escaped = False
        for index in range(begin, len(text)):
            char = text[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                continue
            if char in {'"', "'"}:
                quote = char
            elif char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return text[begin:index + 1]
        return ""

    @classmethod
    def parse_fund_array_js(cls, script: bytes, source_url: str = "") -> List[Dict[str, Any]]:
        """解析官网 ``var FundArr = [...]``，保留原始字段。"""
        text = bytes(script or b"").decode("utf-8-sig", errors="ignore")
        marker = re.search(r"\bvar\s+FundArr\s*=", text)
        if not marker:
            return []
        array = cls._balanced_slice(text, "[", "]", marker.end())
        if not array:
            return []
        # 官网脚本当前是 JSON 兼容的对象数组，历史个别字段包含 JS 的
        # \xNN 转义；只做等价字符解码，不执行脚本。
        array = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda match: chr(int(match.group(1), 16)), array)
        try:
            payload = json.loads(array)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"万家官网 FundArr 解析失败：{exc}") from exc
        return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []

    @classmethod
    def _row_kind(cls, row: Dict[str, Any]) -> str:
        relative = str(row.get("url") or "")
        match = re.search(r"/products/([^/]+)/\d{6}/", relative)
        if match:
            return match.group(1)
        return cls._kind_by_fund_type.get(cls._clean(row.get("fundtype")), "index")

    @classmethod
    def parse_catalogue_script(cls, script: bytes, source_url: str = "") -> List[FundIdentity]:
        found: Dict[str, FundIdentity] = {}
        for row in cls.parse_fund_array_js(script, source_url):
            code = cls._clean(row.get("fundcode"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            kind = cls._row_kind(row)
            relative = cls._clean(row.get("url"))
            product_url = cls._official_url(relative, source_url) if relative else cls.product_url_template.format(kind=kind, code=code)
            name = cls._clean(row.get("fundname") or row.get("fundFullName") or code)
            found[code] = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=cls._clean(row.get("fundtype")),
                share_class=cls._share_class(name),
                source_url=product_url,
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    def discover_funds(self) -> List[FundIdentity]:
        response = self.session.get(self.catalogue_url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("万家基金公开基金目录需要认证")
        response.raise_for_status()
        rows = self.parse_fund_array_js(response.content, response.url or self.catalogue_url)
        self._catalogue_rows = {
            self._clean(row.get("fundcode")): row
            for row in rows
            if re.fullmatch(r"\d{6}", self._clean(row.get("fundcode")))
        }
        funds = self.parse_catalogue_script(response.content, response.url or self.catalogue_url)
        if not funds:
            raise RuntimeError("万家官网 FundArr 未找到当前基金/份额目录")
        return funds

    def _catalogue(self) -> Dict[str, Dict[str, Any]]:
        if self._catalogue_rows is None:
            self.discover_funds()
        return self._catalogue_rows or {}

    @classmethod
    def _product_fields_from_row(cls, row: Dict[str, Any], code: str) -> Dict[str, str]:
        fields = {str(key): cls._clean(value) for key, value in row.items()}
        fields["基金代码"] = str(code)
        fields["基金简称"] = fields.get("fundname", "")
        fields["基金全称"] = fields.get("fundFullName", "")
        fields["基金类型"] = fields.get("fundtype", "")
        fields["风险等级"] = fields.get("levelofriskStr") or fields.get("levelofrisk", "")
        fields["成立日期"] = fields.get("setupdate", "")
        fields["最新净值日期"] = fields.get("valuedate", "")
        fields["最新单位净值"] = fields.get("todaynetvalue", "")
        fields["最新累计净值"] = fields.get("todaytotalnetvalue", "")
        fields["官网交易状态"] = fields.get("statusStr") or fields.get("status", "")
        fields["最低申购金额"] = fields.get("buypoint", "")
        return fields

    @classmethod
    def _product_fields_from_html(cls, soup: BeautifulSoup) -> Dict[str, str]:
        """Read simple label/value pairs present in a product page.

        Most of the current product summary is in ``FundArr``.  Older/newer
        page templates also render a small table, however, so retain those
        values when available instead of silently dropping them.
        """
        fields: Dict[str, str] = {}
        for row in soup.select("tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            for index in range(0, len(cells) - 1, 2):
                label, value = cells[index], cells[index + 1]
                if label and value:
                    fields.setdefault(label, value)
        return fields

    @classmethod
    def parse_product_html(
        cls,
        html: bytes,
        code: str,
        source_url: str,
        observed_at: str,
        row: Optional[Dict[str, Any]] = None,
    ) -> ProductSnapshot:
        """将产品页和 FundArr 当前行合并为结构化产品快照。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._product_fields_from_row(row or {}, code)
        for key, value in cls._product_fields_from_html(soup).items():
            fields.setdefault(key, value)
        title = cls._clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title:
            fields["产品页标题"] = title
        category = soup.select_one("#categaryId2")
        if category and category.get("value"):
            fields["公告分类ID"] = cls._clean(category.get("value"))
        name = fields.get("基金简称") or title or str(code)
        # Keep the canonical fields useful even when the catalogue row omitted
        # one of them and the product page supplied the Chinese label.
        aliases = {
            "基金简称": ("基金简称", "简称"),
            "基金全称": ("基金全称", "法定名称"),
            "基金类型": ("基金类型",),
            "风险等级": ("风险等级", "产品风险等级"),
            "成立日期": ("成立日期",),
            "最新净值日期": ("最新净值日期", "净值日期"),
            "官网交易状态": ("官网交易状态", "交易状态"),
        }
        for canonical, candidates in aliases.items():
            if not fields.get(canonical):
                fields[canonical] = next((fields[item] for item in candidates if fields.get(item)), "")
        name = fields.get("基金简称") or title or str(code)
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=fields.get("基金全称", ""),
            fund_type=fields.get("基金类型", ""),
            risk_level=fields.get("风险等级", ""),
            inception_date=cls._normalize_date(fields.get("成立日期")),
            asset_scale=fields.get("lastasset") or fields.get("最新规模", ""),
            net_value_date=fields.get("最新净值日期", ""),
            trade_status=fields.get("官网交易状态", ""),
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        row = self._catalogue().get(fund.code, {})
        product_url = urljoin(self.home_url, str(row.get("url") or "")) if row.get("url") else fund.source_url
        if not product_url or product_url.endswith("/"):
            kind = self._row_kind(row)
            product_url = self.product_url_template.format(kind=kind, code=fund.code)
        response = self.session.get(product_url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("万家基金产品页需要认证")
        response.raise_for_status()
        return self.parse_product_html(response.content, fund.code, response.url or product_url, self._observed_at(), row=row)

    @staticmethod
    def _state_bool(value: Any) -> Optional[bool]:
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "open", "正常", "开放"}:
            return True
        if text in {"0", "false", "no", "closed", "暂停", "关闭"}:
            return False
        # The product page commonly uses compound labels such as
        # ``正常开放``/``暂停申购`` rather than the short values above.
        if any(token in text for token in ("暂停", "关闭", "停止", "不可")):
            return False
        if any(token in text for token in ("开放", "正常", "可申购", "可赎回")):
            return True
        return None

    @staticmethod
    def _jsonp_payload(text: str) -> Optional[Dict[str, Any]]:
        value = str(text or "").strip().rstrip(";").strip()
        # Some deployments honour the callback parameter and some return the
        # same object as plain JSON.  Both are public responses, so accept
        # either shape without evaluating JavaScript.
        candidates = [value]
        match = re.match(r"^[\w$.-]+\s*\(\s*(\{.*\})\s*\)$", value, re.S)
        if match:
            candidates.insert(0, match.group(1))
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    @classmethod
    def _payload_value(cls, payload: Dict[str, Any], *keys: str) -> Any:
        """Case-insensitive lookup for slightly different official releases."""
        if not isinstance(payload, dict):
            return None
        lowered = {str(key).lower(): value for key, value in payload.items()}
        for key in keys:
            if key in payload:
                return payload[key]
            if key.lower() in lowered:
                return lowered[key.lower()]
        return None

    def _fetch_trade_state(self, code: str, referer: str) -> Optional[Dict[str, Any]]:
        response = self.session.get(
            self.trade_state_url,
            params={"fundCode": code, "call": "wjAdapterCallback"},
            headers=self._headers(referer),
            timeout=min(self.timeout, 10),
        )
        if response.status_code in {401, 403}:
            raise PermissionError("万家基金交易状态接口需要认证")
        response.raise_for_status()
        payload = self._jsonp_payload(response.text)
        result_code = self._payload_value(payload, "resultCode", "result_code", "code")
        if not payload or str(result_code or "") != "ETS-5BP0000":
            return None
        state = self._payload_value(payload, "fundState", "fund_state")
        return state if isinstance(state, dict) else None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        state: Optional[Dict[str, Any]] = None
        state_error = ""
        try:
            state = self._fetch_trade_state(fund.code, product.source_url)
            if state is None:
                state_error = "万家官网交易状态 JSONP 返回错误页或未返回 fundState"
        except (requests.RequestException, PermissionError, ValueError) as exc:
            state_error = str(exc)
        raw = dict(product.fields)
        if state:
            raw.update({f"trade_{key}": self._clean(value) for key, value in state.items()})
        if state_error:
            raw["trade_state_error"] = state_error
        fallback_status = self._state_bool(product.fields.get("官网交易状态") or product.fields.get("statusStr"))
        subscription = self._state_bool(self._payload_value(state, "declarestate", "declareState", "subscription")) if state else fallback_status
        sip = self._state_bool(self._payload_value(state, "valuagrstate", "valueAgrState", "sip")) if state else self._state_bool(product.fields.get("valuagrstate"))
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="万家基金官网 trade!fundState 公开 JSONP" if state else "万家基金官网产品页公开交易状态",
            subscription=subscription,
            redemption=self._state_bool(self._payload_value(state, "withdrawstate", "withdrawState", "redemption")) if state else None,
            sip=sip,
            quota_remark=(
                "交易状态来自万家官网匿名 fundState JSONP；不访问 trade.wjasset.com 登录/提交入口。"
                if state else f"产品页状态可读，但 fundState 匿名接口未稳定返回；{state_error or '保留产品页状态'}。"
            ),
            raw=raw,
        )
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel],
            api_status=0 if state else None,
            message=self._clean(product.trade_status),
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw=raw,
        )

    @classmethod
    def parse_notice_payload(cls, payload: Any, source_url: str = "") -> List[Dict[str, str]]:
        # The CMS has returned ``contents`` at the top level and nested under
        # ``data`` in different public releases.  Locate only list-of-object
        # containers, never execute or infer data from arbitrary HTML.
        rows: List[Dict[str, Any]] = []

        def collect(value: Any, depth: int = 0) -> None:
            if depth > 3:
                return
            if isinstance(value, list):
                if all(isinstance(item, dict) for item in value):
                    rows.extend(item for item in value if isinstance(item, dict))
                return
            if not isinstance(value, dict):
                return
            for key in ("contents", "data", "result", "list", "records", "rows"):
                if key in value:
                    collect(value[key], depth + 1)

        collect(payload)
        output: List[Dict[str, str]] = []
        for item in rows:
            title = cls._clean(
                item.get("title")
                or item.get("contentTitle")
                or item.get("content_title")
                or item.get("name")
            )
            compact = re.sub(r"\s+", "", title)
            if not re.search(r"大额申购|大额定投|定期定额投资|限制申购|申购限制|金额限制|暂停申购|恢复申购", compact):
                continue
            if not re.search(r"调整|暂停|恢复|限制", compact):
                continue
            href = cls._clean(
                item.get("url")
                or item.get("contentUrl")
                or item.get("content_url")
                or item.get("downloadUrl")
                or item.get("attachmentUrl")
                or item.get("href")
                or item.get("link")
            )
            if not href:
                continue
            output.append({
                "title": title,
                "date": cls._date_from_ms(
                    item.get("activationDate")
                    or item.get("publishDate")
                    or item.get("publishDateTime")
                    or item.get("date")
                ),
                "url": cls._official_url(href, source_url),
                "content_id": cls._clean(item.get("contentId") or item.get("content_id") or item.get("id")),
            })
        output.sort(key=lambda item: item.get("date", ""), reverse=True)
        return output

    @classmethod
    def _sequence_after(cls, text: str, label: str, pattern: str) -> List[str]:
        flexible_label = r"\s*".join(re.escape(char) for char in label if not char.isspace())
        match = re.search(flexible_label + r"\s*((?:(?:" + pattern + r")\s*)+)", text, re.I)
        return re.findall(pattern, match.group(1)) if match else []

    @classmethod
    def _values_after(cls, text: str, label: str) -> List[str]:
        """Extract amount/status cells following one PDF table label.

        PDF text extraction often turns ``50 万`` into two tokens and inserts
        whitespace between every Chinese character.  Keep the unit attached
        here so ``50 万`` remains ``50万`` rather than becoming ``50元``.
        """
        flexible_label = r"\s*".join(re.escape(char) for char in label if not char.isspace())
        match = re.search(flexible_label + r"\s*", text, re.I)
        if not match:
            return []
        tail = text[match.end():]
        # Stop before the next table section or the explanatory paragraphs.
        stop = re.search(
            r"(?:\s+(?:该分级基金|下属(?:分级)?基金|2[.、]|其他需要提示|公告日期|公告送出日期))",
            tail,
            re.I,
        )
        if stop:
            tail = tail[:stop.start()]
        token = r"(?:\d[\d,]*(?:\.\d+)?\s*(?:亿元|亿|万元|万|元)?|不限|无限制|暂停|是|否|-|—)"
        return [re.sub(r"\s+", "", value) for value in re.findall(token, tail, re.I)]

    @classmethod
    def _limit_from_cell(cls, value: str) -> Optional[str]:
        value = re.sub(r"\s+", "", str(value or ""))
        if value in {"不限", "无限制"}:
            return "不限"
        if value in {"暂停"}:
            return "暂停"
        if value in {"", "-", "—"}:
            return None
        # A unit-less cell is in RMB yuan because the official table label
        # states the unit.  Explicit 万/万元/亿 units are retained.
        return _amount(value if re.search(r"(?:亿元|亿|万元|万|元)$", value) else f"{value}元")

    @classmethod
    def parse_announcement_text(
        cls,
        text: str,
        code: str,
        title: str = "",
        announcement_date: str = "",
        source_url: str = "",
    ) -> Optional[Dict[str, str]]:
        """解析万家公告中的当前份额限制，并按官方来源归入直销结果。"""
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        title_text = cls._clean(title)
        compact = re.sub(r"\s+", "", title_text + normalized)
        codes = cls._sequence_after(normalized, "下属分级基金的交易代码", r"\d{6}")
        if not codes:
            codes = cls._sequence_after(normalized, "下属基金份额的交易代码", r"\d{6}")
        amount_cells = cls._values_after(
            normalized,
            "下属分级基金的限制申购（含定期定额投资）金额（单位：人民币元）",
        )
        if not amount_cells:
            amount_cells = cls._values_after(
                normalized,
                "下属基金份额的限制申购（含定期定额投资）金额（单位：人民币元）",
            )
        amounts = [cls._limit_from_cell(item) for item in amount_cells]
        if not amounts:
            amounts = []
        pause_cells = cls._values_after(
            normalized,
            "该分级基金是否暂停大额申购（含定期定额投资）",
        )
        if not pause_cells:
            pause_cells = cls._values_after(
                normalized,
                "该分级基金是否暂停大额申购",
            )
        limit: Optional[str] = None
        remark = ""
        status = cls.STATUS_OK
        if code in codes:
            index = codes.index(code)
            if index < len(amounts):
                limit = amounts[index]
                remark = "万家基金官网公告按下属份额交易代码列出限制申购（含定期定额投资）金额；按官网一手来源归入直销结果。"
            if index < len(pause_cells) and pause_cells[index] in {"是", "暂停", "Y", "yes", "true", "1"}:
                status = cls.STATUS_PAUSED
        if limit is None:
            generic = re.search(
                r"限制申购(?:（含定期定额投资）)?金额(?:（单位：人民币元）)?\s*([\d,]+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)?",
                normalized,
            )
            if generic:
                limit = cls._limit_from_cell("".join(item for item in generic.groups() if item))
                remark = "万家基金官网公告明确限制申购（含定期定额投资）金额；按官网一手来源归入直销结果。"
        if limit is None:
            # The same generic row may contain whitespace inserted inside the
            # Chinese label by PDF extraction; use the tolerant label parser
            # as a final official-text fallback.
            generic_cells = cls._values_after(
                normalized,
                "限制申购（含定期定额投资）金额（单位：人民币元）",
            )
            if generic_cells:
                limit = cls._limit_from_cell(generic_cells[0])
                remark = "万家基金官网公告明确限制申购（含定期定额投资）金额；按官网一手来源归入直销结果。"
        if limit is None and re.search(r"恢复(?:大额申购|申购|定期定额投资)", compact):
            limit = "不限"
            remark = "万家基金官网公告明确恢复大额申购/定投业务，未列金额上限。"
        if limit is None and re.search(r"暂停(?:大额申购|申购|定期定额投资)", compact):
            limit = "暂停"
            remark = "万家基金官网公告明确暂停大额申购/定投业务。"
            status = cls.STATUS_PAUSED
        if limit is None:
            return None
        return {
            "limit": limit,
            "status": cls.STATUS_PAUSED if limit == "暂停" else status,
            "quota_type": "单日单个基金账户累计申购（含定期定额投资）",
            "quota_remark": remark,
            "announcement_title": title_text,
            "announcement_date": cls._normalize_date(announcement_date),
            "source_url": source_url,
        }

    def _fetch_notice(
        self,
        fund: FundIdentity,
        category_id: str,
        product_url: str,
    ) -> Optional[Dict[str, str]]:
        if not category_id:
            return None
        for page_number in range(1, 11):
            response = self.session.post(
                self.notice_api_url,
                params={"noCache": str(int(self.clock().timestamp() * 1000))},
                data={"categoryId": category_id, "pageNumber": page_number, "pageSize": 30},
                headers=self._headers(product_url, json_request=True),
                timeout=self.timeout,
            )
            if response.status_code in {401, 403}:
                raise PermissionError("万家基金公告列表接口需要认证")
            response.raise_for_status()
            payload = response.json()
            candidates = self.parse_notice_payload(payload, response.url or self.notice_api_url)
            for item in candidates:
                try:
                    attachment = self.session.get(
                        item["url"], headers=self._headers(product_url), timeout=max(self.timeout, 30)
                    )
                    if attachment.status_code in {401, 403}:
                        raise PermissionError("万家基金公告 PDF 需要认证")
                    attachment.raise_for_status()
                    text = _extract_office_text(attachment.content, item["url"])
                except PermissionError:
                    raise
                except (requests.RequestException, PdfReadError, ValueError, OSError):
                    continue
                parsed = self.parse_announcement_text(
                    text, fund.code, item["title"], item["date"], item["url"]
                )
                if parsed:
                    parsed["announcement_text"] = text
                    return parsed
            total_page = int(payload.get("totalPage") or page_number) if isinstance(payload, dict) else page_number
            if page_number >= total_page or not (payload.get("contents") if isinstance(payload, dict) else None):
                break
        return None

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        observed = self._observed_at()
        try:
            product = self.fetch_product(fund)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="万家基金官网产品页/公告列表/PDF",
                limit=None,
                status=self.STATUS_REQUIRES_AUTH,
                quota_remark=str(exc),
                source_url=fund.source_url,
                observed_at=observed,
                raw={"error": str(exc)},
            )
        category_id = product.fields.get("公告分类ID", "")
        try:
            notice = self._fetch_notice(fund, category_id, product.source_url)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="万家基金官网公告列表/PDF",
                limit=None,
                status=self.STATUS_REQUIRES_AUTH,
                quota_remark=str(exc),
                source_url=product.source_url,
                observed_at=observed,
                raw={"error": str(exc), **product.fields},
            )
        if notice is None:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="万家基金官网公告列表/PDF",
                limit=None,
                status=self.STATUS_NO_DATA,
                quota_remark=(
                    "万家产品页可访问，但当前产品公告列表未解析到最新大额申购/定投限额；"
                    "不沿用历史公告值。"
                ),
                source_url=product.source_url,
                observed_at=observed,
                raw=product.fields,
            )
        raw = dict(product.fields)
        raw.update(notice)
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="万家基金官网最新公告 PDF",
            limit=notice["limit"],
            status=notice["status"],
            quota_type=notice["quota_type"],
            quota_remark=notice["quota_remark"],
            source_url=notice["source_url"],
            observed_at=observed,
            raw=raw,
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == self.STATUS_NO_DATA:
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
            status="public_with_trade_boundary",
            reason=(
                "万家官网 FundArr 公开目录、产品页、公告列表接口和公告 PDF 可匿名读取；"
                "官网交易状态 JSONP 公开接口偶尔返回错误页。实际申购/定投提交位于 "
                "trade.wjasset.com，可能要求登录、验证码、设备校验或绑定银行卡，"
                "本适配器不访问或绕过该边界。"
            ),
            requires_login=True,
            requires_captcha=False,
        )


__all__ = ["WanjiaAdapter"]
