"""嘉实基金官网独立 Adapter。

嘉实官网基金列表页使用 ``/servlet/json`` 的公开业务接口查询基金目录，
产品详情和购买按钮状态也由同一服务提供。官网前端公开的业务码为：

* ``741010``：基金目录/净值/当前交易状态；
* ``741011``：单只基金产品详情（以目录返回的 ``product_id`` 查询）；
* ``741044``：购买、赎回、定投等按钮状态。

直销限额使用嘉实官网公开的《基金申购上限表》。该表将“直销”和“非直销/
代销机构投资者”分列；适配器只取直销部分，并保留表格中的全部字段。这里
不把第三方平台数据写入直销结果，也不访问需要登录的交易提交页。
"""

from datetime import datetime, timezone
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

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


class JiashiAdapter:
    """嘉实官网基金目录、产品详情、交易状态和直销限额 Adapter。"""

    manager_id = "嘉实"
    home_url = "https://www.jsfund.cn/"
    catalogue_url = "https://www.jsfund.cn/main/fund/index.shtml"
    product_url_template = "https://www.jsfund.cn/main/fund/{code}/fundManager.shtml"
    limit_table_url = "https://www.jsfund.cn/main/a/20151216/191092.shtml"
    api_url = "https://www.jsfund.cn/servlet/json"
    source_type_catalogue = "jiashi_official_catalogue_api_741010"
    source_type_product = "jiashi_official_product_api_741011"
    source_type_status = "jiashi_official_status_api_741044"
    source_type_limit = "jiashi_official_direct_limit_table"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._catalogue_rows: Optional[Dict[str, Dict[str, Any]]] = None
        self._limit_rows: Optional[Dict[str, Dict[str, str]]] = None

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
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").replace("\xa0", " ").split())

    @staticmethod
    def _share_class(name: str) -> str:
        text = str(name or "").strip()
        match = re.search(r"([A-Z])(?:类)?(?:人民币|美元(?:现汇|现钞)?)?$", text)
        return match.group(1) if match else ""

    @staticmethod
    def _normalize_date(value: Any) -> str:
        text = str(value or "").strip()
        match = re.fullmatch(r"(\d{4})[-/]?(\d{1,2})[-/]?(\d{1,2})", text)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", text)
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
    def _api_rows(cls, payload: Any) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        rows = payload.get("results") or payload.get("data") or []
        if isinstance(rows, dict):
            rows = [rows]
        return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    @classmethod
    def parse_catalogue_payload(
        cls, payload: Any, source_url: str = ""
    ) -> List[FundIdentity]:
        """解析官网 ``funcNo=741010`` 的全量基金/份额目录。"""
        found: Dict[str, FundIdentity] = {}
        for row in cls._api_rows(payload):
            code = cls._clean(row.get("product_code") or row.get("fund_code"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = cls._clean(row.get("product_abbr") or row.get("product_name") or code)
            identity = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=cls._clean(row.get("fund_class") or row.get("product_type")),
                share_class=cls._share_class(name),
                source_url=cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
            old = found.get(code)
            if old is None or len(identity.name) >= len(old.name):
                found[code] = identity
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        """兼容解析官网目录页面中已渲染的基金行。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        found: Dict[str, FundIdentity] = {}
        for row in soup.select("tr[data-code], tr[data-fundcode]"):
            code = cls._clean(row.get("data-code") or row.get("data-fundcode"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            name = cells[0] if cells else code
            match = re.search(r"基金简称[：:]\s*([^\s]+)", name)
            name = match.group(1) if match else name
            found[code] = FundIdentity(
                manager_id=cls.manager_id, code=code, name=name,
                share_class=cls._share_class(name),
                source_url=cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def catalogue_params(cls, page: int = 1, page_size: int = 1000) -> Dict[str, str]:
        """返回嘉实前端 ``queryFundList``（业务码 741010）的公开参数。"""
        return {
            "funcNo": "741010",
            "cur_page": str(page),
            "num_per_page": str(page_size),
            "channel_source": "",
            "sessionId": "2",
            "hotno": "",
            "mangerno": "",
            "fundtype": "",
            "fundtype2": "",
            "transactionstatus": "",
            "riskcode": "",
            "starttime": "",
            "endtime": "",
            "keyword": "",
            "orderbystatus": "",
            "product_columns": "yieldrate",
            "select_times": "",
            "query_status_type": "1",
        }

    def _get_json(self, params: Dict[str, Any], referer: str = "") -> Dict[str, Any]:
        response = self.session.get(
            self.api_url,
            params=params,
            headers=self._headers(referer),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("嘉实基金官网公开接口需要认证")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("嘉实基金接口返回不是 JSON 对象")
        error_no = str(payload.get("error_no") or "0")
        if error_no not in {"0", "0.0", ""}:
            raise RuntimeError(str(payload.get("error_info") or "嘉实基金接口调用失败"))
        return payload

    def discover_funds(self) -> List[FundIdentity]:
        """通过嘉实官网公开目录接口发现全部当前公开基金/份额。"""
        payload = self._get_json(self.catalogue_params(), self.catalogue_url)
        rows = self._api_rows(payload)
        if not rows:
            raise RuntimeError("嘉实基金目录接口未返回基金列表")
        self._catalogue_rows = {
            self._clean(row.get("product_code")): row
            for row in rows
            if re.fullmatch(r"\d{6}", self._clean(row.get("product_code")))
        }
        return self.parse_catalogue_payload(payload, self.api_url)

    def _catalogue(self) -> Dict[str, Dict[str, Any]]:
        if self._catalogue_rows is None:
            self.discover_funds()
        return self._catalogue_rows or {}

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
    def parse_product_html(
        cls, html: bytes, code: str, source_url: str, observed_at: str
    ) -> ProductSnapshot:
        """解析嘉实产品页面的公开文本字段（API 快照是主路径）。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._fields_from_html(html)
        title = cls._clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        name = fields.get("基金简称") or fields.get("基金名称") or title or str(code)
        fields["基金代码"] = str(code)
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=fields.get("基金全称") or fields.get("基金名称", ""),
            fund_type=fields.get("基金类型", ""),
            risk_level=fields.get("风险等级", ""),
            inception_date=cls._normalize_date(fields.get("成立日期") or fields.get("基金合同生效日")),
            asset_scale=fields.get("资产规模", ""),
            net_value_date=cls._normalize_date(fields.get("净值日期") or fields.get("单位净值日期")),
            trade_status=fields.get("交易状态", ""),
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    @classmethod
    def product_from_api(
        cls, payload: Any, code: str, source_url: str, observed_at: str
    ) -> ProductSnapshot:
        rows = cls._api_rows(payload)
        raw = rows[0] if rows else {}
        fields = {str(key): cls._clean(value) for key, value in raw.items()}
        name = fields.get("product_abbr") or fields.get("product_name") or str(code)
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=fields.get("product_name", ""),
            fund_type=fields.get("fund_class_text") or fields.get("product_type_text") or fields.get("fund_class", ""),
            risk_level=fields.get("risk_level_text") or fields.get("risk_level", ""),
            inception_date=cls._normalize_date(fields.get("found_date")),
            asset_scale=fields.get("newest_asset") or fields.get("scale", ""),
            net_value_date=cls._normalize_date(fields.get("nav_date")),
            trade_status=fields.get("fund_status") or fields.get("product_status", ""),
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        row = self._catalogue().get(fund.code, {})
        product_id = self._clean(row.get("product_id"))
        if not product_id:
            # 741010 supports keyword filtering; this keeps config-only identities usable.
            params = self.catalogue_params()
            params["keyword"] = fund.code
            rows = self._api_rows(self._get_json(params, self.catalogue_url))
            row = next((item for item in rows if self._clean(item.get("product_code")) == fund.code), {})
            product_id = self._clean(row.get("product_id"))
        if not product_id:
            raise RuntimeError(f"嘉实目录未找到基金 {fund.code} 的 product_id")
        params = {
            "funcNo": "741011", "product_id": product_id, "user_id": "",
            "fund_account": "", "channel_source": "", "sessionId": "2",
        }
        payload = self._get_json(params, self.product_url_template.format(code=fund.code))
        return self.product_from_api(
            payload, fund.code, self.product_url_template.format(code=fund.code), self._observed_at()
        )

    @staticmethod
    def _flag(value: Any) -> Optional[bool]:
        text = str(value or "").strip().lower()
        if text in {"1", "y", "yes", "true", "open"}:
            return True
        if text in {"0", "n", "no", "false", "closed"}:
            return False
        return None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        params = {"funcNo": "741044", "product_codes": fund.code, "query_type": "1"}
        payload = self._get_json(params, product.source_url)
        status_rows = self._api_rows(payload)
        status = status_rows[0] if status_rows else {}
        raw = dict(product.fields)
        raw.update({f"status_{key}": self._clean(value) for key, value in status.items()})
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="嘉实官网 741044 公开交易状态 API",
            subscription=self._flag(status.get("canbuy")),
            redemption=self._flag(status.get("redeemstatus")),
            sip=self._flag(status.get("fixstatus")),
            quota_remark="交易状态来自嘉实官网公开接口 741044；不访问需要登录的交易提交入口。",
            raw=raw,
        )
        return TradeSnapshot(
            manager_id=self.manager_id, code=fund.code, channels=[channel],
            api_status=0, message=self._clean(status.get("state") or product.trade_status),
            source_url=self.api_url, observed_at=product.observed_at, raw=raw,
        )

    @classmethod
    def _direct_cell(cls, value: str) -> str:
        """从限额表单元中取直销部分，排除非直销/代销机构值。"""
        text = cls._clean(value)
        if "暂停申购" in text or "暂停定投" in text:
            return "暂停"
        if re.search(r"不限|无限额|不设上限", text):
            return "不限"
        direct = re.search(
            r"(?:直销|除代销机构投资者外)\s*[:：]\s*(.*?)(?=\s*(?:非直销|代销机构投资者)\s*[:：]|$)",
            text,
        )
        selected = direct.group(1) if direct else text
        return cls._clean(selected)

    @classmethod
    def parse_limit_table(cls, html: bytes, source_url: str = "") -> Dict[str, Dict[str, str]]:
        """解析嘉实官网公开申购上限表，返回按基金代码索引的直销字段。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        result: Dict[str, Dict[str, str]] = {}
        for table in soup.find_all("table"):
            header_row = next(
                (row for row in table.find_all("tr") if "基金代码" in cls._clean(row.get_text(" ", strip=True))),
                None,
            )
            if header_row is None:
                continue
            for row in header_row.find_next_siblings("tr"):
                cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
                if len(cells) < 3:
                    continue
                codes = re.findall(r"\d{6}", cells[0])
                if not codes:
                    continue
                purchase = cls._direct_cell(cells[2])
                limit = "暂停" if purchase == "暂停" else _amount(purchase)
                if purchase == "不限":
                    limit = "不限"
                item = {
                    "code_cell": cells[0], "name": cells[1],
                    "purchase_raw": cells[2], "transfer_raw": cells[3] if len(cells) > 3 else "",
                    "sip_raw": cells[4] if len(cells) > 4 else "",
                    "effective_date": cells[5] if len(cells) > 5 else "",
                    "remark": cells[6] if len(cells) > 6 else "",
                    "direct_value": purchase, "limit": limit,
                    "source_url": source_url,
                }
                for code in codes:
                    result[code] = dict(item, code=code)
        return result

    def _limit_rows_from_official_table(self) -> Dict[str, Dict[str, str]]:
        if self._limit_rows is not None:
            return self._limit_rows
        response = self.session.get(
            self.limit_table_url,
            headers=self._headers(self.home_url), timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("嘉实官网申购上限表需要认证")
        response.raise_for_status()
        self._limit_rows = self.parse_limit_table(response.content, response.url or self.limit_table_url)
        return self._limit_rows

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        observed = self._observed_at()
        try:
            rows = self._limit_rows_from_official_table()
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="嘉实官网申购上限表", limit=None,
                status="official_interface_requires_auth", quota_remark=str(exc),
                source_url=self.limit_table_url, observed_at=observed, raw={"error": str(exc)},
            )
        row = rows.get(fund.code)
        if not row or not row.get("limit"):
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="嘉实官网申购上限表（直销）", limit=None,
                status="official_api_no_data",
                quota_remark="嘉实官网当前公开申购上限表未列出该基金份额的可解析直销限额。",
                source_url=self.limit_table_url, observed_at=observed, raw=row or {},
            )
        return DirectLimitSnapshot(
            manager_id=self.manager_id, code=fund.code, customer_type="individual",
            channel="嘉实官网申购上限表（直销）", limit=row["limit"], status="ok",
            quota_type="单日单户累计申购（含）直销上限",
            quota_remark="取嘉实官网表格“单日单户累计申购（含）”列中的直销值；非直销/代销机构值不写入直销结果。",
            source_url=row.get("source_url") or self.limit_table_url,
            observed_at=observed, raw=row,
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(
            snapshot.limit or "未获取", snapshot.channel, snapshot.source_url,
            snapshot.quota_remark, status=snapshot.status,
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="嘉实基金目录 741010、产品详情 741011、交易状态 741044 和申购上限表均可匿名读取；官网购买/定投提交入口可能要求登录，Adapter 不访问或绕过认证。",
        )
