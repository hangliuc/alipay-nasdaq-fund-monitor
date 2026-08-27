"""直销数据抓取编排器。"""

from .announcements import (
    _fetch_ccbfund_announcements,
    _fetch_ccfund_announcements,
    _fetch_dacheng_announcements,
    _fetch_ftsfund_announcements,
    _fetch_fullgoal_announcements,
    _fetch_gffunds_announcements,
    _fetch_huabao_announcements,
    _fetch_wanjia_announcements,
    _fetch_morgan_announcements,
    _fetch_igwfmc_announcements,
    _fetch_puyan_axa_announcements,
    _fetch_yinhu_announcements,
    _fetch_thfund_announcements,
    _fetch_valuetf_announcements,
    _fetch_cmfchina_announcements,
)
from .boundaries import (
    _probe_gf_trade_entry,
    _probe_official_trade_boundaries,
    _track_official_notice_entries,
)
from .product_pages import (
    _fetch_chinaamc,
    _fetch_efunds,
    _fetch_gtfund,
    _fetch_huaan,
    _fetch_huatai_pb,
    _fetch_jsfund,
    _fetch_southern,
    _fetch_bosera_public_product_pages,
    _probe_public_product_pages,
)
from .registry import adapter_for_fund
from .trade_api import _fetch_byfunds_trade_api


def fetch_official_limits(funds: list[dict]) -> list[dict]:
    """汇总各公司独立 Adapter 的当前结果。

    该编排器只负责调用和汇总，不提供统一/历史数据回退：某个 Adapter
    没有返回当前可验证的限额或状态时，结果保持为“未获取”。
    """
    records = {}
    records.update(_fetch_efunds(funds))
    records.update(_fetch_huaan(funds))
    records.update(_fetch_chinaamc(funds))
    records.update(_fetch_jsfund(funds))
    records.update(_fetch_gtfund(funds))
    records.update(_fetch_huatai_pb(funds))
    records.update(_fetch_fullgoal_announcements(funds))
    records.update(_fetch_ccbfund_announcements(funds))
    records.update(_fetch_ccfund_announcements(funds))
    records.update(_fetch_gffunds_announcements(funds))
    records.update(_fetch_ftsfund_announcements(funds))
    records.update(_fetch_byfunds_trade_api(funds))
    records.update(_fetch_bosera_public_product_pages(funds))
    records.update(_fetch_dacheng_announcements(funds))
    # 通用产品页探测只能补充空白基金，不能覆盖前面专用公告/API
    # Adapter 已经解析出的记录。
    records.update(_probe_public_product_pages(funds, set(records)))
    records.update(_fetch_thfund_announcements(funds))
    # 各公司独立 Adapter 是唯一可信直销来源；官方无数据时不回退历史公告。
    # 华宝独立 Adapter 动态读取官网当前产品/公告。
    records.update(_fetch_huabao_announcements(funds))
    # 万家、摩根、景顺长城独立 Adapter 优先于旧静态公告配置。
    records.update(_fetch_wanjia_announcements(funds))
    records.update(_fetch_morgan_announcements(funds))
    records.update(_fetch_igwfmc_announcements(funds))
    records.update(_fetch_puyan_axa_announcements(funds))
    records.update(_fetch_yinhu_announcements(funds))
    # 汇添富独立 Adapter 优先于旧通用公告规则，解析当前产品公告页。
    records.update(_fetch_valuetf_announcements(funds))
    # 招商独立 Adapter 优先于旧静态公告配置。
    records.update(_fetch_cmfchina_announcements(funds))
    # 南方官网交易状态 API 是当前公开状态来源，优先覆盖旧公告追踪值。
    records.update(_fetch_southern(funds))
    # 交易入口探测只用于尚未从公开公告解析出结果的基金，不能覆盖
    # 广发独立 Adapter 已经拿到的实时限额/公告结果。
    known_codes = {
        code for code, item in records.items()
        if item.get("direct_sales_status") == "ok"
    }
    records.update(_probe_gf_trade_entry(funds, known_codes))
    # 仅把已经解析出明确限额/状态的记录视为 known；对“产品页存在但无字段”
    # 的基金继续探测其未登录交易入口，避免错过“需认证”的边界证据。
    known_codes = {
        code for code, item in records.items()
        if item.get("direct_sales_status") == "ok"
    }
    records.update(_probe_official_trade_boundaries(funds, known_codes))
    tracked = _track_official_notice_entries(funds)
    out = []
    for fund in funds:
        record = dict(fund)
        adapter = adapter_for_fund(fund)
        record.update(records.get(fund["code"], {
            "direct_sales_limit": "未获取",
            "direct_sales_status": "official_source_not_available",
            "direct_sales_source": "",
            "direct_sales_url": "",
            "direct_sales_note": "独立 Adapter 未返回当前可验证限额；不使用统一兼容层、历史值或第三方数据回填",
            "direct_sales_manager": adapter.manager if adapter else "未匹配",
        }))
        record.update(tracked.get(fund["code"], {}))
        out.append(record)
    return out
