"""长城基金官网独立 Adapter。

长城基金的公开基金产品页使用静态 HTML，基金目录页列出当前公开的全部基金
份额、风险等级、净值和目录交易状态。产品页的“基金公告/临时公告”由官网
公开接口按基金代码加载，附件通常是长城官网 PDF。适配器提供目录和产品页
的稳定解析，并按公告日期倒序读取“大额申购/定投”公告。

本模块只访问 ``ccfund.com.cn`` 官方域名，不访问购买/登录提交页，不把第三方
平台数据当作直销额度。长城公告接口目前由官网前端封装请求，调用失败时返回
``official_api_no_data``，不沿用历史硬编码值，也不猜测“不限”。
"""

from datetime import datetime, timezone
from io import BytesIO
import re
from typing import Any, Callable, Dict, List, Optional
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


class ChangchengAdapter:
    """长城基金官网基金目录、产品资料、交易状态和限额公告 Adapter。"""

    manager_id = "长城"
    home_url = "https://www.ccfund.com.cn/"
    catalogue_url = "https://www.ccfund.com.cn/main/jjcp/index.shtml"
    product_url_template = "https://www.ccfund.com.cn/main/jjcp/cache/{code}.shtml"
    notice_api_url = "https://www.ccfund.com.cn/fund/querycatdatajj.do"
    # 产品页前端使用的“临时公告”目录编号。
    temporary_notice_catalogue = "998"
    source_type_catalogue = "changcheng_official_fund_catalogue_page"
    source_type_product = "changcheng_official_product_page"
    source_type_notice = "changcheng_official_notice_pdf"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._catalogue_rows: Optional[Dict[str, Dict[str, str]]] = None

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
        match = re.search(r"([A-Z])(?:类)?$", str(name or "").strip())
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
    def parse_catalogue_html(cls, html: bytes, source_url: str = "") -> List[FundIdentity]:
        """解析官网基金产品页中的当前基金/份额目录。

        长城官网将基金名称和代码放在同一 ``a`` 元素的 ``.name/.desc`` 节点，
        风险、净值日期和目录交易状态位于同一行的后续 ``td``。这里保留这些
        原始目录字段，供 ``fetch_trade_status`` 使用。
        """
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        found: Dict[str, FundIdentity] = {}
        for row in soup.select("tr"):
            anchor = row.find("a", href=re.compile(r"/main/jjcp/cache/\d{6}\.shtml"))
            if anchor is None:
                continue
            href = str(anchor.get("href") or "")
            match = re.search(r"/main/jjcp/cache/(\d{6})\.shtml", href)
            if not match:
                continue
            code = match.group(1)
            name_node = anchor.select_one(".name")
            code_node = anchor.select_one(".desc")
            name = cls._clean(name_node.get_text(" ", strip=True) if name_node else anchor.get_text(" ", strip=True))
            listed_code = cls._clean(code_node.get_text(" ", strip=True) if code_node else code)
            if re.fullmatch(r"\d{6}", listed_code):
                code = listed_code
            if not name:
                continue
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            risk = cells[1] if len(cells) > 1 else ""
            # 非货币型目录最后一列是当前交易状态；货币型表格列数略有不同，
            # 因此用状态关键词查找，而不是依赖绝对列号。
            status = next((cell for cell in reversed(cells) if any(k in cell for k in ("正常", "暂停申购", "暂停运作", "封闭期"))), "")
            row_data = {
                "name": name,
                "fund_type_code": cls._clean((row.find("td") or {}).get("jjlx") if row.find("td") else ""),
                "risk_level": risk,
                "nav": cells[3] if len(cells) > 3 else "",
                "nav_date": cls._normalize_date(re.search(r"\d{8}", " ".join(cells)).group(0) if re.search(r"\d{8}", " ".join(cells)) else ""),
                "trade_status": status,
            }
            identity = FundIdentity(
                manager_id=cls.manager_id,
                code=code,
                name=name,
                fund_type=row_data["fund_type_code"],
                share_class=cls._share_class(name),
                source_url=urljoin(source_url or cls.catalogue_url, href),
                source_type=cls.source_type_catalogue,
            )
            found[code] = identity
        return sorted(found.values(), key=lambda item: item.code)

    @classmethod
    def _catalogue_row_map(cls, html: bytes) -> Dict[str, Dict[str, str]]:
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        rows: Dict[str, Dict[str, str]] = {}
        for row in soup.select("tr"):
            anchor = row.find("a", href=re.compile(r"/main/jjcp/cache/\d{6}\.shtml"))
            if anchor is None:
                continue
            match = re.search(r"/main/jjcp/cache/(\d{6})\.shtml", str(anchor.get("href") or ""))
            if not match:
                continue
            code = match.group(1)
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            status = next((cell for cell in reversed(cells) if any(k in cell for k in ("正常", "暂停申购", "暂停运作", "封闭期"))), "")
            date_match = re.search(r"\d{8}", " ".join(cells))
            first_cell = row.find("td")
            rows[code] = {
                "fund_type_code": cls._clean(first_cell.get("jjlx") if first_cell else ""),
                "risk_level": cells[1] if len(cells) > 1 else "",
                "nav": cells[3] if len(cells) > 3 else "",
                "nav_date": cls._normalize_date(date_match.group(0) if date_match else ""),
                "trade_status": status,
            }
        return rows

    def discover_funds(self) -> List[FundIdentity]:
        """从长城官网当前基金产品页发现全部公开基金份额。"""
        response = self.session.get(self.catalogue_url, headers=self._headers(self.home_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("长城基金官网基金目录需要认证")
        response.raise_for_status()
        self._catalogue_rows = self._catalogue_row_map(response.content)
        return self.parse_catalogue_html(response.content, response.url or self.catalogue_url)

    @classmethod
    def _label_fields(cls, soup: BeautifulSoup) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for row in soup.select("table.abstract_tb tr") or soup.find_all("tr"):
            cells = [cls._clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            for index in range(0, len(cells) - 1, 2):
                key = cells[index].rstrip("：:").strip()
                if key:
                    fields[key] = cells[index + 1]
        return fields

    @classmethod
    def parse_product_html(cls, html: bytes, code: str, source_url: str, observed_at: str) -> ProductSnapshot:
        """解析长城产品页的结构化资料、净值和销售机构信息。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        fields = cls._label_fields(soup)
        name_node = soup.select_one(".fund_title .fund_name, .fund_name")
        code_node = soup.select_one(".fund_title .fund_code, .fund_code")
        name = cls._clean(name_node.get_text(" ", strip=True) if name_node else "") or fields.get("基金简称", "") or str(code)
        page_code = cls._clean(code_node.get_text(" ", strip=True) if code_node else "")
        if re.fullmatch(r"\d{6}", page_code):
            fields["基金代码"] = page_code
        else:
            fields["基金代码"] = str(code)
        tags = [cls._clean(node.get_text(" ", strip=True)) for node in soup.select(".fund_title ~ .tag_list li, .info_left_box .tag_list li")]
        fund_type = fields.get("基金类型", tags[0] if tags else "")
        risk = fields.get("风险等级", next((tag for tag in tags if "风险" in tag or tag.startswith("R")), ""))
        full_name = fields.get("基金全称", "")
        inception = cls._normalize_date(fields.get("成立日期") or fields.get("基金合同生效日"))
        nav_date = cls._normalize_date(fields.get("净值日期") or fields.get("单位净值日期"))
        page_text = cls._clean(soup.get_text(" ", strip=True))
        nav_match = re.search(r"(\d{4}-\d{2}-\d{2})净值\(元\)\s*([\d.]+)", page_text)
        if nav_match:
            nav_date = nav_date or nav_match.group(1)
            fields["最新净值"] = nav_match.group(2)
            fields["最新净值日期"] = nav_match.group(1)
        if not nav_date:
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})净值", soup.get_text(" ", strip=True))
            nav_date = date_match.group(1) if date_match else ""
        fields["产品页直销平台"] = "长城基金管理有限公司直销中心；长城基金 APP/微信公众号"
        fields["产品页销售机构信息"] = "公开展示直销平台、直销机构和代销机构"
        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=str(code),
            name=name,
            full_name=full_name,
            fund_type=fund_type,
            risk_level=risk,
            inception_date=inception,
            asset_scale=fields.get("基金规模", ""),
            net_value_date=nav_date,
            trade_status=fields.get("交易状态", "") or "官网产品页未公开当前交易状态",
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=self._headers(self.catalogue_url), timeout=self.timeout)
        if response.status_code in {401, 403}:
            raise PermissionError("长城基金官网产品页需要认证")
        response.raise_for_status()
        return self.parse_product_html(response.content, fund.code, response.url or url, self._observed_at())

    @staticmethod
    def _status_bool(value: str) -> Optional[bool]:
        text = str(value or "")
        if any(word in text for word in ("暂停申购", "暂停运作", "封闭期")):
            return False
        if any(word in text for word in ("正常", "开放")):
            return True
        return None

    def fetch_trade_status(self, fund: FundIdentity) -> TradeSnapshot:
        product = self.fetch_product(fund)
        row = (self._catalogue_rows or {}).get(fund.code, {})
        status_text = row.get("trade_status") or product.trade_status
        channel = ChannelTradeStatus(
            customer_type="individual",
            channel="长城基金官网基金目录/产品页",
            subscription=self._status_bool(status_text),
            redemption=None,
            sip=self._status_bool(status_text),
            quota_remark="交易状态来自长城官网当前基金目录；直销限额来自基金产品页临时公告 PDF。",
            raw={**product.fields, **row},
        )
        return TradeSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            channels=[channel],
            api_status=0,
            message=status_text,
            source_url=product.source_url,
            observed_at=product.observed_at,
            raw={**product.fields, **row},
        )

    @classmethod
    def parse_notice_list_html(cls, html: bytes, source_url: str = "") -> List[Dict[str, str]]:
        """解析产品页/公告接口返回的公告列表 HTML。"""
        soup = BeautifulSoup(bytes(html or b""), "html.parser")
        candidates: List[Dict[str, str]] = []
        nodes = soup.select("ul.notice_list li") or soup.select("table tr")
        for node in nodes:
            anchor = node.find("a", href=True)
            if anchor is None:
                continue
            title = cls._clean((node.select_one(".title") or node).get_text(" ", strip=True))
            if not re.search(r"大额申购|定期定额投资|大额定投", title):
                continue
            if not re.search(r"调整|暂停|恢复", title):
                continue
            date_node = node.select_one(".date, .time")
            date_text = cls._normalize_date(date_node.get_text(" ", strip=True) if date_node else "")
            candidates.append({
                "title": title,
                "date": date_text,
                "url": urljoin(source_url or cls.home_url, str(anchor.get("href"))),
            })
        candidates.sort(key=lambda item: item.get("date", ""), reverse=True)
        return candidates

    @classmethod
    def parse_notice_list_payload(cls, payload: Any, source_url: str = "") -> List[Dict[str, str]]:
        """解析官网公告接口的 JSON 列表，兼容大小写字段名。"""
        if isinstance(payload, dict):
            data = payload.get("data")
            rows = data.get("list") if isinstance(data, dict) else data
            if rows is None:
                rows = payload.get("list") or payload.get("results")
        else:
            rows = payload
        candidates: List[Dict[str, str]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            title = cls._clean(row.get("TITLE") or row.get("title") or row.get("NAME") or row.get("name"))
            if not re.search(r"大额申购|定期定额投资|大额定投", title) or not re.search(r"调整|暂停|恢复", title):
                continue
            href = cls._clean(row.get("URL") or row.get("url") or row.get("href"))
            if not href:
                continue
            candidates.append({
                "title": title,
                "date": cls._normalize_date(row.get("PUBDATE") or row.get("pub_date") or row.get("DATE") or row.get("date")),
                "url": urljoin(source_url or cls.home_url, href),
            })
        return sorted(candidates, key=lambda item: item.get("date", ""), reverse=True)

    @classmethod
    def parse_announcement_text(
        cls,
        text: str,
        code: str,
        title: str = "",
        announcement_date: str = "",
        source_url: str = "",
    ) -> Optional[Dict[str, str]]:
        """解析长城公告中的目标份额直销限额、暂停或恢复状态。"""
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        title_text = cls._clean(title)
        combined = f"{title_text} {normalized}"
        direct = re.search(
            r"(?:本公司)?直销(?:渠道|机构)?[^。；;]{0,100}?(?:限额|不超过|不高于|应不超过)[^\d]{0,20}([\d,.]+)\s*(亿元|亿|万元|万|元)",
            combined,
        )
        limit = _amount("".join(direct.groups())) if direct else None
        remark = ""
        if limit:
            remark = "长城官网公告明确直销渠道单日累计申购/定投金额。"
        if limit is None:
            code_match = re.search(r"(?:下属分级基金的交易代码|下属基金份额的交易代码)\s*((?:\d{6}\s*)+)", normalized)
            amount_match = re.search(
                r"(?:限制申购金额|限制金额)[^\d]{0,60}((?:[\d,.]+\s*)+)(?=\s*(?:下属|限制定期|$))",
                normalized,
            )
            if code_match and amount_match:
                codes = re.findall(r"\d{6}", code_match.group(1))
                amounts = re.findall(r"[\d,.]+", amount_match.group(1))
                if code in codes:
                    index = codes.index(code)
                    if index < len(amounts):
                        limit = _amount(f"{amounts[index]}元")
                        remark = "长城官网公告按份额交易代码列出限制申购金额。"
        if limit is None:
            generic = re.search(r"(?:业务限额为|限制申购金额|限制金额)[^\d]{0,40}([\d,.]+)\s*(亿元|亿|万元|万|元)", combined)
            if generic:
                limit = _amount("".join(generic.groups()))
                remark = "长城官网公告明确的业务限制金额。"
        if limit is None and re.search(r"恢复(?:大额申购|大额定投|申购、定投)", combined):
            limit, remark = "不限", "长城官网公告明确恢复大额申购/定投业务，未列金额上限。"
        if limit is None and re.search(r"暂停(?:大额申购|大额定投|申购、定投)", combined):
            limit, remark = "暂停", "长城官网公告明确暂停大额申购/定投业务。"
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

    def _fetch_notice_candidates(self, code: str) -> List[Dict[str, str]]:
        """读取官网公告公开接口；官网要求前端加密时不绕过，返回空列表。"""
        params = {
            "pageNo": "1",
            "title": "",
            "startTime": "",
            "endTime": "",
            "catalogid": self.temporary_notice_catalogue,
            "fundcode": code,
        }
        try:
            response = self.session.get(
                self.notice_api_url,
                params=params,
                headers=self._headers(self.product_url_template.format(code=code)),
                timeout=self.timeout,
            )
            if response.status_code in {401, 403}:
                raise PermissionError("长城基金官网公告接口需要认证")
            if response.status_code >= 400:
                return []
            try:
                return self.parse_notice_list_payload(response.json(), response.url or self.notice_api_url)
            except (ValueError, TypeError):
                return self.parse_notice_list_html(response.content, response.url or self.notice_api_url)
        except requests.RequestException:
            return []

    def _fetch_notice(self, code: str) -> Optional[Dict[str, str]]:
        for item in self._fetch_notice_candidates(code):
            response = self.session.get(item["url"], headers=self._headers(self.notice_api_url), timeout=max(self.timeout, 25))
            if response.status_code in {401, 403}:
                raise PermissionError("长城基金公告 PDF 需要认证")
            response.raise_for_status()
            try:
                text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
            except (PdfReadError, ValueError, OSError):
                continue
            parsed = self.parse_announcement_text(text, code, item["title"], item.get("date", ""), item["url"])
            if parsed:
                parsed["announcement_text"] = text
                return parsed
        return None

    def fetch_direct_limit(self, fund: FundIdentity) -> DirectLimitSnapshot:
        try:
            product = self.fetch_product(fund)
            notice = self._fetch_notice(fund.code)
        except PermissionError as exc:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="长城基金官网产品页/公告接口",
                limit=None,
                status="official_interface_requires_auth",
                quota_remark=str(exc),
                source_url=self.product_url_template.format(code=fund.code),
                observed_at=self._observed_at(),
                raw={"error": str(exc)},
            )
        if notice is None:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="长城基金官网产品页/公告接口/PDF",
                limit=None,
                status="official_api_no_data",
                quota_remark="产品页可访问，但当前公告接口未返回可确认的最新限额；不沿用历史值。",
                source_url=product.source_url,
                observed_at=product.observed_at,
                raw=product.fields,
            )
        raw = dict(product.fields)
        raw.update(notice)
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type="individual",
            channel="长城基金官网最新限额公告 PDF",
            limit=notice["limit"],
            status=notice["status"],
            quota_type=notice["quota_type"],
            quota_remark=notice["quota_remark"],
            source_url=notice["source_url"],
            observed_at=product.observed_at,
            raw=raw,
        )

    def fetch_direct_sales_record(self, fund: FundIdentity) -> Optional[Dict[str, str]]:
        snapshot = self.fetch_direct_limit(fund)
        if snapshot.status == "official_api_no_data":
            return None
        return _record(
            snapshot.limit or "未获取",
            "长城基金官网最新限额公告 PDF",
            snapshot.source_url,
            snapshot.quota_remark,
            status=snapshot.status,
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public_with_frontend_request_boundary",
            reason="长城基金目录、产品页和直销机构信息公开；公告列表由官网前端加密请求加载，适配器不绕过前端加密、登录、验证码或交易提交认证。",
            requires_login=False,
        )


# 与现有 Adapter 命名保持一致，便于外部开源用户按公司英文名导入。
GreatWallAdapter = ChangchengAdapter
