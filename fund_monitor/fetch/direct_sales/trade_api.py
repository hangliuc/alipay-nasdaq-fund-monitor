"""旧版基金公司交易 API 兼容入口。

宝盈的具体实现位于 ``adapters.baoying.BaoyingAdapter``；本模块保留旧函数名，
避免现有日报编排器和外部调用方发生破坏性变更。
"""

import requests

from .adapters.baoying import BaoyingAdapter
from .common import _record, _snapshot_record
from .registry import adapter_for_fund


def _baoying_sign(params: dict) -> str:
    """旧兼容入口：按宝盈官网直销前端公开规则生成 ``_sign``。"""
    return BaoyingAdapter.sign(params)


def _baoying_get(session: requests.Session, path: str, params: dict, token: str = "") -> dict:
    """旧兼容入口：调用宝盈官网公开 API，不绕过认证。"""
    return BaoyingAdapter(session=session)._get(path, params, token)


def _fetch_byfunds_trade_api(funds: list[dict]) -> dict[str, dict]:
    """从宝盈官网网上交易公开基金详情接口读取申购上限。"""
    records = {}
    adapter = BaoyingAdapter()
    for fund in funds:
        manager_adapter = adapter_for_fund(fund)
        if not manager_adapter or manager_adapter.manager != "宝盈":
            continue
        endpoint = f"{BaoyingAdapter.api_base_url}{BaoyingAdapter.detail_path}?fundcode={fund['code']}"
        try:
            identity = BaoyingAdapter.identity_from_config(fund)
            snapshot = adapter.fetch_direct_limit(identity)
        except PermissionError:
            records[fund["code"]] = _record(
                "未获取",
                "宝盈官网网上交易 API",
                endpoint,
                "官网公开 session/详情接口需要认证或会话已失效；未绕过认证。",
                status="official_interface_requires_auth",
            )
            continue
        except (requests.RequestException, RuntimeError, ValueError, TypeError, KeyError, IndexError):
            continue
        records[fund["code"]] = _snapshot_record(snapshot)
    return records
