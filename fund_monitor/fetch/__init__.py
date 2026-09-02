"""
数据抓取层

对外暴露主入口 fetch_all：内部走"主源 JJJZ + 备源 HTML + 历史快照兜底 + 交叉验证"策略，
详见 aggregator.py 与 sources/ 子模块。
"""

from typing import Optional

from fund_monitor.fetch.aggregator import aggregate


def fetch_all(fund_list: list[dict], history_latest: Optional[dict] = None,
              include_market_distribution: bool = False,
              market_distribution_year: int = 2026) -> list[dict]:
    """
    批量抓取基金信息。

    Args:
        fund_list:       [{"code": "008971", "name": "...", "display": "..."}, ...]
        history_latest:  history.json 中对应 namespace 的 latest 快照，
                         主备源均失败时用作兜底（不传则失败时直接报错）

    Returns:
        每条结果包含：
          - code/name/purchase_status/purchase_limit/return_1y/error（兼容旧字段）
          - source/confidence/warnings（数据源元信息，新增）
    """
    return aggregate(fund_list, history_latest=history_latest,
                     include_market_distribution=include_market_distribution,
                     market_distribution_year=market_distribution_year)


__all__ = ["fetch_all", "aggregate"]
