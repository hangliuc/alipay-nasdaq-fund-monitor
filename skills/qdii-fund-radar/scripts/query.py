#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, ROOT)
from fund_monitor.agent_api import query

def main():
    p = argparse.ArgumentParser(description="Query QDII Fund Radar as JSON")
    p.add_argument("--action", choices=["snapshot", "quota", "performance", "market-distribution", "summary"], default="snapshot")
    p.add_argument("--config", default="config.json")
    p.add_argument("--code", nargs="+")
    p.add_argument("--group", choices=["all", "passive", "active"], default="all")
    p.add_argument("--status"); p.add_argument("--sort", choices=["return_1y", "purchase_limit", "name"])
    p.add_argument("--limit", type=int); p.add_argument("--year", type=int, default=2026)
    p.add_argument("--include-market-distribution", action="store_true")
    args = p.parse_args()
    try:
        print(json.dumps(query(vars(args), root=ROOT), ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr); raise SystemExit(1)
if __name__ == "__main__": main()
