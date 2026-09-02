"""配置加载"""

import json
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    feishu_webhook: str = ""             # 主 webhook（兼容旧字段，通常由 env 注入）
    feishu_webhooks: list[str] = field(default_factory=list)  # 额外 webhook 列表
    passive_funds: list[dict] = field(default_factory=list)
    active_funds: list[dict] = field(default_factory=list)
    history_file: str = "data/history.json"
    image_base_url: str = ""
    market_distribution_enabled: bool = False
    market_distribution_year: int = 2026

    @property
    def all_webhooks(self) -> list[str]:
        """合并主 webhook 和额外 webhook 列表，去重并去掉空值"""
        seen = set()
        result = []
        for w in [self.feishu_webhook, *self.feishu_webhooks]:
            w = (w or "").strip()
            if w and w not in seen:
                seen.add(w)
                result.append(w)
        return result

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # 环境变量覆盖
        for env_key, conf_key in [
            ("FEISHU_WEBHOOK", "feishu_webhook"),
            ("IMAGE_BASE_URL", "image_base_url"),
        ]:
            val = os.environ.get(env_key)
            if val:
                raw[conf_key] = val

        return cls(
            feishu_webhook=raw.get("feishu_webhook", ""),
            feishu_webhooks=raw.get("feishu_webhooks", []),
            passive_funds=raw.get("passive_funds", []),
            active_funds=raw.get("active_funds", []),
            history_file=raw.get("history_file", "data/history.json"),
            image_base_url=raw.get("image_base_url", ""),
            market_distribution_enabled=raw.get("market_distribution_enabled", False),
            market_distribution_year=raw.get("market_distribution_year", 2026),
        )
