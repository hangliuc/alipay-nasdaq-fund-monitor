"""招商基金（cmfchina.com）官方独立 Adapter。

招商官网把基金目录、产品资料和交易状态分别提供在两套公开入口：

* ``common.cmfchina.com/ecwebbff`` 是官网 H5 使用的匿名 JSON API；
* ``www.cmfchina.com/web/fundDetail/{code}/index.html`` 是服务端渲染的
  产品页，包含当前产品公告列表和购买/定投入口。

官网没有在匿名产品详情 API 中直接返回“大额申购上限”字段。Adapter 因此
只从产品页列出的招商官方公告详情中解析“限制申购金额”，并根据公告正文
中的恢复生效日期计算观察日状态。所有交易提交链接均不访问；遇到登录、
验证码、设备签名或银行卡绑定边界时不绕过。
"""

from datetime import datetime, timezone
import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
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


class CmfchinaAdapter:
    """招商基金官网公开目录、产品页和直销公告 Adapter。"""

    manager_id = "招商"
    home_url = "https://www.cmfchina.com/"
    catalogue_page_url = "https://static.cmfchina.com/uniBrowH5/common/allFundList.html"
    catalogue_api_url = "https://common.cmfchina.com/ecwebbff/fund/fundInfo/queryAllFundList"
    detail_api_url = "https://common.cmfchina.com/ecwebbff/fund/fundInfo/queryFundInfoDetail"
    trade_rule_api_url = "https://common.cmfchina.com/ecwebbff/fund/fundInfo/queryFundTradeRule"
    product_url_template = "https://www.cmfchina.com/web/fundDetail/{code}/index.html"
    notice_url_template = "https://www.cmfchina.com/web/noticedetails/{notice_id}/index.html"

    source_type_catalogue = "cmfchina_official_public_fund_catalogue_api"
    source_type_product = "cmfchina_official_product_page"
    source_type_detail_api = "cmfchina_official_fund_detail_api"
    source_type_trade_api = "cmfchina_official_trade_status_api"
    source_type_notice = "cmfchina_official_notice_detail"
    fund_type_names = {
        "0": "股票型",
        "1": "货币型",
        "2": "债券型",
        "3": "混合型",
        "4": "指数型",
        "5": "QDII",
        "6": "REITs",
        "7": "FOF",
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

    def _observed_at(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def _headers(cls, referer: str = "", json_request: bool = False) -> Dict[str, str]:
        headers = {
            **HEADERS,
            "Referer": referer or cls.catalogue_page_url,
            "Origin": "https://static.cmfchina.com",
            # These headers are emitted by招商 H5's public network wrapper. They
            # identify the public browser channel and are not an account token.
            "cmfHeaders": json.dumps(
                {"BUNDLEID": "com.cmfchina.h5web", "CHANNELID": "", "subChannelId": "", "CLIENTID": ""},
                separators=(",", ":"),
            ),
            "smSslFlag": "N",
            "encryptFlag": "N",
            "signFlag": "N",
            "encryptDN": "",
        }
        if json_request:
            headers["Content-Type"] = "application/json; charset=UTF-8"
        return headers

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
            source_url=cls.product_url_template.format(code=code),
            source_type="config_compatibility",
        )

    @classmethod
    def catalogue_params(cls, page_index: int = 0, page_size: int = 1000) -> Dict[str, Any]:
        """官网 H5 ``queryAllFundList`` 的公开参数。"""
        return {
            "sortType": "YIELD_DAY",
            "orderType": "DESC",
            "fundType": "ALL",
            "pageIndex": page_index,
            "pageSize": page_size,
            "tradeCategory": "",
            "filterCriteriaVoList": [],
        }

    @classmethod
    def parse_catalogue_payload(cls, payload: Any, source_url: str = "") -> List[FundIdentity]:
        """解析官网 ``data.fundInfoList`` 的全量当前基金/份额目录。"""
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        rows = data.get("fundInfoList") or data.get("fundInfoVoList") or []
        found: Dict[str, FundIdentity] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            code = cls._clean(row.get("fundId"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = cls._clean(row.get("fundName") or code)
            fund_type_code = cls._clean(row.get("fundType"))
            found[code] = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=cls.fund_type_names.get(fund_type_code, fund_type_code),
                share_class=cls._share_class(name),
                source_url=cls.product_url_template.format(code=code),
                source_type=cls.source_type_catalogue,
            )
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        """兼容解析官网 SSR 产品链接列表；全量发现优先使用 JSON API。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        found: Dict[str, FundIdentity] = {}
        for anchor in soup.select('a[href*="/web/fundDetail/"]'):
            match = re.search(r"/web/fundDetail/(\d{6})/", str(anchor.get("href") or ""))
            if not match:
                continue
            code = match.group(1)
            name_node = anchor.select_one(".pub_title_span") or anchor
            name = cls._clean(name_node.get_text(" ", strip=True))
            if name in {"切换C类", "切换A类"} or not name:
                continue
            found[code] = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                share_class=cls._share_class(name),
                source_url=urljoin(source_url or cls.home_url, str(anchor.get("href"))),
                source_type=cls.source_type_product,
            )
        return sorted(found.values(), key=lambda item: item.code)

    def _post(self, url: str, payload: Dict[str, Any], referer: str = "") -> Dict[str, Any]:
        response = self.session.post(
            url,
            json=payload,
            headers=self._headers(referer, json_request=True),
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("招商基金官方接口需要认证")
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("招商基金官方接口返回不是 JSON 对象")
        return result

    def discover_funds(self) -> List[FundIdentity]:
        payload = self._post(self.catalogue_api_url, self.catalogue_params(), self.catalogue_page_url)
        if str(payload.get("resultCode")) != "0000":
            raise RuntimeError(str(payload.get("resultMsg") or "招商基金目录接口失败"))
        funds = self.parse_catalogue_payload(payload, self.catalogue_api_url)
        if not funds:
            raise RuntimeError("招商基金目录接口未返回基金份额")
        return funds

    @classmethod
    def _pairs_from_table(cls, soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for row in soup.find_all("tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            for index in range(0, len(cells) - 1, 2):
                key = cells[index].rstrip("：:").strip()
                if key:
                    fields[key] = cells[index + 1]
        return fields

    @classmethod
    def _button_status(cls, node: Any) -> str:
        if node is None:
            return "unknown"
        classes = set(node.get("class") or [])
        href = str(node.get("href") or "")
        return "closed" if "disabled" in classes or not href or href.lower().startswith("javascript:") else "open"

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        """解析服务端渲染产品页的资料、净值和公开交易按钮。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._pairs_from_table(soup)
        header = soup.select_one(".pro_name")
        name_node = header.select_one("h5") if header else None
        name = cls._clean(name_node.get_text(" ", strip=True) if name_node else "") or cls._clean(fields.get("基金简称")) or str(code)
        full_name = cls._clean(fields.get("基金全称"))
        fund_type = cls._clean(fields.get("基金类型"))
        risk = cls._clean(fields.get("风险等级"))
        code_node = header.select_one(".fund_code") if header else None
        fields["基金代码"] = cls._clean(code_node.get_text(" ", strip=True) if code_node else code)
        fields["基金简称"] = name
        if full_name:
            fields["基金全称"] = full_name
        nav_node = soup.select_one(".pro_intro_data .item") or soup.select_one(".pro_intro_data_wrap .item")
        if nav_node:
            strong = nav_node.select_one("strong")
            if strong:
                fields["最新净值"] = cls._clean(strong.get_text(" ", strip=True))
            label = nav_node.select_one("p")
            if label:
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", cls._clean(label.get_text(" ", strip=True)))
                if date_match:
                    fields["最新净值日期"] = date_match.group(1)
        buy_status = cls._button_status(soup.select_one(".btn_buy"))
        sip_status = cls._button_status(soup.select_one(".btn_invest"))
        fields["购买按钮状态"] = buy_status
        fields["定投按钮状态"] = sip_status
        notices = cls.parse_notice_list_html(html, source_url)
        fields["产品公告条数"] = str(len(notices))
        status_parts = []
        if buy_status != "unknown":
            status_parts.append("购买可用" if buy_status == "open" else "购买不可用")
        if sip_status != "unknown":
            status_parts.append("定投可用" if sip_status == "open" else "定投不可用")
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=fund_type,
            risk_level=risk,
            inception_date=cls._normalize_date(fields.get("成立日期")),
            asset_scale=fields.get("净值资产", ""),
            net_value_date=fields.get("最新净值日期", ""),
            trade_status="；".join(status_parts) or "官网产品页未公开当前交易状态",
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    @classmethod
    def parse_detail_payload(
        cls, payload: Any, fund: FundIdentity, product: ProductSnapshot, observed_at: str
    ) -> ProductSnapshot:
        """把匿名详情 API 的全部顶层字段保留到结构化快照。"""
        if not isinstance(payload, dict):
            return product
        data = payload.get("data")
        if not isinstance(data, dict):
            return product
        fields = dict(product.fields)
        for key, value in data.items():
            if value not in (None, ""):
                fields[f"API_{key}"] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        name = cls._clean(data.get("fundName") or product.name)
        fund_type = cls._clean(data.get("fundTypeDesc") or product.fund_type)
        risk = cls._clean(data.get("fundRiskLevelDesc") or product.risk_level)
        inception = cls._normalize_date(data.get("establishDate") or product.inception_date)
        nav_date = product.net_value_date
        nav_rows = data.get("fundNavInfoVoList")
        if isinstance(nav_rows, list) and nav_rows and isinstance(nav_rows[0], dict):
            nav_date = cls._normalize_date(nav_rows[0].get("navDate")) or nav_date
            fields["最新净值"] = cls._clean(nav_rows[0].get("navDisp") or nav_rows[0].get("nav"))
            fields["最新净值日期"] = nav_date
        trade = data.get("tradeStatusVo") if isinstance(data.get("tradeStatusVo"), dict) else {}
        status_parts = []
        if trade:
            status_parts.append("购买可用" if str(trade.get("canBuyFlag")) == "Y" else "购买不可用")
            status_parts.append("赎回可用" if str(trade.get("canRedeemFlag")) == "Y" else "赎回不可用")
            status_parts.append("定投可用" if str(trade.get("canMipFlag")) == "Y" else "定投不可用")
            fields["购买按钮状态"] = "open" if str(trade.get("canBuyFlag")) == "Y" else "closed"
            fields["定投按钮状态"] = "open" if str(trade.get("canMipFlag")) == "Y" else "closed"
        return ProductSnapshot(
            manager_id=product.manager_id,
            code=product.code,
            name=name,
            full_name=product.full_name,
            fund_type=fund_type,
            risk_level=risk,
            inception_date=inception,
            asset_scale=cls._clean(data.get("fundSum") or product.asset_scale),
            net_value_date=nav_date,
            trade_status="；".join(status_parts) or product.trade_status,
            source_url=product.source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("招商基金产品页需要认证")
        response.raise_for_status()
        observed = self._observed_at()
        product = self.parse_product_html(response.content, fund.code, response.url or url, observed)
        try:
            payload = self._post(self.detail_api_url, {"fundId": fund.code}, self.catalogue_page_url)
            if str(payload.get("resultCode")) == "0000":
                product = self.parse_detail_payload(payload, fund, product, observed)
        except (PermissionError, requests.RequestException, ValueError, TypeError):
            # 服务端产品页仍是一手公开来源；API 暂时不可用不抹掉产品快照。
            pass
        return product

    @classmethod
    def _flag_bool(cls, value: str) -> Optional[bool]:
        if value == "open":
            return True
        if value == "closed":
            return False
        return None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="招商基金官网产品页/官方详情 API",
            subscription=self._flag_bool(product.fields.get("购买按钮状态", "")),
            redemption=None,
            sip=self._flag_bool(product.fields.get("定投按钮状态", "")),
            quota_remark="交易状态来自招商官网产品页按钮及 common.cmfchina.com 官方详情 API；未访问交易提交页。",
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
    def parse_notice_list_html(cls, html: bytes, source_url: str = "") -> List[Tuple[str, str, str]]:
        """解析产品页公开展示的公告标题、发布日期和详情链接。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        result: List[Tuple[str, str, str]] = []
        for anchor in soup.select('a[href*="/web/noticedetails/"]'):
            match = re.search(r"/web/noticedetails/(\d+)/", str(anchor.get("href") or ""))
            if not match:
                continue
            p = anchor.select_one("p")
            title = cls._clean(p.get_text(" ", strip=True) if p else anchor.get_text(" ", strip=True))
            date_node = anchor.select_one(".date")
            date_text = cls._normalize_date(date_node.get_text(" ", strip=True) if date_node else "")
            result.append((title, date_text, urljoin(source_url or cls.home_url, str(anchor.get("href")))))
        return result

    @classmethod
    def _notice_is_limit_related(cls, title: str) -> bool:
        compact = cls._clean(title).replace(" ", "")
        return "大额" in compact and any(word in compact for word in ("申购", "定期定额", "定投", "转换转入"))

    @classmethod
    def _parse_date_from_text(cls, text: str) -> str:
        match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}" if match else ""

    @classmethod
    def _date_leq_observed(cls, date_text: str, observed_at: Optional[str]) -> bool:
        if not date_text or not observed_at:
            return True
        try:
            return datetime.fromisoformat(date_text).date() <= datetime.fromisoformat(observed_at).date()
        except ValueError:
            return True

    @classmethod
    def _row_values(cls, soup: BeautifulSoup, label_words: Iterable[str]) -> Tuple[List[str], str]:
        for row in soup.find_all("tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            if not cells:
                continue
            label = cells[0]
            if any(word in label for word in label_words):
                return cells[1:], label
        return [], ""

    @classmethod
    def parse_notice_html(
        cls,
        html: bytes,
        code: str,
        source_url: str,
        observed_at: Optional[str] = None,
        announcement_date: str = "",
    ) -> Optional[Dict[str, str]]:
        """解析招商公告详情，按份额代码取对应限制申购金额。

        公告中的“暂停”通常是业务术语：金额字段仍代表单日单账户上限。
        若正文明确写出恢复日期且恢复日已经到达，则当前状态返回“不限”。
        """
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        text = cls._clean(soup.get_text(" ", strip=True))
        title_node = soup.find("h2") or soup.find("h1")
        title = cls._clean(title_node.get_text(" ", strip=True) if title_node else "")
        if not title:
            title = "招商基金官网产品公告"
        if not cls._notice_is_limit_related(title) and "限制申购金额" not in text:
            return None

        codes, _ = cls._row_values(soup, ("下属分级基金的交易代码",))
        amount_values, _ = cls._row_values(soup, ("下属分级基金的限制申购金额",))
        paused_values, _ = cls._row_values(soup, ("该分级基金是否暂停",))
        amount_text = ""
        if code in codes:
            index = codes.index(code)
            if index < len(amount_values):
                amount_text = amount_values[index]
        if not amount_text:
            # 无份额拆分的公告使用主代码和一个金额。
            main_code_match = re.search(r"基金主代码\s*([0-9]{6})", text)
            if main_code_match and main_code_match.group(1) == code:
                amount_values, _ = cls._row_values(soup, ("限制申购金额",))
                amount_text = amount_values[0] if amount_values else ""

        limit = _amount(f"{amount_text}元") if amount_text else None
        effective_date = cls._parse_date_from_text(text)
        restore_match = re.search(
            r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起?\s*恢复",
            text,
        )
        restore_date = ""
        if restore_match:
            year = restore_match.group(1) or (effective_date[:4] if effective_date else "")
            if year:
                restore_date = f"{year}-{int(restore_match.group(2)):02d}-{int(restore_match.group(3)):02d}"
        if restore_date and cls._date_leq_observed(restore_date, observed_at):
            limit = "不限"
            status = "ok"
            remark = f"公告正文明确自{restore_date}恢复大额申购/定投，观察日已超过恢复日。"
            quota_type = "官网公告恢复状态"
        elif limit:
            status = "ok"
            remark = "招商基金官网公告按份额代码列明限制申购金额；该金额适用于直销机构公告口径。"
            quota_type = "官网公告限制申购金额"
        elif "恢复" in title or "恢复" in text:
            limit = "不限"
            status = "ok"
            remark = "公告明确恢复大额申购/定投业务，未列出金额上限。"
            quota_type = "官网公告恢复状态"
        elif "暂停" in title or "暂停" in text:
            limit = "暂停"
            status = "paused"
            remark = "公告明确暂停大额申购/定投业务，正文未列出可用金额上限。"
            quota_type = "官网公告暂停状态"
        else:
            return None
        return {
            "limit": limit,
            "status": status,
            "quota_type": quota_type,
            "quota_remark": remark,
            "announcement_title": title,
            "announcement_date": cls._normalize_date(announcement_date) or effective_date,
            "source_url": source_url,
            "announcement_effective_date": effective_date,
            "announcement_restore_date": restore_date,
            "announcement_paused": paused_values[codes.index(code)] if code in codes and codes.index(code) < len(paused_values) else "",
        }

    def _fetch_notice(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        product_url = self.product_url_template.format(code=fund.code)
        response = self.session.get(product_url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("招商基金产品公告列表需要认证")
        response.raise_for_status()
        entries = self.parse_notice_list_html(response.content, response.url or product_url)
        candidates = [entry for entry in entries if self._notice_is_limit_related(entry[0])]
        candidates.sort(key=lambda item: item[1], reverse=True)
        observed = self._observed_at()
        for title, date_text, detail_url in candidates:
            detail = self.session.get(detail_url, headers=self._headers(product_url), timeout=self.timeout)
            if detail.status_code in {401, 403}:
                raise PermissionError("招商基金公告详情需要认证")
            detail.raise_for_status()
            parsed = self.parse_notice_html(detail.content, fund.code, detail.url or detail_url, observed, date_text)
            if parsed:
                return parsed
        return None

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        observed = self._observed_at()
        product_url = self.product_url_template.format(code=fund.code)
        try:
            notice = self._fetch_notice(fund)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="招商基金官网产品公告",
                limit=None,
                status="official_interface_requires_auth",
                quota_remark=str(exc),
                source_url=product_url,
                observed_at=observed,
                raw={"error": str(exc)},
            )
        except (requests.RequestException, ValueError, TypeError):
            notice = None
        if notice:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="招商基金官网直销机构公告",
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
            channel="招商基金官网直销机构公告",
            limit=None,
            status="official_api_no_data",
            quota_type="",
            quota_remark="产品页公开公告列表未发现可解析的最新大额申购/定投公告；不以最低投资额代替直销限额。",
            source_url=product_url,
            observed_at=observed,
            raw={},
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(
            snapshot.limit or "未获取",
            "招商基金官网直销机构公告",
            snapshot.source_url,
            snapshot.quota_remark,
            status=snapshot.status,
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public_with_auth_trade_boundary",
            reason="招商官网目录 API、产品资料/交易状态 API、产品页和产品公告详情均可匿名访问；购买/定投提交入口位于 direct.cmfchina.com，可能要求登录、验证码、设备签名或银行卡绑定，Adapter 不访问或绕过该边界。",
            requires_login=False,
        )


# 便于外部按管理人英文名引用，同时保留清晰的公司名类名。
ChinaMerchantsAdapter = CmfchinaAdapter
