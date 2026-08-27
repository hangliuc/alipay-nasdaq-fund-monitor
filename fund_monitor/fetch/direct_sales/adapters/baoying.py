"""宝盈基金官网独立 Adapter。

宝盈网上交易前端 ``ibao.byfunds.com`` 暴露了两组无需登录的公开接口：

* ``common/session``：建立官网前端使用的匿名会话；
* ``trade/fund/list``：返回当前网上交易基金目录；
* ``fund/detail``：返回单只基金的净值、交易状态和申购业务限额。

接口要求前端公开的 ``_sign``、``_msgid``、``token`` 和 ``g_systemtype``。
本模块只复现公开前端调用，不登录、不提交交易，也不绕过会话认证。若官网
返回 ``9999999/会话超时``，结果明确标记为 ``official_interface_requires_auth``。
"""

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Callable, Dict, Iterable, List, Optional
import uuid

import requests

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


class BaoyingAdapter:
    """宝盈官网网上交易目录、详情和限额 Adapter。"""

    manager_id = "宝盈"
    home_url = "https://ibao.byfunds.com/"
    api_base_url = "https://ibao.byfunds.com/agate/api/v1"
    session_path = "/common/session"
    catalogue_path = "/trade/fund/list"
    detail_path = "/fund/detail"
    source_type_catalogue = "baoying_official_trade_fund_list"
    source_type_trade_api = "baoying_official_trade_api"
    signing_key = "unH2jWfy"
    success_code = "0000000"
    auth_code = "9999999"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._token: Optional[str] = None

    def _observed_at(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def sign(cls, params: Dict[str, Any]) -> str:
        """按官网前端公开规则生成大写 MD5 ``_sign``。"""
        values = dict(params)
        values["_key"] = cls.signing_key
        plain = "&".join(f"{key}={values[key] or ''}" for key in sorted(values))
        return hashlib.md5(plain.encode("utf-8")).hexdigest().upper()

    @classmethod
    def _request_headers(cls, query: Dict[str, Any], token: str = "") -> Dict[str, str]:
        msgid = str(query.get("_msgid") or "")
        headers = {
            **HEADERS,
            "Referer": cls.home_url,
            "Origin": "https://ibao.byfunds.com",
            "_msgid": msgid,
            "_sign": cls.sign(query),
        }
        if token:
            headers.update({"token": token, "g_systemtype": "2"})
        return headers

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None, token: str = "") -> Dict[str, Any]:
        query = dict(params or {})
        query["_msgid"] = str(uuid.uuid4())
        response = self.session.get(
            f"{self.api_base_url}{path}",
            params=query,
            headers=self._request_headers(query, token),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("宝盈 API 返回不是 JSON 对象")
        return payload

    @staticmethod
    def _first_record(payload: Dict[str, Any]) -> Dict[str, Any]:
        rows = payload.get("record") or payload.get("data") or []
        # 宝盈接口使用 record=[[{...}, ...], [{...}]] 的二维结构。
        if isinstance(rows, list) and rows and isinstance(rows[0], list):
            rows = rows[0]
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return dict(rows[0])
        if isinstance(rows, dict):
            return dict(rows)
        return {}

    @classmethod
    def _extract_rows(cls, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = payload.get("record") or payload.get("data") or []
        if isinstance(rows, list) and rows and isinstance(rows[0], list):
            rows = rows[0]
        if isinstance(rows, dict):
            rows = [rows]
        return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def open_session(self) -> str:
        """建立官网匿名 session 并返回 token；不执行登录。"""
        # 首页请求用于保持与官网浏览器相同的 cookie/origin 上下文。
        response = self.session.get(self.home_url, headers=HEADERS, timeout=self.timeout)
        response.raise_for_status()
        payload = self._get(self.session_path)
        if str(payload.get("code")) != self.success_code:
            if str(payload.get("code")) == self.auth_code:
                raise PermissionError("宝盈公开 session 需要认证")
            raise RuntimeError(str(payload.get("msg") or "宝盈 session 创建失败"))
        row = self._first_record(payload)
        token = str(row.get("token") or "")
        if not token:
            raise PermissionError("宝盈公开 session 未返回 token")
        self._token = token
        return token

    @classmethod
    def _catalogue_params(cls) -> Dict[str, str]:
        # 参数名和默认值来自宝盈网上交易前端 trade/fund/list 调用。
        return {
            "fundtype": "",
            "risklevel": "",
            "managerid": "",
            "ordertype": "",
            "isasc": "",
            "qryallfund": "",
            "flag": "",
            "keyword": "",
            "pagecount": "1000",
            "pageoffset": "0",
            "pinyingflag": "1",
        }

    @classmethod
    def _share_class(cls, name: str, raw: str = "") -> str:
        match = re.search(r"([A-Z])$", name.strip())
        if match:
            return match.group(1)
        return str(raw or "").strip()

    @classmethod
    def _source_url(cls, code: str) -> str:
        return f"{cls.api_base_url}{cls.detail_path}?fundcode={code}"

    def discover_funds(self) -> List[FundIdentity]:
        """发现当前宝盈网上交易公开目录中的全部基金份额。"""
        token = self._token or self.open_session()
        payload = self._get(self.catalogue_path, self._catalogue_params(), token)
        if str(payload.get("code")) == self.auth_code:
            raise PermissionError("宝盈基金目录接口返回会话超时/需认证")
        if str(payload.get("code")) != self.success_code:
            raise RuntimeError(str(payload.get("msg") or "宝盈基金目录接口失败"))
        found: Dict[str, FundIdentity] = {}
        for row in self._extract_rows(payload):
            code = str(row.get("fundcode") or "").strip()
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = str(row.get("fundname") or row.get("fund_shortname") or code).strip()
            found.setdefault(
                code,
                FundIdentity(
                    manager_id=self.manager_id,
                    code=code,
                    name=name,
                    fund_type=str(row.get("fundtype") or ""),
                    share_class=self._share_class(name, str(row.get("sharetype") or "")),
                    source_url=self._source_url(code),
                    source_type=self.source_type_catalogue,
                ),
            )
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def identity_from_config(cls, fund: Dict[str, Any]) -> FundIdentity:
        code = str(fund["code"])
        name = str(fund.get("name") or fund.get("display") or code)
        return FundIdentity(
            manager_id=cls.manager_id,
            code=code,
            name=name,
            share_class=cls._share_class(name),
            source_url=cls._source_url(code),
            source_type="config_compatibility",
        )

    @staticmethod
    def _normalize_date(value: Any) -> str:
        text = str(value or "").strip()
        if re.fullmatch(r"\d{8}", text):
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
        return text

    @staticmethod
    def _positive_amounts(detail: Dict[str, Any], keys: Iterable[str]) -> List[float]:
        values: List[float] = []
        for key in keys:
            raw = detail.get(key)
            try:
                number = float(str(raw).replace(",", ""))
                if number > 0:
                    values.append(number)
            except (TypeError, ValueError):
                continue
        return values

    @classmethod
    def parse_detail(
        cls,
        payload: Dict[str, Any],
        code: str,
        source_url: str,
        observed_at: str,
    ) -> Dict[str, Any]:
        """解析详情接口，保留原始字段并给出标准化的限额字段。"""
        api_code = str(payload.get("code") or "")
        if api_code == cls.auth_code:
            return {"api_status": "official_interface_requires_auth", "message": str(payload.get("msg") or "会话超时"), "detail": {}}
        if api_code != cls.success_code:
            return {"api_status": "official_api_error", "message": str(payload.get("msg") or ""), "detail": {}}
        detail = cls._first_record(payload)
        if str(detail.get("fundcode") or code) != code:
            return {"api_status": "official_api_no_data", "message": "基金代码不匹配", "detail": detail}
        values = cls._positive_amounts(detail, ("td_sum_max_20", "td_sum_max_22"))
        limit = _amount(f"{min(values)}元") if values else None
        return {
            "api_status": "ok",
            "message": str(payload.get("msg") or ""),
            "detail": detail,
            "limit": limit,
            "source_url": source_url,
            "observed_at": observed_at,
        }

    def _fetch_detail_payload(self, fund: FundIdentity) -> tuple[Dict[str, Any], str, str]:
        token = self._token or self.open_session()
        url = self._source_url(fund.code)
        payload = self._get(self.detail_path, {"fundcode": fund.code}, token)
        return payload, url, self._observed_at()

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        payload, url, observed_at = self._fetch_detail_payload(fund)
        parsed = self.parse_detail(payload, fund.code, url, observed_at)
        detail = parsed.get("detail") or {}
        fields = {str(key): str(value) for key, value in detail.items() if value not in (None, "")}
        return ProductSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            name=str(detail.get("fundname") or fund.name),
            full_name=str(detail.get("fund_fullname") or ""),
            fund_type=str(detail.get("fundtype") or ""),
            risk_level=str(detail.get("risklevel") or ""),
            inception_date=self._normalize_date(detail.get("establishdate") or detail.get("foundeddate")),
            asset_scale=str(detail.get("fundsize") or ""),
            net_value_date=self._normalize_date(detail.get("navdate")),
            trade_status=str(detail.get("fundstatus") or detail.get("status") or ""),
            source_url=url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        payload, url, observed_at = self._fetch_detail_payload(fund)
        parsed = self.parse_detail(payload, fund.code, url, observed_at)
        detail = parsed.get("detail") or {}
        limit = parsed.get("limit") or ""
        status_value = str(detail.get("fundstatus") or detail.get("status") or "")
        subscription = None if parsed.get("api_status") != "ok" else status_value not in {"4", "6", "暂停"}
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="宝盈网上交易",
            subscription=subscription,
            limit=str(limit or ""),
            quota_type="td_sum_max_20/td_sum_max_22",
            quota_remark="申购业务 20/22",
            raw=detail,
        )
        api_status = 0 if parsed.get("api_status") == "ok" else None
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel] if detail or parsed.get("api_status") == "ok" else [],
            api_status=api_status,
            message=str(parsed.get("message") or ""),
            source_url=url,
            observed_at=observed_at,
            raw=payload,
        )

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        payload, url, observed_at = self._fetch_detail_payload(fund)
        parsed = self.parse_detail(payload, fund.code, url, observed_at)
        detail = parsed.get("detail") or {}
        status = str(parsed.get("api_status") or "official_api_error")
        limit = parsed.get("limit")
        if status == "ok" and not limit:
            status = "official_api_no_data"
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="宝盈网上交易",
            limit=limit,
            status=status,
            quota_type="td_sum_max_20/td_sum_max_22",
            quota_remark="申购业务 20/22；取两个正值中的较小值",
            source_url=url,
            observed_at=observed_at,
            raw=detail or payload,
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "ok":
            note = "明确渠道：个人客户/宝盈网上交易；结果来自宝盈官网交易 API。"
            note += "字段 td_sum_max_20/td_sum_max_22 对应官网申购业务 20/22。"
            return _record(snapshot.limit or "未获取", "宝盈官网网上交易 API", snapshot.source_url, note)
        if snapshot.status == "official_interface_requires_auth":
            return _record(
                "未获取",
                "宝盈官网网上交易 API",
                snapshot.source_url,
                "官网公开 session/详情接口返回会话超时或需认证；未绕过认证。",
                status=snapshot.status,
            )
        return None

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public_with_session_boundary",
            reason="官网前端可匿名建立 session 并查询公开基金目录/详情；若接口返回 9999999/会话超时，则必须记录认证边界，不登录、不绕过。",
            requires_login=False,
        )
