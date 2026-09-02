# QDII Fund Radar

[English](README_EN.md)

QDII Fund Radar 是一个面向 Agent 的基金信息查询 Skill，同时提供定时监控、日报生成和消息推送能力。Agent 可以通过结构化 JSON 查询 QDII 基金的限购、收益和市场分布信息，也可以在服务器上运行完整的日报任务。

仓库地址：[github.com/hangliuc/qdii-fund-radar](https://github.com/hangliuc/qdii-fund-radar)

## Agent Skill

`skills/qdii-fund-radar/` 是本项目的核心交付物，可独立复制和安装，不依赖主项目或服务器。Skill 通过 `scripts/query.py` 提供只读查询接口，标准输出返回结构化 JSON，适合 Agent 调用、筛选、排序和二次分析。

支持以下查询操作：

- `snapshot`：返回基金综合快照。
- `quota`：查询申购状态和单日申购限额。
- `performance`：查询近一年收益率、最新净值和净值日期。
- `market-distribution`：查询证监会季报中的国家/地区市场投资分布。
- `summary`：按分组和申购状态统计，并列出收益率排名。

支持按基金代码、基金分组、申购状态筛选，也支持按近一年收益率、申购额度或名称排序。每条结果都会保留 `source`、`confidence`、`warnings` 和 `error` 字段，便于 Agent 判断结果质量和处理异常。

示例：

```bash
python3 skills/qdii-fund-radar/scripts/query.py --action snapshot
python3 skills/qdii-fund-radar/scripts/query.py --action quota --status 限大额
python3 skills/qdii-fund-radar/scripts/query.py --action performance --sort return_1y --limit 10
python3 skills/qdii-fund-radar/scripts/query.py --action market-distribution --code 008971 --year 2026
```

Skill 的完整安装和参数说明见 [`skills/qdii-fund-radar/SKILL.md`](skills/qdii-fund-radar/SKILL.md)。

## 功能

- 可以获取基金限购状态、限购额度、净值和近一年收益率。
- 可选抓取证监会季报中的国家/地区市场投资分布。
- 支持被动型和主动型基金配置，以及历史记录和限额变化识别。

## 目录结构

```text
fund_monitor/             核心抓取、降级、历史、图片和通知逻辑
config.json               基金列表和运行配置
data/history.json         历史快照（运行时状态）
data/cards/               生成的日报图片（运行时产物）
docs/                     数据来源和采集流程说明
fonts/                    图片渲染所需中文字体
tests/fixtures/           数据源解析测试夹具
.github/workflows/        自动部署和手动运行工作流
Dockerfile                定时任务镜像
docker-compose.yml        monitor + nginx 服务编排
```

## 本地运行

要求：Python 3.11+。

```bash
git clone https://github.com/hangliuc/qdii-fund-radar.git
cd qdii-fund-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py --dry-run --force
```

`--dry-run` 只是不发送飞书通知；如需同时避免写历史或生成图片，可使用：

```bash
python3 main.py --dry-run --force --no-history --no-image
```

## 命令参数

```text
--config PATH                 使用指定配置文件，默认 config.json
--fund-codes CODE ...         只查询指定基金代码
--dry-run                     不发送飞书通知
--no-history                  不更新 data/history.json
--no-image                    不生成日报图片
--no-notify                   完全关闭通知
--force                       忽略周末和节假日检查
--market-distribution         抓取证监会市场分布数据
```

示例：

```bash
python3 main.py --force --fund-codes 008971 000041
python3 main.py --dry-run --force --market-distribution
```

## 配置

基金列表和默认选项位于 `config.json`：

```json
{
  "passive_funds": [
    {"code": "012345", "name": "示例基金", "share_class": "C"}
  ],
  "active_funds": [],
  "history_file": "data/history.json",
  "image_base_url": "http://your-server:8900",
  "market_distribution_enabled": false,
  "market_distribution_year": 2026,
  "feishu_webhook": ""
}
```

生产环境建议通过环境变量注入敏感或环境相关配置：

```bash
export FEISHU_WEBHOOK='https://open.feishu.cn/open-apis/bot/v2/hook/…'
export IMAGE_BASE_URL='http://your-server:8900'
```

`feishu_webhooks` 可配置多个通知地址；程序会自动去重并忽略空值。不要把真实 webhook 提交到 Git。

## 推送模块

推送模块负责定时运行监控任务、生成日报卡片并发送通知：

- 周末和中国法定非交易日自动跳过，也可用 `--force` 强制运行。
- 分别监控被动型、主动型基金，支持按代码筛选。
- 生成适合飞书消息使用的 PNG 日报卡片。
- 当前支持飞书机器人推送；微信、钉钉等渠道可以随时支持。
- 推送内容可包含限购状态变化、限额变化、净值、收益率和市场分布信息。

通知渠道通过独立的 `FeishuNotifier` 模块实现，后续增加微信、钉钉或其他渠道时，不需要改动基金查询和日报生成逻辑。

## Docker 部署

项目包含两个服务：`monitor` 负责定时运行和生成图片，`nginx` 负责以 HTTP 提供 `data/cards/` 中的图片。

```bash
export FEISHU_WEBHOOK='你的飞书 webhook'
export IMAGE_BASE_URL='http://服务器地址:8900'
docker compose up -d --build
docker compose ps
docker compose logs -f monitor
```

手动执行一次：

```bash
docker compose run --rm monitor python main.py --force --dry-run
```

日报图片默认通过 `8900` 端口访问。若服务器有防火墙或安全组，需要放行该端口，或将 `IMAGE_BASE_URL` 配置为反向代理后的 HTTPS 地址。

## GitHub Actions

- `deploy.yml`：推送到 `main` 后，通过 SSH 在服务器上拉取 [qdii-fund-radar](https://github.com/hangliuc/qdii-fund-radar) 并重建服务。
- `manual-run.yml`：手动触发一次容器内查询，可选择 dry-run 和不写历史。
- `fund-monitor.yml`：项目原有的定时工作流，是否启用取决于仓库中的 workflow 配置。

`deploy.yml` 需要以下 GitHub Secrets：`SERVER_HOST`、`SERVER_USER`、`SSH_PRIVATE_KEY`、`FEISHU_WEBHOOK`。服务器上的项目目录和容器名称可以继续使用现有部署配置，无需因仓库改名迁移运行数据。

## 数据源与降级

详见 [`docs/data-acquisition-flow.md`](docs/data-acquisition-flow.md)。每条结果包含：

- `source`：`jjjz`、`html`、`stale` 或 `none`。
- `confidence`：当前结果的可信度。
- `warnings`：数据源切换、陈旧或不一致提醒。
- `error`：最终查询失败时的错误信息。

市场分布来自证监会季度报告，不是实时持仓数据，不能据此推断投资建议或未来收益。

## 测试与代码检查

当前仓库主要包含数据源解析夹具，可使用标准库 unittest 扫描测试目录；若后续补充测试文件，命令无需变化：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

项目配置了 Ruff 规则，建议提交前运行：

```bash
ruff check .
```

## 注意事项

- 天天基金和证监会页面属于外部数据源，接口或页面变化可能导致降级或失败。
- `data/history.json` 用于状态变化比较，生产部署时应持久化并定期备份。
- `data/cards/` 是运行时生成目录，不应作为源码提交。

## 免责声明

本项目及其 Agent Skill 仅用于公开信息的查询、整理、技术演示和提醒，不构成任何形式的投资建议、收益承诺、买卖推荐或其他金融服务。基金数据可能存在延迟、缺失、错误或因页面和接口变化而无法获取的情况；任何查询结果均不应作为投资决策的唯一依据。使用者应自行核实信息并独立承担投资判断和相关风险，项目作者不对因使用本项目造成的任何直接或间接损失承担责任。
