"""银华基金官网独立 Adapter。

银华基金的公募产品入口为 ``yhfund.com.cn/main/fund``，网上交易入口为
``trade.yhfund.com.cn/yhxntrade``。本模块只访问银华自有域名的基金产品页、
公告详情/PDF 和公开交易入口，不使用第三方公告聚合，不提交登录、验证码或
交易请求，也不绕过设备校验。

银华官网的网上交易登录页在未登录状态要求账号、密码和图片验证码。产品页或
公告页没有明确当前直销限额时，适配器返回 ``official_api_no_data``；交易页
进入认证边界时返回 ``official_interface_requires_auth``，不把起购金额、代销
状态或历史公告值猜成直销限额。
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


class YinhuAdapter:
    """银华基金公开产品、公告和交易认证边界 Adapter。"""

    manager_id = "银华"
    home_url = "https://www.yhfund.com.cn/"
    catalogue_url = "https://www.yhfund.com.cn/main/fund/index.shtml"
    product_url_template = "https://www.yhfund.com.cn/main/fund/funddetail/index.shtml?product_code={code}"
    notice_url = "https://www.yhfund.com.cn/main/home/guidelines/notice/index.shtml"
    trade_login_url = "https://trade.yhfund.com.cn/yhxntrade/account/goLogin.do"

    source_type_catalogue = "yinhu_official_fund_catalogue"
    source_type_product = "yinhu_official_product_page"
    source_type_notice = "yinhu_official_notice"
    source_type_notice_pdf = "yinhu_official_notice_pdf"
    source_type_trade = "yinhu_official_trade_login"

    relevant_notice_terms = (
        "大额申购",
        "大额定投",
        "定期定额投资",
        "限制申购",
        "暂停申购",
        "恢复申购",
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
        self._last_product_html: Optional[bytes] = None
        self._last_product_code = ""
        self._last_product_url = ""

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

    @staticmethod
    def _is_official_url(url: str) -> bool:
        host = (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
        return host == "yhfund.com.cn" or host.endswith(".yhfund.com.cn")

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
        value = str(href or "")
        for pattern in (
            r"(?:product_code|productCode|fundcode|fundCode|code)[=/?:&_-]?(\d{6})",
            r"/fund/(?:product|detail)?/?(\d{6})(?:[/?#]|$)",
        ):
            match = re.search(pattern, value, re.I)
            if match:
                return match.group(1)
        return ""

    @classmethod
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        """解析银华官方基金产品目录中的基金/份额链接并去重。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        rows: Dict[str, FundIdentity] = {}
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            code = cls._code_from_href(href)
            if not code:
                continue
            parent = anchor.find_parent(["tr", "li", "article", "div"]) or anchor.parent
            row_text = cls._clean(parent.get_text(" ", strip=True) if parent else anchor.get_text(" ", strip=True))
            name = cls._clean(anchor.get_text(" ", strip=True))
            if not name or name in {"详情", "查看详情", "购买", "申购"}:
                match = re.search(r"([^|｜;；]{2,100}?)(?:基金)?(?:代码|编号)\s*[：:]?\s*" + re.escape(code), row_text)
                name = cls._clean(match.group(1)) if match else ""
            if not name:
                name_match = re.search(r"银华[^|｜;；]{2,100}", row_text)
                name = cls._clean(name_match.group(0)) if name_match else code
            product_url = urljoin(source_url or cls.catalogue_url, href)
            identity = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                share_class=cls._share_class(name),
                source_url=product_url,
                source_type=cls.source_type_catalogue,
            )
            old = rows.get(code)
            if old is None or len(identity.name) > len(old.name):
                rows[code] = identity

        # 部分历史模板把代码写在文本中，仅在没有产品链接时使用该兜底，
        # 不将搜索结果或第三方页面当作目录来源。
        if not rows:
            text = cls._clean(soup.get_text(" ", strip=True))
            for match in re.finditer(r"([^|｜;；]{2,100}?)(?:基金)?(?:代码|编号)\s*[：:]?\s*(\d{6})", text):
                code = match.group(2)
                name = cls._clean(match.group(1))
                if name:
                    rows[code] = FundIdentity(
                        manager_id=cls.manager_id,
                        code=code,
                        name=name,
                        share_class=cls._share_class(name),
                        source_url=cls.product_url_template.format(code=code),
                        source_type=cls.source_type_catalogue,
                    )
        return sorted(rows.values(), key=lambda item: item.code)

    def discover_funds(self) -> List[FundIdentity]:
        response = self.session.get(
            self.catalogue_url,
            headers=self._headers(self.home_url),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("银华基金产品目录需要认证")
        # 银华官网当前可能返回 521 JavaScript 防护页；这是官网公开目录
        # 不可匿名读取的边界，不伪造一份目录，也不改用第三方目录。
        response.raise_for_status()
        funds = self.parse_catalogue_html(response.content, response.url or self.catalogue_url)
        if not funds:
            raise RuntimeError("银华基金目录页未找到公开基金产品链接或代码")
        return funds

    @classmethod
    def _label_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
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
    def _field(cls, fields: Dict[str, str], text: str, labels: Iterable[str]) -> str:
        for label in labels:
            if fields.get(label):
                return cls._clean(fields[label])
            match = re.search(rf"{re.escape(label)}\s*[：:]\s*([^|｜;；\n]+)", text)
            if match:
                return cls._clean(match.group(1))
        return ""

    @classmethod
    def _status(cls, value: str) -> Optional[bool]:
        value = str(value or "")
        if any(token in value for token in ("暂停", "关闭", "封闭", "不可")):
            return False
        if any(token in value for token in ("开放", "正常", "可申购", "可购买")):
            return True
        return None

    @classmethod
    def _direct_limit_from_text(cls, text: str) -> tuple[Optional[str], str, str]:
        normalized = cls._clean(text)
        direct = re.findall(r"(?:本公司)?(?:直销中心|直销渠道|网上直销|直销柜台)[^。；;]{0,300}", normalized)
        for segment in direct:
            if "起购金额" in segment and not re.search(r"上限|限额|限制|累计申购|大额|不超过", segment):
                continue
            amounts = re.findall(r"[\d,.]+\s*(?:亿元|亿|万元|万|元)", segment)
            if amounts:
                return _amount(amounts[-1]), "直销渠道申购上限", "官网正文明确银华直销渠道限额。"
        # 只接受明确的“限额/限制金额”字段，避免把最低起购额当作大额上限。
        candidates = re.findall(
            r"(?:限制申购金额|申购限额|大额申购(?:金额|上限)|累计申购上限)\s*[:：]?\s*([\d,.]+\s*(?:亿元|亿|万元|万|元))",
            normalized,
        )
        if candidates:
            return _amount(candidates[-1]), "申购限额", "官网产品页/公告明确申购限制金额；未单列渠道。"
        if re.search(r"恢复(?:大额申购|申购|定期定额投资)", normalized):
            return "不限", "大额申购状态", "银华官网公告明确恢复大额申购/定投，未列出金额上限。"
        if re.search(r"暂停(?:大额申购|申购|定期定额投资)", normalized):
            return "暂停", "大额申购状态", "银华官网公告明确暂停大额申购/定投。"
        return None, "", ""

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        """解析银华官方产品页字段、净值和公开交易状态。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._label_fields(soup)
        text = cls._clean(soup.get_text(" ", strip=True))
        title_nodes = soup.select("h1, .fund_title, .fund-title, .fundName, .fund_name, title")
        title = cls._clean(title_nodes[0].get_text(" ", strip=True) if title_nodes else "")
        title = re.sub(r"\s*[（(]\s*基金代码\s*[：:]?\s*\d{6}\s*[）)]", "", title).strip()
        name = title or cls._field(fields, text, ("基金简称", "基金名称")) or str(code)
        full_name = cls._field(fields, text, ("基金全称", "法定名称"))
        fund_type = cls._field(fields, text, ("基金类型", "基金类别"))
        risk = cls._field(fields, text, ("风险等级", "基金风险等级", "风险特征"))
        inception = cls._normalize_date(cls._field(fields, text, ("成立日期", "基金合同生效日")))
        asset_scale = cls._field(fields, text, ("基金规模", "最新规模", "资产规模"))
        nav_date = cls._normalize_date(cls._field(fields, text, ("净值日期", "最新净值日期", "单位净值日期")))
        nav_match = re.search(r"(?:最新净值|单位净值)[^\d]{0,30}(\d+\.\d+)", text)
        if nav_match:
            fields["最新净值"] = nav_match.group(1)
        if nav_date:
            fields["最新净值日期"] = nav_date
        subscription_raw = cls._field(fields, text, ("申购状态", "申购业务状态", "开放申购"))
        redemption_raw = cls._field(fields, text, ("赎回状态", "赎回业务状态", "开放赎回"))
        subscription = cls._status(subscription_raw)
        redemption = cls._status(redemption_raw)
        if subscription_raw:
            fields["申购状态"] = subscription_raw
        if redemption_raw:
            fields["赎回状态"] = redemption_raw
        if subscription is not None or redemption is not None:
            parts = []
            if subscription is not None:
                parts.append("申购开放" if subscription else "申购关闭")
            if redemption is not None:
                parts.append("赎回开放" if redemption else "赎回关闭")
            trade_status = "；".join(parts)
        else:
            trade_status = "官网产品页未公开当前交易状态"
        limit, quota_type, quota_remark = cls._direct_limit_from_text(text)
        if limit is not None:
            fields["直销/申购限额"] = limit
            fields["限额类型"] = quota_type
            fields["限额说明"] = quota_remark
        fields["基金代码"] = str(code)
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
            raise PermissionError("银华基金产品页需要认证")
        response.raise_for_status()
        actual_url = getattr(response, "url", "") or url
        self._last_product_html = response.content
        self._last_product_code = fund.code
        self._last_product_url = actual_url
        return self.parse_product_html(response.content, fund.code, actual_url, self._observed_at())

    @classmethod
    def parse_notice_list_html(cls, html: bytes, source_url: str = "") -> List[Dict[str, str]]:
        """解析银华官方产品页/公告页中的限额公告链接。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        rows: List[Dict[str, str]] = []
        seen = set()
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            if href.lower().startswith("javascript:"):
                continue
            parent = anchor.find_parent(["li", "tr", "article", "div"]) or anchor.parent
            parent_text = cls._clean(parent.get_text(" ", strip=True) if parent else "")
            title = cls._clean(anchor.get("title") or anchor.get_text(" ", strip=True) or parent_text)
            joined = f"{title} {parent_text}"
            if not any(term in joined for term in cls.relevant_notice_terms):
                continue
            url = urljoin(source_url or cls.notice_url, href)
            if not cls._is_official_url(url) or url in seen:
                continue
            seen.add(url)
            date_match = re.search(r"(?:20\d{2}[年/.-]\d{1,2}[月/.-]\d{1,2})|(?:20\d{6})", joined)
            rows.append({
                "title": title,
                "date": cls._normalize_date(date_match.group(0) if date_match else ""),
                "url": url,
                "source_type": cls.source_type_notice_pdf if re.search(r"\.pdf(?:$|[?#])", url, re.I) else cls.source_type_notice,
            })
        return sorted(rows, key=lambda row: row.get("date", ""), reverse=True)

    @classmethod
    def _document_text(cls, content: bytes, source_url: str) -> str:
        if str(source_url).lower().split("?", 1)[0].endswith(".pdf") or bytes(content or b"").startswith(b"%PDF"):
            try:
                return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
            except (PdfReadError, ValueError, OSError):
                return ""
        return cls._clean(BeautifulSoup(bytes(content or b""), "html.parser").get_text(" ", strip=True))

    @classmethod
    def parse_announcement_text(
        cls,
        text: str,
        code: str,
        title: str = "",
        announcement_date: str = "",
        source_url: str = "",
    ) -> Optional[Dict[str, str]]:
        """解析银华公告中明确属于目标基金份额的直销限额/状态。"""
        normalized = cls._clean(text)
        title_text = cls._clean(title)
        if code and code not in normalized and code not in title_text:
            return None
        limit, quota_type, remark = cls._direct_limit_from_text(f"{title_text} {normalized}")
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
            "announcement_text": normalized,
        }

    def _fetch_notice(self, code: str, product_html: Optional[bytes] = None, product_url: str = "") -> Optional[Dict[str, str]]:
        if product_html is None and self._last_product_code == code:
            product_html, product_url = self._last_product_html, self._last_product_url
        if not product_html:
            return None
        candidates = self.parse_notice_list_html(product_html, product_url or self.catalogue_url)
        for row in candidates:
            if code not in row.get("title", "") and code not in row.get("url", ""):
                continue
            response = self.session.get(row["url"], headers=self._headers(product_url or self.home_url), timeout=max(self.timeout, 25))
            if response.status_code in {401, 403}:
                raise PermissionError("银华基金公告详情需要认证")
            response.raise_for_status()
            actual_url = getattr(response, "url", "") or row["url"]
            parsed = self.parse_announcement_text(
                self._document_text(response.content, actual_url), code, row.get("title", ""), row.get("date", ""), actual_url
            )
            if parsed:
                return parsed
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
                channel="银华官网直销交易入口",
                limit=None,
                status="official_interface_requires_auth",
                quota_remark=str(exc),
                source_url=self.trade_login_url,
                observed_at=observed,
                raw={"error": str(exc)},
            )
        except requests.RequestException as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="银华官网产品页/公告",
                limit=None,
                status="official_api_no_data",
                quota_remark=f"官网公开页面当前不可读取：{exc}",
                source_url=self.product_url_template.format(code=fund.code),
                observed_at=observed,
                raw={"error": str(exc)},
            )
        limit = product.fields.get("直销/申购限额")
        quota_type = product.fields.get("限额类型", "")
        remark = product.fields.get("限额说明", "")
        raw = dict(product.fields)
        if limit is None:
            notice = self._fetch_notice(fund.code, self._last_product_html, product.source_url)
            if notice:
                limit = notice["limit"]
                quota_type = notice.get("quota_type", "")
                remark = notice.get("quota_remark", "")
                raw.update(notice)
                return DirectLimitSnapshot(
                    manager_id=self.manager_id, code=fund.code, customer_type="individual",
                    channel="银华基金官网公告/直销渠道", limit=limit, status=notice["status"],
                    quota_type=quota_type, quota_remark=remark, source_url=notice["source_url"],
                    observed_at=product.observed_at, raw=raw,
                )
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="银华基金官网产品页/公告",
            limit=limit,
            status="ok" if limit is not None else "official_api_no_data",
            quota_type=quota_type,
            quota_remark=remark or "官网公开产品页/公告未提供当前可验证直销限额。",
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw=raw,
        )

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        try:
            product = self.fetch_product(fund)
            subscription = self._status(product.fields.get("申购状态", ""))
            redemption = self._status(product.fields.get("赎回状态", ""))
            channel = ChannelTradeStatus(
                customer_type="individual", channel="银华官网公开产品页",
                subscription=subscription, redemption=redemption,
                quota_remark="仅使用官网公开产品页字段；未从交易导航推断状态。", raw=product.fields,
            )
            return TradeSnapshot(
                manager_id=self.manager_id, code=fund.code, channels=[channel], api_status=0,
                message=product.trade_status, source_url=product.source_url,
                observed_at=product.observed_at, raw=product.fields,
            )
        except PermissionError as exc:
            return TradeSnapshot(
                manager_id=self.manager_id, code=fund.code,
                channels=[ChannelTradeStatus(customer_type="individual", channel="银华官网直销交易入口", quota_remark=str(exc))],
                api_status=401, message="银华官网直销交易入口需要登录和验证码",
                source_url=self.trade_login_url, observed_at=self._observed_at(), raw={"error": str(exc)},
            )
        except requests.RequestException as exc:
            return TradeSnapshot(
                manager_id=self.manager_id, code=fund.code,
                channels=[ChannelTradeStatus(customer_type="individual", channel="银华官网公开产品页", quota_remark=str(exc))],
                api_status=None, message="银华官网公开产品页当前不可读取", source_url=self.product_url_template.format(code=fund.code),
                observed_at=self._observed_at(), raw={"error": str(exc)},
            )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        if snapshot.status != "ok":
            return _record(
                "未获取", snapshot.channel, snapshot.source_url,
                snapshot.quota_remark or "银华官网直销交易接口需要认证，未绕过认证。", status=snapshot.status,
            )
        return _record(
            snapshot.limit or "未获取", snapshot.channel, snapshot.source_url,
            snapshot.quota_remark or "银华官网公开产品页/公告明确当前直销限额。",
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public_with_trade_auth_boundary",
            reason="银华官网产品/公告入口可公开追踪；网上直销交易入口要求账号、密码和图片验证码，未登录无法查询交易限额。",
            requires_login=True,
            requires_captcha=True,
            requires_device_signature=False,
            requires_bank_card=False,
        )


YHFundAdapter = YinhuAdapter
