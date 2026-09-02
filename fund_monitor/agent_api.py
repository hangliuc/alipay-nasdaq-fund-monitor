"""Stable, read-only JSON query interface for other agents."""
from __future__ import annotations
import contextlib, io, os, re
from datetime import datetime
from fund_monitor.config import Config
from fund_monitor.fetch import fetch_all
from fund_monitor.storage import History

def _number(value):
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(m.group()) if m else None

def query(options: dict, root: str = ".") -> dict:
    config = Config.load(os.path.join(root, options.get("config", "config.json")))
    group = options.get("group", "all")
    groups = [(g, getattr(config, f"{g}_funds")) for g in ("passive", "active") if group in ("all", g)]
    codes = set(options.get("code") or [])
    selected = [{**f, "group": g} for g, fs in groups for f in fs if not codes or f.get("code") in codes]
    history_path = config.history_file if os.path.isabs(config.history_file) else os.path.join(root, config.history_file)
    include_market = options.get("action") == "market-distribution" or options.get("include_market_distribution", False)
    rows = []
    for g, _ in groups:
        fs = [f for f in selected if f["group"] == g]
        if not fs: continue
        latest = History(history_path, namespace=g).latest_snapshot()
        with contextlib.redirect_stdout(io.StringIO()):
            result = fetch_all(fs, history_latest=latest, include_market_distribution=include_market, market_distribution_year=options.get("year", 2026))
        for row in result: row["group"] = g
        rows.extend(result)
    if options.get("status"): rows = [r for r in rows if options["status"] in r.get("purchase_status", "")]
    action = options.get("action", "snapshot")
    fields = {"quota": ("code","name","display","group","purchase_status","purchase_limit","source","confidence","warnings","error"), "performance": ("code","name","display","group","return_1y","nav","nav_date","source","confidence","warnings","error"), "market-distribution": ("code","name","display","group","market_distribution","market_distribution_report_id","market_distribution_error")}
    data = [{k: r.get(k) for k in fields[action]} for r in rows] if action in fields else rows
    if options.get("sort"):
        key = options["sort"]; data.sort(key=lambda r: (_number(r.get(key)) if key != "name" else r.get(key, "")) or 0, reverse=key == "return_1y")
    if action == "summary":
        data = {"by_group": {g: sum(r.get("group") == g for r in rows) for g, _ in groups}, "by_status": {s: sum(r.get("purchase_status") == s for r in rows) for s in sorted({r.get("purchase_status") for r in rows})}, "top_return_1y": sorted(rows, key=lambda r: _number(r.get("return_1y")) or -1e9, reverse=True)[:10]}
    elif options.get("limit") is not None: data = data[:max(0, options["limit"])]
    return {"as_of": datetime.now().isoformat(timespec="seconds"), "action": action, "count": len(rows), "data": data, "warnings": [f"{r.get('code')}: {w}" for r in rows for w in (r.get("warnings") or [])]}
