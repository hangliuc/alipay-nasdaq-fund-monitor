"""各基金公司独立 Adapter 的公告/限额编排入口。"""

from .common import _fetch_adapter_limits
from .adapters.cmfchina import CmfchinaAdapter
from .adapters.changcheng import ChangchengAdapter
from .adapters.dacheng import DachengAdapter
from .adapters.fullgoal import FullgoalAdapter
from .adapters.guangfa import GuangfaAdapter
from .adapters.guofu import GuofuAdapter
from .adapters.huabao import HuabaoAdapter
from .adapters.igwfmc import IgwfmcAdapter
from .adapters.jianxin import JianxinAdapter
from .adapters.morgan import MorganAdapter
from .adapters.puyan_axa import PuyanAxaAdapter
from .adapters.tianhong import TianhongAdapter
from .adapters.valuetf import ValuetfAdapter
from .adapters.wanjia import WanjiaAdapter
from .adapters.yinhu import YinhuAdapter


THFUND_ANNOUNCEMENT_URLS = {
    "016665": "https://cdn-thweb.tianhongjijin.com.cn/fundnotice/7884d8fa6b0bf00ed0e6106ab693b556.pdf",
    "018044": "https://cdn-thweb.tianhongjijin.com.cn/fundnotice/574b5473d3485eeca8990102f6098ebd.pdf",
}


def _fetch_fullgoal_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过富国独立 Adapter 读取当前公告限额。"""
    return _fetch_adapter_limits(funds, FullgoalAdapter())


def _fetch_ccbfund_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过建信独立 Adapter 读取当前公告限额。"""
    return _fetch_adapter_limits(funds, JianxinAdapter())


def _fetch_ccfund_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过长城独立 Adapter 读取当前公告限额。"""
    return _fetch_adapter_limits(funds, ChangchengAdapter())


def _fetch_huabao_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过华宝独立 Adapter 读取当前产品和公告限额。"""
    return _fetch_adapter_limits(funds, HuabaoAdapter())


def _fetch_wanjia_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过万家独立 Adapter 读取当前产品和公告限额。"""
    return _fetch_adapter_limits(funds, WanjiaAdapter())


def _fetch_morgan_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过摩根独立 Adapter 读取当前公告限额。"""
    return _fetch_adapter_limits(funds, MorganAdapter())


def _fetch_igwfmc_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过景顺长城独立 Adapter 读取当前公告限额。"""
    return _fetch_adapter_limits(funds, IgwfmcAdapter())


def _fetch_puyan_axa_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过浦银安盛独立 Adapter 读取当前公告限额。"""
    return _fetch_adapter_limits(funds, PuyanAxaAdapter())


def _fetch_yinhu_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过银华独立 Adapter 读取当前产品和认证边界。"""
    return _fetch_adapter_limits(funds, YinhuAdapter())


def _fetch_gffunds_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过广发独立 Adapter 读取当前限额 API/公告。"""
    return _fetch_adapter_limits(funds, GuangfaAdapter())


def _fetch_valuetf_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过汇添富独立 Adapter 读取当前公告限额。"""
    return _fetch_adapter_limits(funds, ValuetfAdapter())


def _fetch_cmfchina_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过招商独立 Adapter 读取当前公告限额。"""
    return _fetch_adapter_limits(funds, CmfchinaAdapter())


def _fetch_ftsfund_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过国富独立 Adapter 读取当前信息披露公告。"""
    return _fetch_adapter_limits(funds, GuofuAdapter())


def _fetch_thfund_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过天弘独立 Adapter 读取产品公告 CDN PDF。"""
    return _fetch_adapter_limits(
        funds, TianhongAdapter(notice_urls=THFUND_ANNOUNCEMENT_URLS)
    )


def _fetch_dacheng_announcements(funds: list[dict]) -> dict[str, dict]:
    """通过大成独立 Adapter 读取公告列表和 PDF。"""
    return _fetch_adapter_limits(funds, DachengAdapter())
