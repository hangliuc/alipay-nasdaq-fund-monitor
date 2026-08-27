"""易方达基金官网独立 Adapter。

数据层次：

* 基金超市 ``vip.efunds.com.cn`` 的 ``#allFundTable``：发现官网公开目录；
* ``www.efunds.com.cn/fund/{code}.shtml``：产品字段和产品页交易提醒；
* ``api.efunds.com.cn/xcowch/front/fund/tradestatus/{code}``：当前交易状态，
  按个人/机构和网上直销/直销中心/非直销机构分别返回。

本模块不访问登录页、不提交交易，也不把空 API 结果解释为“不限”。
"""

from datetime import date, datetime, timezone
import re
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

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


class EFundsAdapter:
    """易方达官网公开基金目录、产品页和交易状态 API 适配器。"""

    manager_id = "易方达"
    catalogue_url = "https://vip.efunds.com.cn/"
    product_url_template = "https://www.efunds.com.cn/fund/{code}.shtml"
    trade_status_url_template = (
        "https://api.efunds.com.cn/xcowch/front/fund/tradestatus/{code}"
    )
    source_type_catalogue = "efunds_official_fund_catalogue"
    source_type_product = "efunds_official_product_page"
    source_type_trade_api = "efunds_official_trade_status_api"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 15,
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

    @staticmethod
    def _https_url(url: str) -> str:
        """把官网旧表格中的 http 链接升级为同域 HTTPS。"""
        parts = urlsplit(url.strip())
        if not parts.netloc:
            return url
        return urlunsplit(("https", parts.netloc, parts.path, parts.query, parts.fragment))

    @staticmethod
    def _share_class(name: str) -> str:
        match = re.search(r"([A-Z])$", name.strip())
        return match.group(1) if match else ""

    @classmethod
    def parse_catalogue_html(cls, html: bytes) -> List[FundIdentity]:
        """解析易方达基金超市的公开目录表。

        只接受 ``#allFundTable`` 中含六位基金代码的行；推荐基金区、ETF
        申赎清单区和不含基金代码的表格不会被误当成产品目录。
        """
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", id="allFundTable")
        if table is None:
            return []

        found: Dict[str, FundIdentity] = {}
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            values = [cell.get_text(" ", strip=True) for cell in cells]
            code_match = re.search(r"\b(\d{6})\b", values[2])
            if not code_match:
                continue
            code = code_match.group(1)
            name_anchor = cells[0].find("a")
            name = name_anchor.get_text(" ", strip=True) if name_anchor else values[0]
            name = re.sub(r"\s*\[[^]]+\]", "", name).strip()
            href = name_anchor.get("href") if name_anchor else ""
            source_url = cls._https_url(urljoin("https://vip.efunds.com.cn/", href or ""))
            if not source_url or source_url == "https://vip.efunds.com.cn/":
                source_url = cls.product_url_template.format(code=code)
            found.setdefault(
                code,
                FundIdentity(
                    manager_id=cls.manager_id,
                    code=code,
                    name=name,
                    fund_type=values[1],
                    share_class=cls._share_class(name),
                    source_url=source_url,
                    source_type=cls.source_type_catalogue,
                ),
            )
        return sorted(found.values(), key=lambda item: item.code)

    def discover_funds(self) -> List[FundIdentity]:
        """从易方达官网基金超市发现当前公开目录中的基金。"""
        response = self.session.get(
            self.catalogue_url,
            headers=HEADERS,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self.parse_catalogue_html(response.content)

    @classmethod
    def identity_from_config(cls, fund: Dict[str, Any]) -> FundIdentity:
        """把现有 ``config.json`` 基金转换为 Adapter 输入。

        该方法只用于兼容当前日报；全基金流程应优先使用
        :meth:`discover_funds` 的官方目录结果。
        """
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

    @staticmethod
    def _label(text: str, label: str, labels: Iterable[str]) -> str:
        stop = "|".join(re.escape(item) for item in labels if item != label)
        pattern = rf"{re.escape(label)}\s*[:：]\s*(.*?)(?=\s+(?:{stop})\s*[:：]|$)"
        match = re.search(pattern, text)
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""

    @staticmethod
    def _normalize_limit(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text in {"-", "不限", "无限", "无"}:
            return None
        parsed = _amount(text)
        if parsed:
            return parsed
        # 少数前端版本直接返回“100000”而不带单位；API 字段单位为人民币元。
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return _amount(f"{text}元")
        return None

    @classmethod
    def parse_product_html(
        cls,
        html: bytes,
        code: str,
        source_url: str,
        observed_at: str,
    ) -> ProductSnapshot:
        """解析易方达产品页的基本信息和公开交易提醒。"""
        text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        labels = (
            "基金名称",
            "基金简称",
            "场内简称",
            "基金代码",
            "上市场所",
            "基金类型",
            "成立日期",
            "基金管理人",
            "基金经理",
            "基金托管人",
            "基金规模",
            "基金净值日期",
        )
        fields = {
            label: cls._label(text, label, labels)
            for label in labels
            if cls._label(text, label, labels)
        }
        full_name = fields.get("基金名称", "")
        short_name = fields.get("基金简称", "") or full_name
        risk_match = re.search(r"(?:低|中低|中|中高|高)风险\s*\(R\d\)", text)
        inception = re.search(r"成立日期\s*[：:]\s*(\d{4}-\d{2}-\d{2})", text)
        nav_date = re.search(r"基金净值日期\s*[：:]\s*(\d{4}-\d{2}-\d{2})", text)
        scale = re.search(r"资产规模\s*[：:]\s*([\d,.]+\s*亿元)", text)
        if not scale:
            scale = re.search(r"基金规模\s*[：:]\s*数据截至[^：:]*[：:]\s*([\d,.]+\s*元)", text)

        if "暂停申购" in text:
            trade_status = "暂停申购"
        elif "开放申购" in text:
            trade_status = "开放申购"
        else:
            trade_status = "未知"

        return ProductSnapshot(
            manager_id=cls.manager_id,
            code=code,
            name=short_name,
            full_name=full_name,
            fund_type=fields.get("基金类型", ""),
            risk_level=risk_match.group(0) if risk_match else "",
            inception_date=inception.group(1) if inception else "",
            asset_scale=scale.group(1) if scale else "",
            net_value_date=nav_date.group(1) if nav_date else "",
            trade_status=trade_status,
            source_url=source_url,
            observed_at=observed_at,
            fields=fields,
        )

    def fetch_product(self, fund: FundIdentity) -> ProductSnapshot:
        """读取一只基金的官网产品页。"""
        url = self.product_url_template.format(code=fund.code)
        response = self.session.get(url, headers=HEADERS, timeout=self.timeout)
        response.raise_for_status()
        return self.parse_product_html(response.content, fund.code, url, self._observed_at())

    @classmethod
    def parse_trade_payload(
        cls,
        payload: Dict[str, Any],
        code: str,
        source_url: str,
        observed_at: str,
    ) -> TradeSnapshot:
        data = payload.get("data") or {}
        channels: List[ChannelTradeStatus] = []
        for customer_type, rows in (("individual", data.get("individual")), ("organization", data.get("organization"))):
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                channels.append(
                    ChannelTradeStatus(
                        customer_type=customer_type,
                        channel=str(row.get("agencyName") or ""),
                        subscription=row.get("subscription"),
                        redemption=row.get("redemption"),
                        transfer_in=row.get("transferIn"),
                        transfer_out=row.get("transferOut"),
                        sip=row.get("sip"),
                        limit=str(row.get("limit") or ""),
                        quota_type=str(row.get("quotaType") or ""),
                        quota_remark=str(row.get("quotaRemark") or ""),
                        subscription_open_remark=str(row.get("subscriptionOpenRemark") or ""),
                        subscription_suspend_remark=str(row.get("subscriptionSuspendRemark") or ""),
                        raw=dict(row),
                    )
                )
        return TradeSnapshot(
            manager_id=cls.manager_id,
            code=code,
            channels=channels,
            api_status=payload.get("status"),
            message=str(payload.get("message") or ""),
            source_url=source_url,
            observed_at=observed_at,
            raw=payload,
        )

    def fetch_trade_status(self, fund: FundIdentity, trade_date: Optional[date] = None) -> TradeSnapshot:
        """读取个人/机构全部渠道的当前交易状态。"""
        url = self.trade_status_url_template.format(code=fund.code)
        request_date = trade_date or self.clock().date()
        response = self.session.get(
            url,
            params={"date": request_date.isoformat()},
            headers=HEADERS,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self.parse_trade_payload(response.json(), fund.code, url, self._observed_at())

    def fetch_direct_limit(
        self,
        fund: FundIdentity,
        trade_date: Optional[date] = None,
    ) -> DirectLimitSnapshot:
        """从个人客户“网上直销”行解析当前直销申购限额。"""
        snapshot = self.fetch_trade_status(fund, trade_date=trade_date)
        direct = next(
            (
                channel
                for channel in snapshot.channels
                if channel.customer_type == "individual" and channel.channel == "网上直销"
            ),
            None,
        )
        if direct is None:
            return DirectLimitSnapshot(
                manager_id=self.manager_id,
                code=fund.code,
                customer_type="individual",
                channel="网上直销",
                limit=None,
                status="official_api_no_data",
                source_url=snapshot.source_url,
                observed_at=snapshot.observed_at,
                raw=snapshot.raw,
            )
        if direct.subscription is False:
            limit = "暂停"
        else:
            limit = self._normalize_limit(direct.limit) or self._normalize_limit(direct.quota_remark) or "不限"
        return DirectLimitSnapshot(
            manager_id=self.manager_id,
            code=fund.code,
            customer_type=direct.customer_type,
            channel=direct.channel,
            limit=limit,
            status="ok",
            quota_type=direct.quota_type,
            quota_remark=direct.quota_remark,
            source_url=snapshot.source_url,
            observed_at=snapshot.observed_at,
            raw=direct.raw,
        )

    def fetch_direct_sales_record(
        self,
        fund: FundIdentity,
        trade_date: Optional[date] = None,
    ) -> Optional[Dict[str, str]]:
        """生成现有日报编排器兼容的直销记录。"""
        snapshot = self.fetch_direct_limit(fund, trade_date=trade_date)
        if snapshot.status != "ok":
            return None
        note = "明确渠道：个人客户/网上直销；结果来自易方达官网交易状态 API。"
        if snapshot.quota_type or snapshot.quota_remark:
            note += f" quotaType={snapshot.quota_type or '-'}；quotaRemark={snapshot.quota_remark or '-'}。"
        return _record(
            snapshot.limit or "未获取",
            "易方达官网交易状态 API",
            snapshot.source_url,
            note,
        )

    def auth_boundary(self) -> AuthBoundary:
        return AuthBoundary(
            status="public",
            reason="当前公开交易状态 API 可返回个人/机构及各销售渠道状态；未观察到登录、验证码或设备签名要求。",
        )
