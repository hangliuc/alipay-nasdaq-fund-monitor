"""命令行入口"""

import argparse
import os
import sys
import uuid
from datetime import datetime

from fund_monitor.config import Config
from fund_monitor.fetch import fetch_all
from fund_monitor.storage import History
from fund_monitor.output import generate, FeishuNotifier
from fund_monitor.trading_day import is_trading_day

# 运行时产物与项目说明文档分离；该目录由 Docker/nginx 作为静态卡片目录挂载。
IMAGE_DIR = "data/cards"


def main():
    args = _parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 非交易日跳过（周末 + 中国法定节假日，含调休）
    if not args.force:
        ok, reason = is_trading_day()
        if not ok:
            print(f"📅 {reason}，支付宝非交易日，跳过日报生成和飞书推送。")
            print("   如需强制运行，使用 --force 参数。")
            return

    config_path = _resolve(root, args.config)
    try:
        config = Config.load(config_path)
    except FileNotFoundError:
        sys.exit(f"❌ 配置文件不存在: {config_path}")
    except Exception as e:
        sys.exit(f"❌ 配置文件错误: {e}")

    date_str = datetime.now().strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:6]
    image_dir = os.path.join(root, IMAGE_DIR)
    image_urls = []
    all_changes = []          # 收集所有变化
    all_health_warnings = []  # 收集数据源健康警告

    # ── 被动型基金 ──
    if config.passive_funds:
        funds = _filter(config.passive_funds, args.fund_codes)
        print(f"🚀 被动型基金：查询 {len(funds)} 只...")
        print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        history_path = _resolve(root, config.history_file)
        passive_history = History(history_path, namespace="passive").latest_snapshot()

        results = fetch_all(funds, history_latest=passive_history,
                            include_market_distribution=(args.market_distribution or config.market_distribution_enabled),
                            market_distribution_year=config.market_distribution_year)
        _print_health(results, "被动型")
        all_health_warnings.extend(_collect_health(results, "被动型"))

        changes = _record_history(args, root, config, results, "passive")
        all_changes.extend(changes)

        if not args.no_image:
            name = f"passive_{date_str}_{suffix}.png"
            generate(results, os.path.join(image_dir, name),
                     title="纳斯达克被动型基金限购日报",
                     subtitle_prefix="指数基金 · C类份额")
            if config.image_base_url:
                image_urls.append(f"{config.image_base_url}/{name}")

    # ── 主动型基金 ──
    if config.active_funds:
        funds = _filter(config.active_funds, args.fund_codes)
        print(f"\n🚀 主动型基金：查询 {len(funds)} 只...")
        print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        history_path = _resolve(root, config.history_file)
        active_history = History(history_path, namespace="active").latest_snapshot()

        results = fetch_all(funds, history_latest=active_history,
                            include_market_distribution=(args.market_distribution or config.market_distribution_enabled),
                            market_distribution_year=config.market_distribution_year)
        _print_health(results, "主动型")
        all_health_warnings.extend(_collect_health(results, "主动型"))

        changes = _record_history(args, root, config, results, "active")
        all_changes.extend(changes)

        if not args.no_image:
            name = f"active_{date_str}_{suffix}.png"
            generate(results, os.path.join(image_dir, name),
                     title="纳斯达克主动型基金限购日报",
                     subtitle_prefix="主动管理 QDII")
            if config.image_base_url:
                image_urls.append(f"{config.image_base_url}/{name}")

    # ── 飞书通知 ──
    if not args.no_notify:
        webhooks = [] if args.dry_run else config.all_webhooks
        changes_text = History.format_changes_for_feishu(all_changes)
        health_text = _format_health_for_feishu(all_health_warnings)
        FeishuNotifier(webhooks).send_reminder(image_urls, changes_text, health_text)

    print("\n✅ 完成！")


def _filter(funds, codes):
    if not codes:
        return funds
    return [f for f in funds if f["code"] in codes]


def _record_history(args, root, config, results, prefix):
    """记录历史并返回变化列表"""
    if args.no_history:
        return []
    path = _resolve(root, config.history_file)
    history = History(path, namespace=prefix)
    changes = history.update(results)
    text = History.format_changes(changes)
    if text:
        print(text)
    errors = sum(1 for r in results if r.get("error"))
    if errors:
        print(f"  ⚠️ {errors} 只基金查询失败")
    return changes


def _print_health(results: list[dict], group_label: str):
    """控制台打印数据源健康状况"""
    by_src = {"jjjz": 0, "html": 0, "stale": 0, "none": 0}
    warnings = []
    for r in results:
        by_src[r.get("source", "none")] = by_src.get(r.get("source", "none"), 0) + 1
        for w in r.get("warnings", []) or []:
            warnings.append(f"  · {r.get('name', r['code'])}({r['code']}): {w}")
    print(f"\n  📊 {group_label} 数据源：主源 jjjz={by_src['jjjz']} | "
          f"备源 html={by_src['html']} | 历史兜底 stale={by_src['stale']} | "
          f"失败 none={by_src['none']}")
    if warnings:
        print(f"  ⚠️ {group_label} 数据警告：")
        for w in warnings:
            print(w)


def _collect_health(results: list[dict], group_label: str) -> list[dict]:
    """提取需要告警的条目（用于飞书通知）"""
    out = []
    for r in results:
        src = r.get("source", "none")
        if src in ("stale", "none") or r.get("warnings"):
            out.append({
                "group": group_label,
                "code": r["code"],
                "name": r.get("display") or r.get("name", ""),
                "source": src,
                "confidence": r.get("confidence", "low"),
                "warnings": r.get("warnings") or [],
                "error": r.get("error"),
            })
    return out


def _format_health_for_feishu(items: list[dict]):
    """格式化数据源健康警告，用于飞书消息底部追加。"""
    if not items:
        return None
    lines = ["⚠️ **数据源健康提醒**：", ""]
    for it in items:
        tag = {"stale": "📦 历史兜底", "none": "❌ 抓取失败",
               "html": "🔁 备源", "jjjz": "✅ 主源"}.get(it["source"], it["source"])
        lines.append(f"- [{it['group']}] {it['name']}({it['code']}) {tag}")
        for w in it["warnings"]:
            lines.append(f"    · {w}")
        if it.get("error") and not it["warnings"]:
            lines.append(f"    · {it['error']}")
    return "\n".join(lines)


def _parse_args():
    p = argparse.ArgumentParser(description="支付宝纳斯达克 QDII 基金限购监控")
    p.add_argument("--config", default="config.json", help="配置文件路径")
    p.add_argument("--fund-codes", nargs="+", help="只查询指定基金代码")
    p.add_argument("--dry-run", action="store_true", help="不推送飞书")
    p.add_argument("--no-history", action="store_true", help="不记录历史")
    p.add_argument("--no-image", action="store_true", help="不生成图片")
    p.add_argument("--no-notify", action="store_true", help="不发送通知")
    p.add_argument("--force", action="store_true", help="强制运行（忽略节假日/周末检查）")
    p.add_argument("--market-distribution", action="store_true",
                   help="抓取证监会季报中的国家/地区市场投资分布")
    return p.parse_args()


def _resolve(root, path):
    return path if os.path.isabs(path) else os.path.join(root, path)
