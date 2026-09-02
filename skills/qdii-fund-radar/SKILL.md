---
name: qdii-fund-radar
description: 为其他 agent 提供本项目 QDII 基金的申购限额、申购状态、近一年收益率、市场分布和数据源健康度查询，并返回结构化 JSON。
---

# QDII 基金雷达

这是一个自包含 Skill。安装时只需复制本目录，不需要安装 QDII Radar 主项目或连接服务器。运行 `scripts/query.py` 时会通过 AKShare 批量查询，再逐只访问天天基金 HTML 详情页；该接口为只读操作，标准输出返回一份 JSON 文档。

安装依赖：

```bash
cd skills/qdii-fund-radar
pip install -r requirements.txt
```

查询限额、申购状态、净值和收益率时，AKShare 作为批量主源，天天基金 HTML 详情页作为备用和交叉验证来源。两者都失败时，会尝试使用本机缓存；缓存默认位于用户缓存目录，也可用 `QDII_RADAR_CACHE` 自定义。查询证监会市场分布需要 `pdfplumber`。

如需换一组基金，可通过 `--config PATH` 传入 JSON。配置可以沿用本项目的 `passive_funds`/`active_funds` 结构，也可以使用 `{"funds": [{"code": "...", "name": "...", "group": "passive"}]}` 结构。

支持以下操作：

- `snapshot`：综合快照（默认）
- `quota`：申购状态和单日申购限额
- `performance`：近一年收益率、最新净值和净值日期
- `market-distribution`：证监会季报中的国家/地区市场分布
- `summary`：按分组、申购状态统计，并列出收益率排名

支持以下过滤和排序参数：`--code CODE ...`、`--group passive|active|all`、`--status STATUS`、`--sort return_1y|purchase_limit|name`、`--limit N`。查询市场分布时可用 `--year YYYY` 指定报告年份；综合快照可加 `--include-market-distribution`。

向用户或其他 agent 返回结果时，必须保留 `source`、`quota_source`、`performance_source`、`cross_validation`、`confidence`、`warnings` 和 `error`。`source=stale` 表示使用历史兜底数据。`cross_validation` 可能为 `matched`、`mismatch`、`akshare_only`、`html_only`、`stale` 或 `none`。暂停申购基金可能不在 AKShare 的排行结果中，此时若限购数据来自 AKShare 且 HTML 验证一致，`performance_source=html` 不会单独导致 `confidence=medium`。市场分布来自季度报告，不是实时持仓；不要根据这些字段推断投资建议或未来收益。

示例：

```bash
python3 skills/qdii-fund-radar/scripts/query.py --action quota --status 限大额
python3 skills/qdii-fund-radar/scripts/query.py --action performance --sort return_1y --limit 10
python3 skills/qdii-fund-radar/scripts/query.py --action market-distribution --code 008971 --year 2026
```
