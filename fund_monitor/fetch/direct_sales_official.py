"""直销官网数据的稳定导入入口（不提供数据回退）。

实现位于 :mod:`fund_monitor.fetch.direct_sales`。此模块只保留面向调用方的
稳定 API；解析器的内部辅助函数不再通过这里泄漏为公共依赖。适配器失败时
由编排器保留“未获取”，不会从统一兼容层或历史静态映射补值。
"""

from .direct_sales import (
    OFFICIAL_ADAPTERS,
    OFFICIAL_NOTICE_TRACKING_URLS,
    OfficialAdapter,
    PdfAnnouncementFeed,
    adapter_for_fund,
    coverage_rows,
    fetch_official_limits,
    GuangfaAdapter,
    JiashiAdapter,
    ValuetfAdapter,
    CmfchinaAdapter,
    HuabaoAdapter,
    JianxinAdapter,
    ChangchengAdapter,
    WanjiaAdapter,
    MorganAdapter,
    IgwfmcAdapter,
    PuyanAxaAdapter,
    YinhuAdapter,
)

__all__ = [
    "OFFICIAL_ADAPTERS",
    "OFFICIAL_NOTICE_TRACKING_URLS",
    "OfficialAdapter",
    "PdfAnnouncementFeed",
    "adapter_for_fund",
    "coverage_rows",
    "fetch_official_limits",
    "GuangfaAdapter",
    "JiashiAdapter",
    "ValuetfAdapter",
    "CmfchinaAdapter",
    "HuabaoAdapter",
    "JianxinAdapter",
    "ChangchengAdapter",
    "WanjiaAdapter",
    "MorganAdapter",
    "IgwfmcAdapter",
    "PuyanAxaAdapter",
    "YinhuAdapter",
]
