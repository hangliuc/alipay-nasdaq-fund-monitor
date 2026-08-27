"""大成基金官网独立 Adapter。

大成官网前端公开使用 ``/servlet/json``：742001 返回基金目录，742002 返回
产品详情，742003 返回单只基金公告列表。公告附件是官网 PDF；适配器按公告
日期倒序寻找最新的“调整/暂停/恢复大额申购（含定期定额投资）”公告，并
按份额代码解析直销金额。

请求严格复用官网公开的匿名参数，不登录、不签名、不访问交易提交接口，也
不使用历史硬编码值或第三方平台数据。
"""

from datetime import datetime, timezone
from io import BytesIO
import random
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


class DachengAdapter:
    """大成官网公开目录、产品详情和公告 PDF Adapter。"""

    manager_id = "大成"
    home_url = "https://www.dcfund.com.cn/"
    catalogue_url = "https://www.dcfund.com.cn/main/fund/index.shtml"
    api_url = "https://www.dcfund.com.cn/servlet/json"
    product_url_template = "https://www.dcfund.com.cn/main/fund/productdetail/index.shtml?product_code={code}"
    source_type_catalogue = "dacheng_official_fund_catalogue_api"
    source_type_product = "dacheng_official_product_api"
    source_type_notice = "dacheng_official_notice_pdf"
    fund_list_func_no = "742001"
    fund_detail_func_no = "742002"
    announcement_func_no = "742003"

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
        return {**HEADERS, "Referer": referer or cls.home_url, "X-Requested-With": "XMLHttpRequest"}

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

    @classmethod
    def _rows_from_results(cls, results: Any) -> List[Dict[str, Any]]:
        if isinstance(results, list):
            if results and isinstance(results[0], dict) and isinstance(results[0].get("data"), list):
                return [row for row in results[0]["data"] if isinstance(row, dict)]
            return [row for row in results if isinstance(row, dict)]
        if isinstance(results, dict):
            for key in ("data", "rows", "list", "results"):
                value = results.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        return []

    @classmethod
    def parse_catalogue_payload(cls, payload: Dict[str, Any], source_url: str = "") -> List[FundIdentity]:
        found: Dict[str, FundIdentity] = {}
        for row in cls._rows_from_results(payload.get("results") if isinstance(payload, dict) else None):
            code = str(row.get("product_code") or row.get("fund_code") or "").strip()
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = cls._clean(row.get("product_abbr") or row.get("product_name") or code)
            found[code] = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=cls._clean(row.get("product_type2") or row.get("product_type")),
                share_class=cls._share_class(name),
                source_url=cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def parse_product_payload(cls, payload: Dict[str, Any], code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        rows = cls._rows_from_results(payload.get("results") if isinstance(payload, dict) else None)
        row = rows[0] if rows else {}
        fields = {str(key): cls._clean(value) for key, value in row.items()}
        name = cls._clean(row.get("product_abbr") or row.get("product_name") or code)
        full_name = cls._clean(row.get("product_name") or name)
        status = cls._clean(row.get("product_status_text") or row.get("product_status"))
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=cls._clean(row.get("product_type2") or row.get("product_type")),
            risk_level=cls._clean(row.get("p_risk_level_text") or row.get("risk_level_text")),
            inception_date=cls._normalize_date(row.get("found_date")),
            asset_scale=cls._clean(row.get("newest_asset") or row.get("scale")),
            net_value_date=cls._normalize_date(row.get("nav_date")),
            trade_status=status,
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def _request_json(self, params: Dict[str, Any], referer: str = "") -> Dict[str, Any]:
        query = {**params, "random": f"{random.random():.16f}"}
        response = self.session.get(self.api_url, params=query, headers=self._headers(referer), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("大成基金官网接口需要认证")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("大成基金官网接口返回格式异常")
        error_info = self._clean(payload.get("error_info"))
        if error_info and "调用成功" not in error_info:
            raise RuntimeError(f"大成基金官网接口失败：{error_info}")
        return payload

    def discover_funds(self) -> List[FundIdentity]:
        payload = self._request_json(
            {"funcNo": self.fund_list_func_no, "query_type": "1"}, self.catalogue_url
        )
        return self.parse_catalogue_payload(payload, self.api_url)

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        payload = self._request_json(
            {"funcNo": self.fund_detail_func_no, "product_code": fund.code},
            self.product_url_template.format(code=fund.code),
        )
        return self.parse_product_payload(
            payload, fund.code, self.product_url_template.format(code=fund.code), self._observed_at()
        )

    @staticmethod
    def _status_bool(value: str) -> Optional[bool]:
        text = str(value or "")
        if re.search(r"正常|开放", text):
            return True
        if re.search(r"暂停|封闭|停止", text):
            return False
        return None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        status = self._status_bool(product.trade_status)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="大成基金官网产品详情 API",
            subscription=status,
            redemption=None,
            sip=status,
            quota_remark="产品状态来自大成官网 742002；直销限额来自最新 742003 公告 PDF。",
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
    def parse_announcement_list_payload(cls, payload: Dict[str, Any]) -> List[Dict[str, str]]:
        candidates = []
        for row in cls._rows_from_results(payload.get("results") if isinstance(payload, dict) else None):
            title = cls._clean(row.get("title"))
            if not re.search(r"大额申购|定期定额投资", title):
                continue
            if not re.search(r"调整|暂停|恢复", title):
                continue
            href = cls._clean(row.get("attachment_url") or row.get("attachmentUrl"))
            if not href:
                continue
            candidates.append({
                "title": title,
                "date": cls._normalize_date(row.get("pub_date") or row.get("publish_date")),
                "url": urljoin(cls.home_url, href),
            })
        candidates.sort(key=lambda item: item.get("date", ""), reverse=True)
        return candidates

    @classmethod
    def parse_announcement_text(
        cls, text: str, code: str, title: str = "", announcement_date: str = "", source_url: str = ""
    ) -> Optional[Dict[str, str]]:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        title_text = cls._clean(title)
        direct = re.search(
            r"(?:本公司直销渠道|通过本公司直销渠道|直销渠道).*?(?:不超过|低于|等于或低于)\s*([\d,.]+)\s*(元|万元|万|亿元|亿)",
            normalized,
            re.S,
        )
        limit: Optional[str] = _amount("".join(direct.groups())) if direct else None
        remark = ""
        if limit:
            remark = "大成官网公告明确本公司直销渠道单日累计申购及定投金额。"
        if limit is None:
            code_match = re.search(r"(?:下属基金份额的交易代码|下属分级基金的交易代码)\s*((?:\d{6}\s*)+)", normalized)
            amount_match = re.search(
                r"(?:下属基金份额的限制金额|下属分级基金的限制申购金额).*?((?:[\d,.]+\s*)+)(?=\s*(?:2\.|其他需要|下属|$))",
                normalized,
            )
            if code_match and amount_match:
                codes = re.findall(r"\d{6}", code_match.group(1))
                amounts = re.findall(r"[\d,.]+", amount_match.group(1))
                if code in codes:
                    index = codes.index(code)
                    if index < len(amounts):
                        limit = _amount(f"{amounts[index]}元")
                        remark = "大成官网公告按份额代码列出限制金额，且公告未使用第三方数据。"
        if limit is None:
            generic = re.search(r"(?:限制申购金额|限制金额)[^\d]{0,80}([\d,.]+)\s*(元|万元|万|亿元|亿)", normalized)
            if generic:
                limit = _amount("".join(generic.groups()))
                remark = "大成官网公告明确的限制申购金额；按一手官网来源归入直销结果。"
        if limit is None and re.search(r"恢复正常办理大额申购|恢复大额申购", title_text + normalized):
            limit, remark = "不限", "大成官网公告明确恢复大额申购/定投，未列金额上限。"
        if limit is None and re.search(r"暂停大额申购", title_text + normalized):
            limit, remark = "暂停", "大成官网公告明确暂停大额申购/定投。"
        if limit is None:
            return None
        return {
            "limit": limit,
            "status": "ok",
            "quota_type": "单日单个基金账户累计申购及定期定额投资",
            "quota_remark": remark,
            "announcement_title": title_text,
            "announcement_date": cls._normalize_date(announcement_date),
            "source_url": source_url,
        }

    def _fetch_notice(self, code: str) -> Optional[Dict[str, str]]:
        payload = self._request_json(
            {
                "funcNo": self.announcement_func_no,
                "product_code": code,
                "curtPageNo": "1",
                "numPerPage": "50",
                "select_time": "",
                "start_date": "",
                "end_date": "",
                "ann_type": "",
                "key_word": "",
            },
            self.product_url_template.format(code=code),
        )
        for item in self.parse_announcement_list_payload(payload):
            response = self.session.get(item["url"], headers=self._headers(self.api_url), timeout=max(self.timeout, 25))
            if response.status_code in {401, 403}:
                raise PermissionError("大成基金公告 PDF 需要认证")
            response.raise_for_status()
            try:
                pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
            except (PdfReadError, ValueError, OSError):
                continue
            parsed = self.parse_announcement_text(pdf_text, code, item["title"], item["date"], item["url"])
            if parsed:
                parsed["announcement_text"] = pdf_text
                return parsed
        return None

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        try:
            product = self.fetch_product(fund)
            notice = self._fetch_notice(fund.code)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="大成官网产品详情 API/公告 PDF", limit=None,
                status="official_interface_requires_auth", quota_remark=str(exc),
                source_url=self.product_url_template.format(code=fund.code), observed_at=self._observed_at(),
                raw={"error": str(exc)},
            )
        if notice is None:
            return DirectLimitSnapshot(
                manager_id=self.manager_id, code=fund.code, customer_type="individual",
                channel="大成官网公告 API/PDF", limit=None, status="official_api_no_data",
                quota_remark="产品详情可访问，但未从当前公告列表解析到最新有效限额；不沿用历史公告值。",
                source_url=product.source_url, observed_at=product.observed_at, raw=product.fields,
            )
        raw = dict(product.fields)
        raw.update(notice)
        return DirectLimitSnapshot(
            manager_id=self.manager_id, code=fund.code, customer_type="individual",
            channel="大成基金官网最新公告 PDF", limit=notice["limit"], status=notice["status"],
            quota_type=notice["quota_type"], quota_remark=notice["quota_remark"],
            source_url=notice["source_url"], observed_at=product.observed_at, raw=raw,
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(snapshot.limit or "未获取", "大成基金官网公告 API/PDF", snapshot.source_url,
                       snapshot.quota_remark, status=snapshot.status)

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="大成 742001/742002/742003 与公告 PDF 可匿名访问；未访问登录交易入口或提交交易。",
        )
