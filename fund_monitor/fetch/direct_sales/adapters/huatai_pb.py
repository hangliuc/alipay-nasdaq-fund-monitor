"""华泰柏瑞基金官网公开 ``FundArr`` 目录 Adapter。

华泰柏瑞官网首页脚本 ``common/index.html.js`` 内嵌了当前基金目录数组
``FundArr``。数组同时包含产品名称、净值、风险、交易状态和产品页展示的
申购上限 ``limitmoney``。本模块只读取该公开脚本，不登录、不提交交易，且不
把最低申购金额 ``buypoint`` 当作申购限额。
"""

from datetime import datetime, timezone
import json
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

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


class HuataiPBAdapter:
    """华泰柏瑞官网 FundArr 基金目录、产品快照和申购限额 Adapter。"""

    manager_id = "华泰柏瑞"
    home_url = "https://www.huatai-pb.com/"
    catalogue_url = "https://www.huatai-pb.com/common/index.html.js?v="
    source_type_catalogue = "huatai_pb_official_fundarr_catalogue"
    source_type_product = "huatai_pb_official_fundarr_product_snapshot"
    product_url_template = "https://www.huatai-pb.com/products/{code}/index.html"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._rows: Optional[Dict[str, Dict[str, Any]]] = None

    def _observed_at(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def _headers(cls) -> Dict[str, str]:
        return {**HEADERS, "Referer": cls.home_url}

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

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

    @classmethod
    def _product_url(cls, row: Dict[str, Any], code: str) -> str:
        raw_url = str(row.get("url") or "").strip()
        if raw_url:
            return urljoin(cls.home_url, raw_url)
        return cls.product_url_template.format(code=code)

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
    def parse_catalogue_script(cls, script: Any, source_url: str = "") -> List[FundIdentity]:
        """从脚本中的 ``var FundArr = [...]`` 解析当前全部基金份额。"""
        text = bytes(script or b"").decode("utf-8", errors="replace") if isinstance(script, bytes) else str(script or "")
        marker = re.search(r"\bvar\s+FundArr\s*=\s*\[", text)
        if not marker:
            raise ValueError("华泰柏瑞脚本缺少 FundArr")
        array_start = text.find("[", marker.start())
        try:
            rows, _ = json.JSONDecoder().raw_decode(text[array_start:])
        except (TypeError, ValueError) as exc:
            raise ValueError("华泰柏瑞 FundArr 不是有效 JSON 数组") from exc
        if not isinstance(rows, list):
            raise ValueError("华泰柏瑞 FundArr 不是数组")

        found: Dict[str, FundIdentity] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("fundcode") or "").strip()
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = str(row.get("fundname") or row.get("fundFullName") or code).strip()
            identity = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=str(row.get("fundtype") or row.get("property") or "").strip(),
                share_class=cls._share_class(name),
                source_url=cls._product_url(row, code),
                source_type=cls.source_type_catalogue,
            )
            old = found.get(code)
            if old is None or len(identity.name) > len(old.name) or (not old.fund_type and identity.fund_type):
                found[code] = identity
        return sorted(found.values(), key=lambda item: item.code)

    def _load_rows(self) -> Dict[str, Dict[str, Any]]:
        if self._rows is not None:
            return self._rows
        response = self.session.get(self.catalogue_url, headers=self._headers(), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("华泰柏瑞官网目录需要认证")
        response.raise_for_status()
        text = response.content.decode("utf-8", errors="replace")
        marker = re.search(r"\bvar\s+FundArr\s*=\s*\[", text)
        if not marker:
            raise ValueError("华泰柏瑞脚本缺少 FundArr")
        array_start = text.find("[", marker.start())
        try:
            rows, _ = json.JSONDecoder().raw_decode(text[array_start:])
        except (TypeError, ValueError) as exc:
            raise ValueError("华泰柏瑞 FundArr 不是有效 JSON 数组") from exc
        if not isinstance(rows, list):
            raise ValueError("华泰柏瑞 FundArr 不是数组")
        found: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("fundcode") or "")
            if not re.fullmatch(r"\d{6}", code):
                continue
            old = found.get(code)
            if old is None or sum(value not in (None, "", "--") for value in row.values()) > sum(
                value not in (None, "", "--") for value in old.values()
            ):
                found[code] = dict(row)
        self._rows = found
        return self._rows

    def discover_funds(self) -> List[FundIdentity]:
        rows = list(self._load_rows().values())
        # 通过同一解析器生成身份，确保 live 请求和 fixture 测试规则一致。
        script = "var FundArr = " + json.dumps(rows, ensure_ascii=False)
        return self.parse_catalogue_script(script, self.catalogue_url)

    @classmethod
    def _subscription_status(cls, text: str) -> Optional[bool]:
        if any(token in text for token in ("暂停申购", "暂停交易", "基金终止")):
            return False
        if any(token in text for token in ("正常开放", "认购期", "暂停赎回")):
            return True
        return None

    @classmethod
    def _redemption_status(cls, text: str) -> Optional[bool]:
        if "暂停交易" in text or "基金终止" in text or "暂停赎回" in text:
            return False
        if "正常开放" in text or "暂停申购" in text or "认购期" in text:
            return True
        return None

    @classmethod
    def parse_product_row(
        cls,
        row: Dict[str, Any],
        code: str,
        source_url: str,
        observed_at: str,
    ) -> ProductSnapshot:
        status_text = str(row.get("statusStr") or "").strip()
        fields = {str(key): cls._stringify(value) for key, value in row.items()}
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=str(row.get("fundname") or row.get("fundFullName") or code),
            full_name=str(row.get("fundFullName") or ""),
            fund_type=str(row.get("fundtype") or row.get("property") or ""),
            risk_level=str(row.get("levelofriskStr") or row.get("levelofrisk") or ""),
            inception_date=cls._normalize_date(row.get("setupdate")),
            asset_scale=str(row.get("lastasset") or ""),
            net_value_date=cls._normalize_date(row.get("valuedate")),
            trade_status=status_text or "未知",
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def _row_for_code(self, code: str) -> Optional[Dict[str, Any]]:
        row = self._load_rows().get(str(code))
        return dict(row) if row else None

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        row = self._row_for_code(fund.code)
        if row is None:
            raise ValueError(f"华泰柏瑞 FundArr 未返回基金 {fund.code}")
        return self.parse_product_row(
            row,
            fund.code,
            self._product_url(row, fund.code),
            self._observed_at(),
        )

    @classmethod
    def _limit_from_row(cls, row: Dict[str, Any]) -> Optional[str]:
        raw = str(row.get("limitmoney") or "").strip()
        if "暂停" in raw:
            return "暂停"
        return _amount(raw)

    @classmethod
    def _channel_from_row(cls, row: Dict[str, Any]) -> ChannelTradeStatus:
        status_text = str(row.get("statusStr") or "").strip()
        limit = cls._limit_from_row(row) or ""
        if not limit and cls._subscription_status(status_text) is False:
            limit = "暂停"
        return ChannelTradeStatus(
            customer_type="individual",
            channel="华泰柏瑞官网 FundArr 公开目录脚本",
            subscription=cls._subscription_status(status_text),
            redemption=cls._redemption_status(status_text),
            limit=limit,
            quota_type="产品页申购上限" if limit and limit != "暂停" else "",
            quota_remark=f"limitmoney={row.get('limitmoney') or '--'}；statusStr={status_text or '未提供'}",
            raw=row,
        )

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        row = product.fields
        channel = self._channel_from_row(row)
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel],
            api_status=0,
            message=product.trade_status,
            source_url=self.catalogue_url,
            observed_at=product.observed_at,
            raw=product.fields,
        )

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        try:
            product = self.fetch_product(fund)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="华泰柏瑞官网 FundArr 公开目录脚本",
                limit=None,
                status="official_interface_requires_auth",
                quota_remark=str(exc),
                source_url=self.catalogue_url,
                observed_at=self._observed_at(),
                raw={"error": str(exc)},
            )
        row = product.fields
        channel = self._channel_from_row(row)
        status = "ok" if channel.limit else "official_api_no_data"
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type=channel.customer_type,
            channel=channel.channel,
            limit=channel.limit or None,
            status=status,
            quota_type=channel.quota_type,
            quota_remark=channel.quota_remark,
            source_url=self.catalogue_url,
            observed_at=product.observed_at,
            raw=product.fields,
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        if snapshot.status != "ok":
            return _record(
                "未获取",
                "华泰柏瑞官网 FundArr 公开目录脚本",
                snapshot.source_url,
                snapshot.quota_remark or "官网接口返回认证边界，未绕过认证。",
                status=snapshot.status,
            )
        return _record(
            snapshot.limit or "未获取",
            "华泰柏瑞官网 FundArr 公开目录脚本",
            snapshot.source_url,
            "官网公开 FundArr.limitmoney；按产品页展示申购上限作为直销限额，未将 buypoint 最低申购金额混入限额。",
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="华泰柏瑞官网公开 FundArr 目录脚本可匿名访问；未观察到登录、验证码、设备签名或银行卡绑定要求。",
        )
