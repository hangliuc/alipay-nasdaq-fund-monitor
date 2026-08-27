"""浦银安盛基金官网独立 Adapter。

浦银安盛新版官网的前端通过 ``middle-platform-gateway`` 提供匿名公开的
基金目录、产品详情和公告列表 API。公告附件是同一官方域名下的 PDF；本模块
只读取这些公开资源，不登录、不提交交易，也不使用第三方平台数据。

直销限额只接受当前官方公告中明确的“限制申购金额”或暂停/恢复状态。产品
页的起购金额不是大额申购限额，缺少当前公告时返回 ``official_api_no_data``，
不会猜测为“不限”，也不会回退历史静态值。
"""

from datetime import datetime, timezone
from io import BytesIO
import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from pypdf import PdfReader

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


class PuyanAxaAdapter:
    """浦银安盛官网目录、产品 API 和业务公告 Adapter。"""

    manager_id = "浦银安盛"
    home_url = "https://www.py-axa.com/"
    gateway_url = "https://www.py-axa.com/middle-platform-gateway"
    catalogue_url = gateway_url + "/wechatwork-product-biz/front/api/sales/fundList"
    product_api_url = gateway_url + "/wechatwork-product-biz/front/api/fund/info"
    nav_api_url = gateway_url + "/wechatwork-product-biz/front/api/getFundValuesByFundCode"
    notice_api_url = gateway_url + "/wechatwork-content-biz/front/api/notice/list"
    file_url_template = gateway_url + "/system-file-biz/resources/file/{path}"
    trade_entry_url = "https://www.py-axa.com/etrading/account/login/init"

    source_type_catalogue = "puyan_axa_official_sales_fund_list"
    source_type_product = "puyan_axa_official_product_api"
    source_type_notice = "puyan_axa_official_notice_api"
    source_type_notice_pdf = "puyan_axa_official_notice_pdf"

    relevant_notice_terms = (
        "大额申购",
        "大额定投",
        "定期定额投资",
        "限制申购",
        "暂停申购",
        "恢复申购",
        "申购及定投",
        "申购及定期定额",
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
        self._catalogue_rows: Optional[Dict[str, Dict[str, Any]]] = None

    def _observed_at(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def _headers(cls, referer: str = "", json_request: bool = False) -> Dict[str, str]:
        headers = {**HEADERS, "Referer": referer or cls.home_url, "Origin": "https://www.py-axa.com"}
        if json_request:
            headers["Content-Type"] = "application/json;charset=UTF-8"
        return headers

    @staticmethod
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").replace("\xa0", " ").split())

    @staticmethod
    def _official_url(value: Any, base: str = "https://www.py-axa.com/") -> str:
        url = str(value or "").strip()
        if not url:
            return ""
        # API 目前仍返回 http 链接；统一为官网 HTTPS，避免生成不必要的明文请求。
        if url.startswith("http://www.py-axa.com"):
            url = "https://www.py-axa.com" + url[len("http://www.py-axa.com") :]
        return urljoin(base, url)

    @staticmethod
    def _share_class(name: str) -> str:
        match = re.search(r"([A-Z])(?:类)?(?:人民币|美元(?:现汇|现钞)?)?$", str(name or "").strip())
        return match.group(1) if match else ""

    @staticmethod
    def _normalize_date(value: Any) -> str:
        text = str(value or "").strip()
        match = re.search(r"(\d{4})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})", text)
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
            source_url=f"{cls.product_api_url}?fundCode={code}",
            source_type="config_compatibility",
        )

    @classmethod
    def parse_catalogue_payload(cls, payload: Dict[str, Any]) -> List[FundIdentity]:
        """解析官网 ``sales/fundList`` 的全部当前基金份额。"""
        rows = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        found: Dict[str, FundIdentity] = {}
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            code = cls._clean(raw.get("fundCode") or raw.get("marketCode"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = cls._clean(raw.get("fundName") or raw.get("fullName") or code)
            found.setdefault(
                code,
                FundIdentity(
                    manager_id=cls.manager_id,
                    code=code,
                    name=name,
                    fund_type=cls._clean(raw.get("investType_dictText") or raw.get("fundType")),
                    share_class=cls._share_class(name),
                    source_url=f"{cls.product_api_url}?fundCode={code}",
                    source_type=cls.source_type_catalogue,
                ),
            )
        return sorted(found.values(), key=lambda item: item.code)

    def _get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self.session.get(url, params=params, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403, 412}:
            raise PermissionError(f"浦银安盛公开接口返回 HTTP {response.status_code}，需要官网访问授权")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("浦银安盛 API 返回不是 JSON 对象")
        return payload

    def _post_json(self, url: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self.session.post(
            url,
            params=params,
            json=body or {},
            headers=self._headers(self.home_url, json_request=True),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403, 412}:
            raise PermissionError(f"浦银安盛公开接口返回 HTTP {response.status_code}，需要官网访问授权")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("浦银安盛 API 返回不是 JSON 对象")
        return payload

    @staticmethod
    def _api_result(payload: Dict[str, Any], description: str) -> Any:
        if payload.get("success") is False:
            message = str(payload.get("message") or f"浦银安盛{description}失败")
            if "登录" in message or "认证" in message or "权限" in message:
                raise PermissionError(message)
            raise RuntimeError(message)
        result = payload.get("result")
        if result is None:
            raise RuntimeError(f"浦银安盛{description}没有返回 result")
        return result

    def discover_funds(self) -> List[FundIdentity]:
        payload = self._get_json(self.catalogue_url)
        result = self._api_result(payload, "基金目录")
        self._catalogue_rows = {
            str(row.get("fundCode")): dict(row)
            for row in result
            if isinstance(row, dict) and re.fullmatch(r"\d{6}", str(row.get("fundCode") or ""))
        }
        funds = self.parse_catalogue_payload(payload)
        if not funds:
            raise RuntimeError("浦银安盛官网基金目录未返回当前基金份额")
        return funds

    @classmethod
    def _string_fields(cls, raw: Dict[str, Any]) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for key, value in raw.items():
            if value in (None, ""):
                continue
            if isinstance(value, (dict, list)):
                fields[str(key)] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            else:
                fields[str(key)] = str(value)
        return fields

    @classmethod
    def parse_product_payload(
        cls,
        payload: Dict[str, Any],
        fund: FundIdentity,
        source_url: str,
        observed_at: str,
    ) -> ProductSnapshot:
        result = payload.get("result") if isinstance(payload, dict) else None
        raw = dict(result) if isinstance(result, dict) else {}
        fields = cls._string_fields(raw)
        status = cls._clean(raw.get("fundState_dictText") or raw.get("openState_dictText"))
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=fund.code,
            name=cls._clean(raw.get("fundName") or fund.name),
            full_name=cls._clean(raw.get("fullName")),
            fund_type=cls._clean(raw.get("investType_dictText") or raw.get("fundType_dictText")),
            risk_level=cls._clean(raw.get("riskLevel_dictText") or raw.get("riskLevel")),
            inception_date=cls._normalize_date(raw.get("setupDate")),
            asset_scale=cls._clean(raw.get("fundSize")),
            net_value_date=cls._normalize_date((raw.get("pdFdValue") or {}).get("releaseDate") if isinstance(raw.get("pdFdValue"), dict) else ""),
            trade_status=status or "未知",
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = f"{self.product_api_url}?fundCode={fund.code}"
        payload = self._post_json(self.product_api_url, params={"fundCode": fund.code})
        self._api_result(payload, "基金详情")
        return self.parse_product_payload(payload, fund, url, self._observed_at())

    @classmethod
    def _status_bool(cls, text: str, action: str) -> Optional[bool]:
        value = cls._clean(text)
        if not value:
            return None
        if re.search(rf"暂停[^；，。]*{re.escape(action)}|{re.escape(action)}[^；，。]*暂停", value):
            return False
        if re.search(rf"开放[^；，。]*{re.escape(action)}|正常开放", value):
            return True
        return None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        status = product.trade_status
        subscription = self._status_bool(status, "申购")
        # 产品 API 没有明确赎回时不从“申购正常”推断赎回开放。
        redemption = self._status_bool(status, "赎回")
        sip = self._status_bool(status, "定投")
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="浦银安盛官网公开产品 API",
            subscription=subscription,
            redemption=redemption,
            sip=sip,
            limit="",
            quota_remark="交易状态来自官网 fund/info 产品详情；未登录、不提交交易。",
            raw=product.fields,
        )
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel],
            api_status=0,
            message=status or "官网未公开明确交易状态",
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw=product.fields,
        )

    @classmethod
    def _notice_date(cls, value: Any) -> str:
        return cls._normalize_date(str(value or "").replace(" ", "T")[:10])

    @classmethod
    def parse_notice_payload(cls, payload: Dict[str, Any], source_url: str = "") -> List[Dict[str, Any]]:
        result = payload.get("result") if isinstance(payload, dict) else None
        rows = result.get("records") if isinstance(result, dict) else result
        if not isinstance(rows, list):
            return []
        notices: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = cls._clean(row.get("title") or row.get("noticeTitle"))
            if not title or not any(term in title for term in cls.relevant_notice_terms):
                continue
            attachments = row.get("attachments") if isinstance(row.get("attachments"), list) else []
            attachment = next((item for item in attachments if isinstance(item, dict)), {})
            url = cls._official_url(attachment.get("previewUrl") or attachment.get("fileUrl"), source_url or cls.home_url)
            if not url and attachment.get("attachmentPath"):
                url = cls.file_url_template.format(path=str(attachment["attachmentPath"]).lstrip("/"))
            notices.append({
                "title": title,
                "date": cls._notice_date(row.get("publishTime")),
                "publish_time": cls._clean(row.get("publishTime")),
                "source_url": url,
                "notice_id": str(row.get("id") or ""),
                "attachment_name": cls._clean(attachment.get("attachmentName")),
                "raw": row,
            })
        return sorted(notices, key=lambda item: (item.get("date", ""), item.get("publish_time", "")), reverse=True)

    def _fetch_notices(self, code: str) -> List[Dict[str, Any]]:
        payload = self._post_json(
            self.notice_api_url,
            body={"fundCode": code, "pageNo": 1, "pageSize": 100},
        )
        return self.parse_notice_payload(payload, self.notice_api_url)

    def _fetch_notice_text(self, notice: Dict[str, Any]) -> str:
        url = str(notice.get("source_url") or "")
        if not url:
            return ""
        response = self.session.get(url, headers=self._headers(self.notice_api_url), timeout=self.timeout)
        if response.status_code in {401, 403, 412}:
            raise PermissionError(f"浦银安盛公告附件返回 HTTP {response.status_code}")
        response.raise_for_status()
        content = bytes(response.content or b"")
        if not content.startswith(b"%PDF"):
            return content.decode("utf-8", errors="ignore")
        return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)

    @classmethod
    def _code_sequence(cls, text: str) -> List[str]:
        flat = cls._clean(text)
        match = re.search(r"下属分级基金的交易代码\s+((?:\d{6}\s*)+)", flat)
        if match:
            return re.findall(r"\d{6}", match.group(1))
        return []

    @classmethod
    def _amount_sequence(cls, text: str, label: str) -> List[str]:
        flat = cls._clean(text)
        match = re.search(rf"{re.escape(label)}.*?(?=注|2\s*其他|$)", flat)
        if not match:
            return []
        # 公告表格的“单位：人民币元”通常只在表头出现，单元格本身是
        # ``3000.00``，不能要求每个数字后面重复出现“元”。
        values = re.findall(r"(?<!\d)[\d,]+(?:\.\d+)?", match.group(0))
        return [_amount(f"{value}元") or value for value in values]

    @classmethod
    def parse_announcement_text(
        cls,
        text: str,
        code: str,
        title: str = "",
        announcement_date: str = "",
        source_url: str = "",
    ) -> Dict[str, Any]:
        """按份额代码解析浦银安盛限额公告，保留当前有效公告原文。"""
        flat = cls._clean(text)
        if code not in re.findall(r"\d{6}", flat):
            return {"status": "official_api_no_data", "limit": None, "announcement_text": text, "source_url": source_url}
        lower = f"{title} {flat}"
        codes = cls._code_sequence(flat)
        index = codes.index(code) if code in codes else 0
        if re.search(r"暂停(?:申购|大额申购)", lower) and (
            "定期定额" in lower or "定投" in lower or "申购" in lower
        ):
            # 公告表格可能只暂停 A 份额或只暂停 C 份额；若表格明确列出
            # “是/否”，必须按交易代码定位，不能把同一主基金的其他份额带过来。
            status_match = re.search(
                r"该分级基金是否(?:暂停申购(?:及定期定额投资)?|暂停申购、定期定额投资)\s+(.+?)(?=\s+(?:下属|限制|注|2\s)|$)",
                flat,
            )
            if status_match and codes:
                status_values = re.findall(r"是|否", status_match.group(1))
                if index >= len(status_values) or status_values[index] != "是":
                    return {
                        "status": "official_api_no_data",
                        "limit": None,
                        "quota_remark": "公告按份额列出状态，但该份额未明确标记为暂停。",
                        "announcement_title": title,
                        "announcement_date": announcement_date,
                        "source_url": source_url,
                        "announcement_text": text,
                    }
            # 最新公告的表格明确逐份额列出“是”；代码出现在公告中后才接受该状态。
            channel_text = "直销机构" if "直销" in lower else "官网公告"
            return {
                "status": "ok",
                "limit": "暂停",
                "quota_type": "申购/定期定额投资状态",
                "quota_remark": f"浦银安盛官网最新公告明确{channel_text}暂停该份额申购及定期定额投资。",
                "announcement_title": title,
                "announcement_date": announcement_date,
                "source_url": source_url,
                "announcement_text": text,
            }
        if re.search(r"恢复(?:申购|大额申购)", lower):
            return {
                "status": "ok",
                "limit": "不限",
                "quota_type": "申购/定期定额投资状态",
                "quota_remark": "浦银安盛官网公告明确恢复该份额申购/定投，未列出新的金额上限。",
                "announcement_title": title,
                "announcement_date": announcement_date,
                "source_url": source_url,
                "announcement_text": text,
            }
        values = cls._amount_sequence(flat, "限制申购金额")
        if values:
            value = values[index] if index < len(values) else (values[0] if len(values) == 1 else None)
            if value:
                return {
                    "status": "ok",
                    "limit": value,
                    "quota_type": "限制申购金额",
                    "quota_remark": "浦银安盛官网公告按基金份额代码列出限制申购金额。",
                    "announcement_title": title,
                    "announcement_date": announcement_date,
                    "source_url": source_url,
                    "announcement_text": text,
                }
        return {
            "status": "official_api_no_data",
            "limit": None,
            "announcement_title": title,
            "announcement_date": announcement_date,
            "source_url": source_url,
            "announcement_text": text,
        }

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        notices = self._fetch_notices(fund.code)
        source_url = f"{self.notice_api_url}?fundCode={fund.code}"
        # 只解析最新一条相关公告。若最新公告无法解析，不使用更早公告的金额，
        # 避免把已被新公告覆盖的历史限额写入当前结果。
        if notices:
            notice = notices[0]
            text = ""
            try:
                if notice.get("source_url"):
                    text = self._fetch_notice_text(notice)
            except (PermissionError, requests.RequestException, OSError, ValueError):
                text = ""
            parsed = self.parse_announcement_text(
                text,
                fund.code,
                notice.get("title", ""),
                notice.get("date", ""),
                notice.get("source_url", ""),
            )
            if parsed.get("status") == "ok":
                raw = dict(parsed)
                return DirectLimitSnapshot(
                    manager_id=self.manager_id,
                    code=fund.code,
                    customer_type="individual",
                    channel="浦银安盛官网直销公告",
                    limit=parsed.get("limit"),
                    status="ok",
                    quota_type=str(parsed.get("quota_type") or ""),
                    quota_remark=str(parsed.get("quota_remark") or ""),
                    source_url=str(parsed.get("source_url") or source_url),
                    observed_at=self._observed_at(),
                    raw=raw,
                )
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="浦银安盛官网直销公告",
                limit=None,
                status="official_api_no_data",
                quota_type="",
                quota_remark="浦银安盛最新相关公告未解析出该份额当前可验证直销限额/状态；不回退历史公告。",
                source_url=str(notice.get("source_url") or source_url),
                observed_at=self._observed_at(),
                raw={"latest_notice": notice, "parsed": parsed},
            )
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="浦银安盛官网直销公告",
            limit=None,
            status="official_api_no_data",
            quota_type="",
            quota_remark="浦银安盛官网公告列表中未解析出该份额当前可验证直销限额/状态。",
            source_url=source_url,
            observed_at=self._observed_at(),
            raw={"notice_count": len(notices), "notices": notices},
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status != "ok":
            return None
        return _record(
            snapshot.limit or "未获取",
            "浦银安盛官网业务公告 PDF",
            snapshot.source_url,
            snapshot.quota_remark or "官网公告明确直销申购限额/状态；未使用第三方数据。",
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public_with_trade_auth_boundary",
            reason="浦银安盛官网匿名公开 API 可读取基金目录、产品详情和业务公告；实际交易入口需要登录，适配器不登录、不绕过验证码/设备签名/银行卡绑定。",
            requires_login=True,
            requires_captcha=False,
            requires_device_signature=False,
            requires_bank_card=False,
        )


# 与部分旧调用方的命名习惯保持一致。
PuyanAXAAdapter = PuyanAxaAdapter
