"""官方公告追踪与未登录认证边界探测。"""

import requests
from typing import Optional

from .common import _record
from .registry import HEADERS, OFFICIAL_NOTICE_TRACKING_URLS, PDF_ANNOUNCEMENT_FEEDS, adapter_for_fund


def _track_official_notice_entries(funds: list[dict]) -> dict[str, dict]:
    """为所有未命中基金执行官网公告入口追踪并保留可审计状态。"""
    tracked = {}
    feed_managers = {feed.manager for feed in PDF_ANNOUNCEMENT_FEEDS}
    for fund in funds:
        adapter = adapter_for_fund(fund)
        if not adapter:
            continue
        url = OFFICIAL_NOTICE_TRACKING_URLS.get(adapter.manager)
        if not url:
            continue
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            status = "active_pdf_feed" if adapter.manager in feed_managers else "official_notice_entry_tracked"
            note = "官网公告入口已纳入自动追踪；该管理人已有 PDF 限额解析器。" if adapter.manager in feed_managers else "官网公告入口已纳入自动追踪，等待该管理人的专用公告/PDF字段解析器。"
        except requests.RequestException:
            status = "official_notice_entry_network_retry"
            note = "官网公告入口已配置，当前访问失败，下一次运行自动重试。"
        tracked[fund["code"]] = {
            "direct_sales_tracking_status": status,
            "direct_sales_tracking_url": url,
            "direct_sales_tracking_note": note,
        }
    return tracked


def _probe_gf_trade_entry(funds: list[dict], known_codes: Optional[set[str]] = None) -> dict[str, dict]:
    """识别广发官网的直销交易入口及其认证边界，不尝试绕过验证码。"""
    known_codes = known_codes or set()
    records = {}
    for fund in funds:
        if adapter_for_fund(fund).manager != "广发":
            continue
        if fund["code"] in known_codes:
            continue
        product_url = f"https://www.gffunds.com.cn/funds/?fundcode={fund['code']}"
        trade_url = f"https://trade.gffunds.com.cn/fund/all-fund/buy?fundCode={fund['code']}"
        try:
            page = requests.get(product_url, headers=HEADERS, timeout=15).text
        except requests.RequestException:
            continue
        if trade_url not in page:
            continue
        records[fund["code"]] = _record(
            "未获取",
            "广发基金官网直销交易入口",
            trade_url,
            "官网产品页提供直销购买入口；交易页使用验证码，未登录公开页面未提供可验证的限额字段。",
            status="official_interface_requires_auth",
        )
    return records


def _probe_official_trade_boundaries(funds: list[dict], known_codes: set[str]) -> dict[str, dict]:
    """探测其他官网直销入口的认证边界，不提交交易、不绕过登录。"""
    templates = {
        "博时": "https://trade.bosera.com/indi/api/www/buyfundurl.do?fundCode={code}",
        "华宝": "https://e.fsfund.com/etrading/trade/buyFund/{code}/0;",
        "国富": "https://e-trade.ftsfund.com/webapp/fromSite.do?fundcode={code}&business=022&source=100",
        "银华": "https://trade.yhfund.com.cn/yhxntrade/account/goLogin.do",
    }
    records = {}
    for fund in funds:
        if fund["code"] in known_codes:
            continue
        adapter = adapter_for_fund(fund)
        if not adapter or adapter.manager not in templates:
            continue
        url = templates[adapter.manager].format(code=fund["code"])
        try:
            # 只观察首个响应的 Location，避免跟随国富交易站点的 HTTP 登录
            # 跳转导致网络错误；302 本身已经是“需认证”的官方证据。
            response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=False)
            location = response.headers.get("location", "")
            text = response.text[:20000]
            # 博时首跳是交易表单页，页面本身再返回登录标题；只跟随这一跳
            # 读取公开 HTML，不提交任何表单或交易请求。
            if location and not any(token in location.lower() for token in ("login", "登录", "auth")):
                next_url = requests.compat.urljoin(url, location)
                follow = requests.get(next_url, headers=HEADERS, timeout=15, allow_redirects=False)
                text += "\n" + follow.text[:20000]
        except requests.RequestException:
            continue
        if (
            response.status_code in (301, 302, 303, 307, 308)
            and ("login" in location.lower() or "登录" in location or "auth" in location.lower())
        ) or (
            "login" in location.lower()
            or "登录" in location
            or "登录" in text
            or "验证码" in text
            or "会话已过期" in text
            or "authorized" in text.lower()
        ):
            records[fund["code"]] = _record(
                "未获取",
                f"{adapter.manager}基金官网直销交易入口",
                url,
                "未登录交易入口自动跳转登录页/出现认证控件；未绕过登录、验证码或设备校验。",
                status="official_interface_requires_auth",
            )
    return records

