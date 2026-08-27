"""南方基金官网公开目录、产品详情和交易状态 Adapter。

南方官网的产品状态页使用匿名 JSON 接口返回当前基金的申购、赎回、定投
以及转换状态；基金产品页则使用同域的 ``fundList`` 和 ``overreview`` 接口
返回目录及产品资料。本模块只复现这些公开的只读请求，不登录、不提交交易，
也不把空字段猜测成“不限”。
"""

from datetime import datetime, timezone
import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

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


class SouthernAdapter:
    """南方基金官网公开交易状态与产品数据适配器。"""

    manager_id = "南方"
    home_url = "https://www.nffund.com/"
    catalogue_url = "https://www.nffund.com/new/personal-financing/fund-products.html"
    status_page_url = "https://www.nffund.com/new/transaction-guide/product-status-and-limits.html"
    catalogue_api_url = "https://www.nffund.com/nfwebApi/fund/fundList"
    status_api_url = "https://www.nffund.com/nfwebApi/customer/subscriptionAndRedemptionStatus"
    product_api_url = "https://www.nffund.com/nfwebApi/fund/overreview"
    product_url_template = "https://www.nffund.com/new/personal-financing/detail.html?fundCode={code}"
    source_type_catalogue = "southern_official_fund_catalogue_api"
    source_type_status = "southern_official_subscription_redemption_status_api"
    source_type_product = "southern_official_fund_overreview_api"
    success_code = "ETS-5BP00000"

    _catalogue_keys = (
        "g_classify_fhb",
        "g_classify_all",
        "g_classify_lcjlist",
        "g_classify_hb",
        "g_classify_fh",
        "g_classify_lc",
    )

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._catalogue_payload: Optional[Dict[str, Any]] = None
        self._status_payload: Optional[Dict[str, Any]] = None

    def _observed_at(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def _headers(cls, referer: str = "") -> Dict[str, str]:
        return {
            **HEADERS,
            "Referer": referer or cls.status_page_url,
            "Origin": "https://www.nffund.com",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _post(self, url: str, data: Optional[Dict[str, Any]], referer: str) -> Dict[str, Any]:
        response = self.session.post(
            url,
            data=data or {},
            headers=self._headers(referer),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("南方基金官方接口需要认证")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("南方基金官方接口返回不是 JSON 对象")
        code = str(payload.get("code") or "")
        if code and code != self.success_code:
            if code.upper() in {"401", "403", "AUTH", "ETS-5BP00001", "ETS-5BP99999"}:
                raise PermissionError(str(payload.get("message") or "南方基金官方接口需要认证"))
            raise RuntimeError(str(payload.get("message") or "南方基金官方接口返回失败"))
        return payload

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
    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

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
    def _identity_from_row(cls, row: Dict[str, Any], source_type: str) -> Optional[FundIdentity]:
        code = str(row.get("fundcode") or row.get("fundCode") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            return None
        name = str(row.get("fundname") or row.get("fundName") or code).strip()
        return FundIdentity(
            manager_id=cls.manager_id,
            code=code,
            name=name,
            fund_type=str(row.get("sectype") or row.get("fundtype") or "").strip(),
            share_class=cls._share_class(name),
            source_url=cls.product_url_template.format(code=code),
            source_type=source_type,
        )

    @classmethod
    def parse_catalogue_payload(cls, payload: Dict[str, Any]) -> List[FundIdentity]:
        """解析 ``fundList`` 的各分类数组并按基金代码去重。"""
        if str(payload.get("code") or "") != cls.success_code:
            raise ValueError("南方基金目录接口返回失败")
        data = payload.get("data") or {}
        found: Dict[str, FundIdentity] = {}
        if not isinstance(data, dict):
            return []
        for key in cls._catalogue_keys:
            rows = data.get(key) or []
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                identity = cls._identity_from_row(row, cls.source_type_catalogue)
                if identity is None:
                    continue
                # 分类数组可能重复；优先保留名称/类型更完整的一行。
                old = found.get(identity.code)
                if old is None or (not old.fund_type and identity.fund_type) or len(identity.name) > len(old.name):
                    found[identity.code] = identity
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def parse_status_payload(cls, payload: Dict[str, Any]) -> List[FundIdentity]:
        """解析产品状态 API 的当前基金列表。"""
        if str(payload.get("code") or "") != cls.success_code:
            raise ValueError("南方基金交易状态接口返回失败")
        data = payload.get("data") or {}
        rows = data.get("fundlist") if isinstance(data, dict) else []
        rows = rows or []
        if isinstance(rows, dict):
            rows = [rows]
        found: Dict[str, FundIdentity] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity = cls._identity_from_row(row, cls.source_type_status)
            if identity is not None:
                found[identity.code] = identity
        return sorted(found.values(), key=lambda item: item.code)

    def _load_catalogue(self) -> Dict[str, Any]:
        if self._catalogue_payload is None:
            self._catalogue_payload = self._post(
                self.catalogue_api_url,
                {},
                self.catalogue_url,
            )
        return self._catalogue_payload

    def _load_status(self) -> Dict[str, Any]:
        if self._status_payload is None:
            self._status_payload = self._post(
                self.status_api_url,
                {},
                self.status_page_url,
            )
        return self._status_payload

    def discover_funds(self) -> List[FundIdentity]:
        """合并官网基金目录和当前交易状态表，避免任一列表遗漏份额。"""
        catalogue = self.parse_catalogue_payload(self._load_catalogue())
        status = self.parse_status_payload(self._load_status())
        found = {fund.code: fund for fund in catalogue}
        for fund in status:
            found.setdefault(fund.code, fund)
        return sorted(found.values(), key=lambda item: item.code)

    @staticmethod
    def _status_flag(value: Any) -> Optional[bool]:
        text = str(value if value is not None else "").strip().lower()
        if text in {"1", "true", "y", "yes"}:
            return True
        if text in {"0", "false", "n", "no"}:
            return False
        return None

    @classmethod
    def _status_text(cls, row: Dict[str, Any]) -> str:
        transaction_state = str(row.get("transactionState") or "").strip()
        if transaction_state == "10":
            return "认购期"
        pieces: List[str] = []
        for key, open_text, closed_text in (
            ("sgStatus", "开放申购", "暂停申购"),
            ("shStatus", "开放赎回", "暂停赎回"),
            ("dtStatus", "开放定投", "暂停定投"),
            ("zhrStatus", "开放转换转入", "暂停转换转入"),
            ("zhcStatus", "开放转换转出", "暂停转换转出"),
        ):
            flag = cls._status_flag(row.get(key))
            if flag is not None:
                pieces.append(open_text if flag else closed_text)
        return " ".join(pieces) or "未知"

    @staticmethod
    def _limit_from_remark(remark: Any) -> Optional[str]:
        text = str(remark or "").replace("，", ",").strip()
        if not text:
            return None
        patterns = (
            r"限额(?:调整为|调整至|设置为|设为|为|至)?\s*([\d,.]+)\s*(亿元|亿|万元|万|元)",
            r"上限(?:调整为|调整至|设置为|设为|为|至)?\s*([\d,.]+)\s*(亿元|亿|万元|万|元)",
            r"不(?:超过|高于)\s*([\d,.]+)\s*(亿元|亿|万元|万|元)",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return _amount("".join(match.groups()))
        return None

    @classmethod
    def _status_rows(cls, payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        data = payload.get("data") or {}
        rows = data.get("fundlist") if isinstance(data, dict) else []
        if isinstance(rows, dict):
            rows = [rows]
        return (row for row in (rows or []) if isinstance(row, dict))

    def _status_row(self, code: str) -> Optional[Dict[str, Any]]:
        for row in self._status_rows(self._load_status()):
            if str(row.get("fundCode") or row.get("fundcode") or "").strip() == str(code):
                return dict(row)
        return None

    @classmethod
    def parse_overreview_payload(
        cls,
        payload: Dict[str, Any],
        code: str,
        source_url: str,
        observed_at: str,
        status_row: Optional[Dict[str, Any]] = None,
    ) -> ProductSnapshot:
        """把产品详情 API 映射为标准产品快照，同时保留原始字段。"""
        if str(payload.get("code") or "") != cls.success_code:
            raise ValueError("南方基金产品详情接口返回失败")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("南方基金产品详情接口缺少 data")
        info = data.get("fund_info") or {}
        returns = data.get("fundReturn") or {}
        risk = data.get("fundRiskRating") or {}
        quarter = data.get("quarter") or {}
        if not isinstance(info, dict):
            info = {}
        if not isinstance(returns, dict):
            returns = returns[0] if isinstance(returns, list) and returns and isinstance(returns[0], dict) else {}
        if not isinstance(risk, dict):
            risk = {}
        if not isinstance(quarter, dict):
            quarter = {}
        fields: Dict[str, str] = {}
        for key, value in info.items():
            fields[str(key)] = cls._stringify(value)
        for prefix, mapping in (("fundReturn.", returns), ("fundRiskRating.", risk), ("quarter.", quarter)):
            for key, value in mapping.items():
                fields[f"{prefix}{key}"] = cls._stringify(value)
        if status_row:
            for key, value in status_row.items():
                fields[f"status.{key}"] = cls._stringify(value)
        name = str(info.get("fundName") or info.get("fundNameEx") or code)
        trade_status = cls._status_text(status_row or info)
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=str(info.get("fundNameEx") or ""),
            fund_type=str(info.get("basedetailType") or info.get("fundType") or ""),
            risk_level=str(risk.get("RISKRATING") or info.get("riskLevel") or ""),
            inception_date=cls._normalize_date(info.get("fundDate") or info.get("contractValidDate")),
            asset_scale=cls._stringify(quarter.get("fundsize") or info.get("asset")),
            net_value_date=cls._normalize_date(returns.get("FDATE") or info.get("navDate")),
            trade_status=trade_status,
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        payload = self._post(self.product_api_url, {"fundCode": fund.code}, url)
        # 产品详情本身是独立公开接口；状态表暂时不可用时仍保留产品快照，
        # 不把一个接口的认证/网络边界误报成产品页不可访问。
        try:
            status_row = self._status_row(fund.code)
        except (PermissionError, requests.RequestException, RuntimeError, ValueError, TypeError):
            status_row = None
        return self.parse_overreview_payload(
            payload,
            fund.code,
            url,
            self._observed_at(),
            status_row,
        )

    @classmethod
    def _channel_from_status_row(cls, row: Dict[str, Any]) -> ChannelTradeStatus:
        remark = str(row.get("remark") or "").strip()
        limit = cls._limit_from_remark(remark) or ""
        return ChannelTradeStatus(
            customer_type="individual",
            channel="南方官网公开交易状态 API",
            subscription=cls._status_flag(row.get("sgStatus")),
            redemption=cls._status_flag(row.get("shStatus")),
            transfer_in=cls._status_flag(row.get("zhrStatus")),
            transfer_out=cls._status_flag(row.get("zhcStatus")),
            sip=cls._status_flag(row.get("dtStatus")),
            limit=limit,
            quota_type="大额申购（含定投和转换转入）" if limit else "",
            quota_remark=remark,
            raw=row,
        )

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        payload = self._load_status()
        row = self._status_row(fund.code)
        product = self.fetch_product(fund)
        if row is None:
            return TradeSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                channels=[],
                api_status=0,
                message="南方官网交易状态 API 未返回该基金",
                source_url=self.status_api_url,
                observed_at=product.observed_at,
                raw={"currentDate": (payload.get("data") or {}).get("currentDate", ""), "product": product.fields},
            )
        channel = self._channel_from_status_row(row)
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel],
            api_status=0,
            message=self._status_text(row),
            source_url=self.status_api_url,
            observed_at=product.observed_at,
            raw={
                "currentDate": (payload.get("data") or {}).get("currentDate", ""),
                "status": row,
                "product": product.fields,
            },
        )

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        try:
            payload = self._load_status()
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="南方官网公开交易状态 API",
                limit=None,
                status="official_interface_requires_auth",
                quota_remark=str(exc),
                source_url=self.status_api_url,
                observed_at=self._observed_at(),
                raw={"error": str(exc)},
            )
        row = self._status_row(fund.code)
        observed_at = self._observed_at()
        current_date = str((payload.get("data") or {}).get("currentDate", ""))
        if row is None:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="南方官网公开交易状态 API",
                limit=None,
                status="official_api_no_data",
                source_url=self.status_api_url,
                observed_at=observed_at,
                raw={"currentDate": current_date},
            )
        channel = self._channel_from_status_row(row)
        status = "ok" if channel.limit or channel.subscription is False else "official_api_no_data"
        limit = channel.limit or ("暂停" if channel.subscription is False else None)
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type=channel.customer_type,
            channel=channel.channel,
            limit=limit,
            status=status,
            quota_type=channel.quota_type,
            quota_remark=channel.quota_remark,
            source_url=self.status_api_url,
            observed_at=observed_at,
            raw={"currentDate": current_date, "status": row},
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        if snapshot.status != "ok":
            return _record(
                "未获取",
                "南方基金官网公开交易状态 API",
                snapshot.source_url,
                snapshot.quota_remark or "官网接口返回认证边界，未绕过认证。",
                status=snapshot.status,
            )
        current_date = str((snapshot.raw.get("currentDate") if isinstance(snapshot.raw, dict) else "") or "")
        note = "官网公开交易状态 API；" + (f"接口当前日期={current_date}；" if current_date else "")
        note += snapshot.quota_remark or "官网未返回明确金额限额。"
        return _record(
            snapshot.limit or "未获取",
            "南方基金官网公开交易状态 API",
            snapshot.source_url,
            note,
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="南方基金 fundList、overreview 和 subscriptionAndRedemptionStatus 均可匿名读取；未观察到登录、验证码、设备签名或银行卡绑定要求。",
        )
