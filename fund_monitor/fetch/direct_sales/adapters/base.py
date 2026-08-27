"""独立基金公司 Adapter 使用的最小数据契约。

这些类型只描述官网适配器的输出，不负责任何一家公司的字段解析。
公司差异应留在各自的 adapter 模块内，便于测试、替换和开源复用。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class FundIdentity:
    """官网基金目录中的一只基金/份额。"""

    manager_id: str
    code: str
    name: str
    fund_type: str = ""
    share_class: str = ""
    source_url: str = ""
    source_type: str = "official_fund_catalogue"


@dataclass(frozen=True)
class ProductSnapshot:
    """单只基金官网产品页快照。"""

    manager_id: str
    code: str
    name: str = ""
    full_name: str = ""
    fund_type: str = ""
    risk_level: str = ""
    inception_date: str = ""
    asset_scale: str = ""
    net_value_date: str = ""
    trade_status: str = ""
    source_url: str = ""
    observed_at: str = ""
    fields: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelTradeStatus:
    """交易状态 API 中的一条客户类型/销售渠道记录。"""

    customer_type: str
    channel: str
    subscription: Optional[bool] = None
    redemption: Optional[bool] = None
    transfer_in: Optional[bool] = None
    transfer_out: Optional[bool] = None
    sip: Optional[bool] = None
    limit: str = ""
    quota_type: str = ""
    quota_remark: str = ""
    subscription_open_remark: str = ""
    subscription_suspend_remark: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TradeSnapshot:
    """交易状态 API 的完整快照，保留所有渠道而不只取直销行。"""

    manager_id: str
    code: str
    channels: List[ChannelTradeStatus]
    api_status: Optional[int]
    message: str
    source_url: str
    observed_at: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DirectLimitSnapshot:
    """从某一销售渠道解析出的直销限额。"""

    manager_id: str
    code: str
    customer_type: str
    channel: str
    limit: Optional[str]
    status: str
    quota_type: str = ""
    quota_remark: str = ""
    source_url: str = ""
    observed_at: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthBoundary:
    """适配器观察到的认证边界。"""

    status: str
    reason: str
    requires_login: bool = False
    requires_captcha: bool = False
    requires_device_signature: bool = False
    requires_bank_card: bool = False
