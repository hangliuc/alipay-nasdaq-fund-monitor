"""按基金管理人拆分的独立官网 Adapter。"""

from .base import (
    AuthBoundary,
    ChannelTradeStatus,
    DirectLimitSnapshot,
    FundIdentity,
    ProductSnapshot,
    TradeSnapshot,
)
from .efunds import EFundsAdapter
from .baoying import BaoyingAdapter
from .bosera import BoseraAdapter
from .chinaamc import ChinaAMCAdapter
from .cmfchina import CmfchinaAdapter, ChinaMerchantsAdapter
from .dacheng import DachengAdapter
from .guotai import GuotaiAdapter
from .guofu import GuofuAdapter
from .fullgoal import FullgoalAdapter
from .guangfa import GuangfaAdapter
from .jiashi import JiashiAdapter
from .huaan import HuaanAdapter
from .huatai_pb import HuataiPBAdapter
from .huabao import HuabaoAdapter
from .jianxin import JianxinAdapter
from .changcheng import ChangchengAdapter
from .wanjia import WanjiaAdapter
from .morgan import MorganAdapter
from .igwfmc import IgwfmcAdapter, IGWFMCAdapter
from .southern import SouthernAdapter
from .tianhong import TianhongAdapter
from .valuetf import ValuetfAdapter
from .puyan_axa import PuyanAxaAdapter, PuyanAXAAdapter
from .yinhu import YinhuAdapter, YHFundAdapter

__all__ = [
    "AuthBoundary",
    "ChannelTradeStatus",
    "DirectLimitSnapshot",
    "FundIdentity",
    "ProductSnapshot",
    "TradeSnapshot",
    "EFundsAdapter",
    "BaoyingAdapter",
    "BoseraAdapter",
    "ChinaAMCAdapter",
    "CmfchinaAdapter",
    "ChinaMerchantsAdapter",
    "DachengAdapter",
    "GuotaiAdapter",
    "GuofuAdapter",
    "FullgoalAdapter",
    "GuangfaAdapter",
    "JiashiAdapter",
    "HuaanAdapter",
    "HuataiPBAdapter",
    "HuabaoAdapter",
    "JianxinAdapter",
    "ChangchengAdapter",
    "WanjiaAdapter",
    "MorganAdapter",
    "IgwfmcAdapter",
    "IGWFMCAdapter",
    "SouthernAdapter",
    "TianhongAdapter",
    "ValuetfAdapter",
    "PuyanAxaAdapter",
    "PuyanAXAAdapter",
    "YinhuAdapter",
    "YHFundAdapter",
]
