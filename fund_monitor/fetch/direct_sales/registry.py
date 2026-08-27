"""基金公司 Adapter 注册表与覆盖审计。"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OfficialAdapter:
    """单家基金公司的官方数据源配置。"""

    manager: str
    domains: tuple[str, ...]
    public_channels: tuple[str, ...]
    token_env: Optional[str] = None
    notes: str = ""


@dataclass(frozen=True)
class PdfAnnouncementFeed:
    """基金公司官网公告列表的 PDF 解析配置。"""

    manager: str
    list_url_template: str
    title_keyword: str = "大额申购"
    amount_pattern: str = r"不应超过\s*([\d,.]+)\s*人民币元"


# 仅列基金公司自有域名；第三方域名不允许加入本注册表。
OFFICIAL_ADAPTERS: tuple[OfficialAdapter, ...] = (
    OfficialAdapter("大成", ("dcfund.com.cn",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("国泰", ("gtfund.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("广发", ("gffunds.com.cn",), ("官网产品页", "官网信息披露", "App公开接口")),
    OfficialAdapter("华安", ("huaan.com.cn",), ("官网产品页", "官网限额表")),
    OfficialAdapter("易方达", ("efunds.com.cn",), ("官网交易状态API", "官网交易页")),
    OfficialAdapter("华夏", ("chinaamc.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("南方", ("southernfund.com", "nffund.com"), ("官网产品页", "官网产品状态页")),
    OfficialAdapter("天弘", ("thfund.com.cn",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("嘉实", ("jsfund.cn",), ("官网限额表", "官网产品页")),
    OfficialAdapter("博时", ("bosera.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("招商", ("cmfchina.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("华泰柏瑞", ("huatai-pb.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("摩根", ("cifm.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("汇添富", ("99fund.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("建信", ("ccbfund.cn",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("宝盈", ("byfunds.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("万家", ("wjasset.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("华宝", ("fsfund.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("浦银安盛", ("py-axa.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("国富", ("ftsfund.com",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("银华", ("yhfund.com.cn",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("长城", ("ccfund.com.cn",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("富国", ("fullgoal.com.cn",), ("官网产品页", "官网信息披露")),
    OfficialAdapter("景顺长城", ("igwfmc.com",), ("官网产品页", "官网信息披露")),
)

# 已接入 ``fetch_official_limits`` 且能够产出直销额度/状态的管理人。注册表中
# 其余公司仅用于官网入口追踪，不能据此将其加入直销额度卡片。
DIRECT_SALES_MANAGERS = frozenset({
    "大成", "国泰", "广发", "华安", "易方达", "华夏", "南方", "天弘", "嘉实",
    "博时", "华泰柏瑞", "建信", "华宝", "宝盈", "富国", "国富", "长城", "汇添富", "招商",
    "摩根", "万家", "景顺长城", "浦银安盛", "银华",
})

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

# 后续管理人只要补充官网公告列表 URL 和必要的金额正则，即复用同一解析器。
PDF_ANNOUNCEMENT_FEEDS = (
    PdfAnnouncementFeed("汇添富", "https://www.99fund.com/main/products/pofund/{code}/fundgg.shtml"),
)

# 所有管理人的官方公告入口。没有专用 PDF 解析器时，仍会在每次运行时
# 访问该入口并记录追踪状态，避免未获取基金被遗漏。
OFFICIAL_NOTICE_TRACKING_URLS = {
    "大成": "https://www.dcfund.com.cn/",
    "国泰": "https://www.gtfund.com/",
    "广发": "https://www.gffunds.com.cn/",
    "华安": "https://www.huaan.com.cn/",
    "易方达": "https://www.efunds.com.cn/",
    "华夏": "https://www.chinaamc.com/",
    "南方": "https://www.southernfund.com/",
    "天弘": "https://www.thfund.com.cn/",
    "嘉实": "https://www.jsfund.cn/",
    "博时": "https://www.bosera.com/",
    "招商": "https://www.cmfchina.com/",
    "华泰柏瑞": "https://www.huatai-pb.com/",
    "摩根": "https://www.cifm.com/",
    "汇添富": "https://www.99fund.com/",
    "建信": "https://www.ccbfund.cn/",
    "宝盈": "https://www.byfunds.com/",
    "万家": "https://www.wjasset.com/",
    "华宝": "https://www.fsfund.com/",
    "浦银安盛": "https://www.py-axa.com/",
    "国富": "https://www.ftsfund.com/",
    "银华": "https://www.yhfund.com.cn/",
    "长城": "https://www.ccfund.com.cn/",
    "富国": "https://www.fullgoal.com.cn/",
    "景顺长城": "https://www.igwfmc.com/",
}


def adapter_for_fund(fund: dict) -> Optional[OfficialAdapter]:
    """根据配置中的基金名称匹配所属管理人 Adapter。"""
    text = f"{fund.get('name', '')} {fund.get('display', '')}"
    for adapter in sorted(OFFICIAL_ADAPTERS, key=lambda item: len(item.manager), reverse=True):
        if adapter.manager in text:
            return adapter
    return None


def has_direct_sales_support(fund: dict) -> bool:
    """该基金所属公司是否已接入可抓取的直销额度能力。"""
    adapter = adapter_for_fund(fund)
    return bool(adapter and adapter.manager in DIRECT_SALES_MANAGERS)


def coverage_rows(funds: list[dict]) -> list[dict]:
    """生成不发请求的 Adapter 覆盖清单，用于审计报告。"""
    rows = []
    for fund in funds:
        adapter = adapter_for_fund(fund)
        rows.append({
            "code": fund["code"],
            "name": fund.get("name", ""),
            "manager": adapter.manager if adapter else "未匹配",
            "domains": list(adapter.domains) if adapter else [],
            "channels": list(adapter.public_channels) if adapter else [],
        })
    return rows
