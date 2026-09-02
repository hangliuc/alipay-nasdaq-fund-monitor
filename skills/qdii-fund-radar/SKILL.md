---
name: qdii-fund-radar
description: 为其他 agent 提供本项目 QDII 基金的申购限额、申购状态、近一年收益率、市场分布和数据源健康度查询，并返回结构化 JSON。
---

# QDII 基金雷达

从项目根目录运行 `scripts/query.py`。该接口为只读操作，标准输出返回一份 JSON 文档，抓取进度输出到标准错误，不会污染 agent 的数据结果。

支持以下操作：

- `snapshot`：综合快照（默认）
- `quota`：申购状态和单日申购限额
- `performance`：近一年收益率、最新净值和净值日期
- `market-distribution`：证监会季报中的国家/地区市场分布
- `summary`：按分组、申购状态统计，并列出收益率排名

支持以下过滤和排序参数：`--code CODE ...`、`--group passive|active|all`、`--status STATUS`、`--sort return_1y|purchase_limit|name`、`--limit N`。查询市场分布时可用 `--year YYYY` 指定报告年份；综合快照可加 `--include-market-distribution`。

向用户或其他 agent 返回结果时，必须保留 `source`、`confidence`、`warnings` 和 `error`。`source=stale` 表示使用历史兜底数据。市场分布来自季度报告，不是实时持仓；不要根据这些字段推断投资建议或未来收益。

示例：

```bash
python3 skills/qdii-fund-radar/scripts/query.py --action quota --status 限大额
python3 skills/qdii-fund-radar/scripts/query.py --action performance --sort return_1y --limit 10
python3 skills/qdii-fund-radar/scripts/query.py --action market-distribution --code 008971 --year 2026
```
