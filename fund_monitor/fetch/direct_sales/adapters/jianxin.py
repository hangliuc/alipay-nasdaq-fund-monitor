"""建信基金官网独立 Adapter。

建信官网的公开 Vue 应用提供三条匿名的一手数据链路：

* ``/website/v1/api/fundList``：当前基金/份额目录；
* ``/website/v1/api/fund/detail``：产品资料、净值、收益和费率；
* ``/website/v1/api/fund/notice``：按基金代码检索公告，公告详情页再提供
  官网 DOC/PDF 附件。

建信目前没有在上述匿名产品详情接口返回一个可直接复用的个人直销实时
上限字段，因此直销限额取最新的大额申购/定投公告附件。建信公告经常同时
给出“全渠道”限额和“建信基金直销渠道”例外；解析时优先使用直销渠道
例外，避免把代销渠道或通用限额写入直销结果。

本模块不登录、不提交交易、不绕过验证码/设备签名/银行卡绑定，也不访问
第三方平台。
"""

from datetime import datetime, timezone
import json
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


class JianxinAdapter:
    """建信官网基金目录、产品详情和直销公告 Adapter。"""

    manager_id = "建信"
    home_url = "https://www.ccbfund.cn/"
    catalogue_api_url = "https://www.ccbfund.cn/website/v1/api/fundList"
    product_api_url = "https://www.ccbfund.cn/website/v1/api/fund/detail"
    chart_api_url = "https://www.ccbfund.cn/website/v1/api/fund/chart"
    notice_api_url = "https://www.ccbfund.cn/website/v1/api/fund/notice"
    notice_category_api_url = "https://www.ccbfund.cn/website/v1/api/fund/noticeCategory"
    detail_url_template = "https://www.ccbfund.cn/#/fund?fundCode={code}"
    notice_detail_url_template = "https://www.ccbfund.cn/resource/static/content/{cnt_id}.html"

    source_type_catalogue = "jianxin_official_fund_catalogue_api"
    source_type_product = "jianxin_official_product_detail_api"
    source_type_notice = "jianxin_official_notice_doc"
    notice_page_limit = 8

    fund_type_names = {
        "1": "货币基金",
        "2": "债券基金",
        "3": "混合基金",
        "4": "海外基金",
        "5": "股票基金",
        "8": "指数基金",
    }

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
        text = str(name or "").strip()
        match = re.search(r"([A-Z])(?:类)?(?:人民币|美元(?:现汇|现钞)?)?$", text)
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
            source_url=cls.detail_url_template.format(code=code),
            source_type="config_compatibility",
        )

    @classmethod
    def _catalogue_rows_from_payload(cls, payload: Any) -> Dict[str, Dict[str, Any]]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            data = data.get("content") or data.get("list") or []
        rows: Dict[str, Dict[str, Any]] = {}
        for category in data if isinstance(data, list) else []:
            if not isinstance(category, dict):
                continue
            category_name = cls._clean(category.get("name"))
            category_id = category.get("id")
            items = category.get("list") or category.get("content") or []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                code = cls._clean(item.get("fundCode") or item.get("fund_code"))
                if not re.fullmatch(r"\d{6}", code):
                    continue
                row = dict(item)
                row["categoryName"] = category_name
                row["categoryId"] = category_id
                rows[code] = row
        return rows

    @classmethod
    def parse_catalogue_payload(cls, payload: Any, source_url: str = "") -> List[FundIdentity]:
        """解析建信 ``fundList`` 接口返回的全部当前基金/份额。"""
        rows = cls._catalogue_rows_from_payload(payload)
        found: List[FundIdentity] = []
        for code, row in rows.items():
            name = cls._clean(row.get("fundName") or code)
            type_code = cls._clean(row.get("fundTypeCode") or row.get("fundTypeId"))
            found.append(
                FundIdentity(
                    manager_id=cls.manager_id,
                    code=code,
                    name=name,
                    fund_type=cls.fund_type_names.get(type_code, cls._clean(row.get("fundTypeName") or type_code)),
                    share_class=cls._share_class(name),
                    source_url=cls.detail_url_template.format(code=code),
                    source_type=cls.source_type_catalogue,
                )
            )
        return sorted(found, key=lambda item: item.code)

    @classmethod
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        """兼容解析产品页/静态 HTML 中的基金代码和名称。

        建信全量目录的主路径是 JSON API；该方法用于离线快照或页面被 SSR
        的场景，不依赖一个脆弱的 CSS 类名。
        """
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        found: Dict[str, FundIdentity] = {}
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            code_match = re.search(r"(?:fundCode=|/fund/)(\d{6})", href, re.I)
            if not code_match:
                continue
            code = code_match.group(1)
            name = cls._clean(anchor.get_text(" ", strip=True)) or code
            if len(name) > 1:
                found[code] = FundIdentity(
                    manager_id=cls.manager_id,
                    code=code,
                    name=name,
                    share_class=cls._share_class(name),
                    source_url=cls.detail_url_template.format(code=code),
                    source_type=cls.source_type_product,
                )
        return sorted(found.values(), key=lambda item: item.code)

    def _get_json(self, url: str, params: Optional[Dict[str, Any]] = None, referer: str = "") -> Dict[str, Any]:
        response = self.session.get(url, params=params or {}, headers=self._headers(referer), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("建信基金官方接口需要认证")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("建信基金官方接口返回不是 JSON 对象")
        if str(payload.get("errcode")) not in {"0", "0.0", "None", ""}:
            raise RuntimeError(str(payload.get("msg") or "建信基金官方接口调用失败"))
        return payload

    def discover_funds(self) -> List[FundIdentity]:
        """通过建信官网公开目录接口发现全部当前基金/份额。"""
        payload = self._get_json(self.catalogue_api_url, referer=self.home_url)
        self._catalogue_rows = self._catalogue_rows_from_payload(payload)
        funds = self.parse_catalogue_payload(payload, self.catalogue_api_url)
        if not funds:
            raise RuntimeError("建信基金目录接口未返回基金份额")
        return funds

    @staticmethod
    def _flatten_fields(prefix: str, value: Any, result: Dict[str, str]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                JianxinAdapter._flatten_fields(f"{prefix}_{key}" if prefix else str(key), item, result)
        elif isinstance(value, list):
            result[prefix] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            result[prefix] = JianxinAdapter._clean(value)

    @classmethod
    def parse_product_payload(cls, payload: Any, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        """解析 ``fund/detail`` 的产品、收益、净值和费率快照。"""
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            data = {}
        detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
        fund = detail.get("fund") if isinstance(detail.get("fund"), dict) else {}
        profit = detail.get("profit") if isinstance(detail.get("profit"), dict) else {}
        fields: Dict[str, str] = {}
        cls._flatten_fields("fund", fund, fields)
        cls._flatten_fields("profit", profit, fields)
        cls._flatten_fields("fareStructure", data.get("fareStructure") or [], fields)
        cls._flatten_fields("fundManager", data.get("fundManager") or [], fields)
        cls._flatten_fields("category", data.get("category") or [], fields)

        name = cls._clean(fund.get("fundShortName") or fund.get("fundName") or code)
        full_name = cls._clean(fund.get("fundName"))
        nav_date = cls._normalize_date(detail.get("netValueDateStr1") or detail.get("netValueDateStr"))
        fields.update(
            {
                "基金代码": cls._clean(fund.get("fundCode") or code),
                "基金简称": name,
                "基金全称": full_name,
                "基金类型": cls._clean(fund.get("fundType")),
                "基金类型代码": cls._clean(fund.get("fundTypeCode")),
                "风险等级": cls._clean(fund.get("riskLevel")),
                "成立日期": cls._normalize_date(fund.get("issueDateStr")),
                "运作方式": cls._clean(fund.get("optMode")),
                "基金管理人": cls._clean(fund.get("managerName")),
                "基金托管人": cls._clean(fund.get("trusteeName")),
                "最新净值": cls._clean(detail.get("netValue")),
                "累计净值": cls._clean(detail.get("totalNetValue")),
                "净值日期": nav_date,
                "收益数据来源": cls._clean(profit.get("dataSource")),
                "近一年收益率": cls._clean(profit.get("lastYear")),
                "官网赎回展示": cls._clean(fund.get("showRedemption")),
            }
        )
        manager_names = [cls._clean(item.get("name")) for item in data.get("fundManager", []) if isinstance(item, dict)]
        if manager_names:
            fields["基金经理"] = "、".join(name for name in manager_names if name)
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=cls._clean(fund.get("fundType")),
            risk_level=cls._clean(fund.get("riskLevel")),
            inception_date=cls._normalize_date(fund.get("issueDateStr")),
            asset_scale="",
            net_value_date=nav_date,
            trade_status="官网产品详情未公开当前申购/赎回交易状态",
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        """兼容解析建信产品页 HTML；产品主路径为 ``fund/detail`` JSON。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        title = cls._clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        text = cls._clean(soup.get_text(" ", strip=True))
        name = title or code
        fields = {"基金代码": str(code), "页面标题": title}
        if text:
            fields["页面文本摘要"] = text[:2000]
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            source_url=source_url,
            observed_at=observed_at,
            trade_status="官网产品页未公开可解析的当前交易状态",
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        source_url = self.detail_url_template.format(code=fund.code)
        payload = self._get_json(self.product_api_url, {"fundCode": fund.code}, source_url)
        return self.parse_product_payload(payload, fund.code, self.product_api_url + f"?fundCode={fund.code}", self._observed_at())

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="建信基金官网产品详情 API",
            subscription=None,
            redemption=None,
            sip=None,
            quota_remark="建信公开产品详情返回资料、净值和费率，但未返回当前申购/赎回/定投开关；不访问需要认证的交易提交页。",
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
    def _parse_amount(cls, number: str, unit: str) -> Optional[str]:
        value = cls._clean(number).replace(",", "")
        if not value or value in {"-", "—"}:
            return None
        if unit == "美元":
            try:
                numeric = float(value)
            except ValueError:
                return None
            return f"{numeric:g}美元"
        return _amount(f"{value}{unit}")

    @classmethod
    def _direct_amounts(cls, text: str) -> List[str]:
        """提取公告正文明确写在“直销渠道”语境下的金额。"""
        results: List[str] = []
        patterns = (
            r"直销(?:渠道)?(?P<body>.{0,600}?)(?:高于|超过|限额(?:为|是)?|上限(?:为|是)?)[^0-9]{0,80}(?P<num>[\d,.]+)\s*(?P<unit>亿元|亿|万元|万|元|美元)",
            r"直销(?:渠道)?(?P<body>.{0,600}?)(?P<num>[\d,.]+)\s*(?P<unit>亿元|亿|万元|万|元|美元)\s*(?:的)?(?:申购|定投)?(?:限额|上限)",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.S):
                amount = cls._parse_amount(match.group("num"), match.group("unit"))
                if amount and amount not in results:
                    results.append(amount)
        return results

    @classmethod
    def parse_announcement_text(
        cls,
        text: str,
        code: str,
        title: str = "",
        announcement_date: str = "",
        source_url: str = "",
    ) -> Optional[Dict[str, Any]]:
        """解析建信公告附件；直销例外优先于通用表格。"""
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        compact_title = cls._clean(title).replace(" ", "")
        direct_amounts = cls._direct_amounts(normalized)
        if direct_amounts:
            return {
                "limit": direct_amounts[0],
                "status": "ok",
                "quota_type": "建信基金直销渠道公告限额",
                "quota_remark": "公告正文明确写出建信基金直销渠道限额；优先于同公告的全渠道/代销限额。",
                "announcement_title": cls._clean(title),
                "announcement_date": cls._normalize_date(announcement_date),
                "source_url": source_url,
            }

        code_match = re.search(r"(?:下属分级基金的交易代码|交易代码|基金代码)\s*((?:\d{6}\s*)+)", normalized)
        if code_match:
            codes = re.findall(r"\d{6}", code_match.group(1))
            block_match = re.search(
                r"限制申购金额[^：:]{0,40}(.*?)(?=限制定期定额投资金额|其他需要提示的事项|$)",
                normalized,
            )
            block = block_match.group(1) if block_match else ""
            values = re.findall(r"([\d,.]+)\s*(亿元|亿|万元|万|元|美元)|([-—])", block)
            parsed_values: List[Optional[str]] = []
            for number, unit, dash in values:
                parsed_values.append(None if dash else cls._parse_amount(number, unit))
            if code in codes:
                index = codes.index(code)
                if index < len(parsed_values) and parsed_values[index]:
                    return {
                        "limit": parsed_values[index],
                        "status": "ok",
                        "quota_type": "建信基金公告限制申购金额",
                        "quota_remark": "公告表格按下属分级基金交易代码对应解析限制申购金额；未发现单独直销例外。",
                        "announcement_title": cls._clean(title),
                        "announcement_date": cls._normalize_date(announcement_date),
                        "source_url": source_url,
                    }

        generic = re.search(r"(?:申购|定投)(?:业务)?[^。；]{0,80}?(?:高于|超过|限额为|上限为)\s*([\d,.]+)\s*(亿元|亿|万元|万|元|美元)", normalized)
        if generic:
            limit = cls._parse_amount(generic.group(1), generic.group(2))
            if limit:
                return {
                    "limit": limit,
                    "status": "ok",
                    "quota_type": "建信基金公告业务限额",
                    "quota_remark": "公告正文明确业务限额，但未分离直销渠道例外。",
                    "announcement_title": cls._clean(title),
                    "announcement_date": cls._normalize_date(announcement_date),
                    "source_url": source_url,
                }

        if "恢复大额申购" in compact_title or "恢复大额申购" in normalized:
            return {
                "limit": "不限",
                "status": "ok",
                "quota_type": "建信基金公告恢复状态",
                "quota_remark": "公告明确恢复大额申购/定投且未列出金额上限。",
                "announcement_title": cls._clean(title),
                "announcement_date": cls._normalize_date(announcement_date),
                "source_url": source_url,
            }
        if "暂停大额申购" in compact_title or "暂停大额申购" in normalized:
            return {
                "limit": "暂停",
                "status": "ok",
                "quota_type": "建信基金公告暂停状态",
                "quota_remark": "公告明确暂停大额申购/定投，但正文没有可解析的金额。",
                "announcement_title": cls._clean(title),
                "announcement_date": cls._normalize_date(announcement_date),
                "source_url": source_url,
            }
        return None

    @classmethod
    def parse_notice_payload(cls, payload: Any) -> List[Dict[str, Any]]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            data = data.get("content") or data.get("list") or data.get("rows") or []
        if not isinstance(data, list):
            return []
        return [dict(item) for item in data if isinstance(item, dict) and (item.get("cntId") or item.get("id"))]

    @classmethod
    def parse_notice_detail_html(cls, html: bytes, source_url: str = "") -> Tuple[str, str, Optional[str]]:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        title = cls._clean((soup.select_one(".right-top .title") or soup.title).get_text(" ", strip=True) if (soup.select_one(".right-top .title") or soup.title) else "")
        date_node = soup.select_one(".right-top .time")
        date_text = cls._normalize_date(date_node.get_text(" ", strip=True) if date_node else "")
        attachment = next(
            (
                node
                for node in soup.find_all("a", href=True)
                if re.search(r"\.(?:docx?|pdf)(?:$|\?)", str(node.get("href") or ""), re.I)
            ),
            None,
        )
        attachment_url = urljoin(source_url, str(attachment.get("href"))) if attachment else None
        return title, date_text, attachment_url

    def _notice_candidates(self, fund: FundIdentity) -> Iterable[Dict[str, Any]]:
        seen: set[str] = set()
        for keyword in ("暂停", "大额申购", "恢复"):
            for page in range(1, self.notice_page_limit + 1):
                payload = self._get_json(
                    self.notice_api_url,
                    {
                        "fundCode": fund.code,
                        "categoryId": "",
                        "keyword": keyword,
                        "start": "",
                        "end": "",
                        "page": page,
                    },
                    self.detail_url_template.format(code=fund.code),
                )
                notices = self.parse_notice_payload(payload)
                if not notices:
                    break
                for notice in notices:
                    title = self._clean(notice.get("title"))
                    if not any(word in title for word in ("大额申购", "大额定投", "定期定额投资")):
                        continue
                    notice_id = str(notice.get("cntId") or notice.get("id"))
                    if notice_id in seen:
                        continue
                    seen.add(notice_id)
                    yield notice

    def _fetch_notice(self, fund: FundIdentity) -> Optional[Dict[str, Any]]:
        for notice in self._notice_candidates(fund):
            notice_id = str(notice.get("cntId") or notice.get("id"))
            detail_url = self.notice_detail_url_template.format(cnt_id=notice_id)
            detail_response = self.session.get(detail_url, headers=self._headers(self.home_url), timeout=self.timeout)
            if detail_response.status_code in {401, 403}:
                raise PermissionError("建信基金公告详情需要认证")
            detail_response.raise_for_status()
            title, date_text, attachment_url = self.parse_notice_detail_html(detail_response.content, detail_url)
            source_url = attachment_url or detail_url
            try:
                if attachment_url:
                    attachment_response = self.session.get(attachment_url, headers=self._headers(detail_url), timeout=max(self.timeout, 30))
                    if attachment_response.status_code in {401, 403}:
                        raise PermissionError("建信基金公告附件需要认证")
                    attachment_response.raise_for_status()
                    text = _extract_office_text(attachment_response.content, attachment_url)
                else:
                    text = BeautifulSoup(detail_response.content, "html.parser").get_text(" ", strip=True)
            except PermissionError:
                raise
            except (requests.RequestException, OSError, ValueError, UnicodeError):
                continue
            parsed = self.parse_announcement_text(text, fund.code, title or notice.get("title", ""), date_text or notice.get("date", ""), source_url)
            if parsed:
                parsed["announcement_text"] = text
                parsed["notice_id"] = notice_id
                return parsed
        return None

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        observed = self._observed_at()
        try:
            notice = self._fetch_notice(fund)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="建信基金官网公告 API/DOC",
                limit=None,
                status="official_interface_requires_auth",
                quota_remark=str(exc),
                source_url=self.notice_api_url + f"?fundCode={fund.code}",
                observed_at=observed,
                raw={"error": str(exc)},
            )
        except (requests.RequestException, RuntimeError, ValueError, TypeError, KeyError):
            notice = None
        if notice:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="建信基金官网最新公告附件（直销渠道）",
                limit=notice["limit"],
                status=notice["status"],
                quota_type=notice["quota_type"],
                quota_remark=notice["quota_remark"],
                source_url=notice["source_url"],
                observed_at=observed,
                raw=notice,
            )
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="建信基金官网公告 API/DOC",
            limit=None,
            status="official_api_no_data",
            quota_remark="建信官网公告 API 未定位到可解析的当前大额申购/定投限额；不沿用历史快照、不使用第三方数据。",
            source_url=self.notice_api_url + f"?fundCode={fund.code}",
            observed_at=observed,
            raw={},
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
            status="public",
            reason="建信基金目录、产品详情、净值/收益和公告 API 及 DOC/PDF 附件均可匿名读取；交易提交页面可能要求登录、验证码、设备签名或银行卡绑定，Adapter 不访问或绕过该认证边界。",
        )

