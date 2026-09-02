# QDII Quota Radar

一个面向纳斯达克及全球科技主题 QDII 基金的申购额度监控工具：在中国交易日抓取申购状态、单日限额与近一年收益率，生成小红书卡片，并通过飞书提醒。

> 数据仅供信息参考，不构成投资建议。实际申购规则以基金管理人和交易渠道的最新页面或公告为准。

## 能做什么

- 监控 `config.json` 中的被动指数型、主动管理型 QDII 基金（当前共 37 只）。
- 生成两张 1080px 宽竖版卡片：被动型（指数基金）和主动型（主动管理 QDII）。
- 卡片展示基金简称、代码、近一年收益率、申购状态和当日限额，并按额度排序。
- 自动发现申购状态/限额变化，飞书消息附带可复制的发布文案。
- 保存最近 30 个有记录日期的快照；周末、中国法定节假日和调休自动处理。
- 支持多个飞书 Webhook、静态图片链接和 Docker 定时运行。

## 日报数据链路

日报卡片只使用天天基金数据。

```text
天天基金 JJJZ 全市场接口 ──┐
  申购状态、申购限额、净值    ├──> 合并、排序、生成日报卡片 ──> 飞书提醒
天天基金 RANKING 全市场接口 ─┘
  近一年收益率
            │ 主源失败或个别基金缺失
            ▼
天天基金基金详情页（HTML）
            │ 限额仍不可得
            ▼
data/history.json 上次成功快照
```

正常运行只需两个 HTTP 请求：一次 JJJZ 全市场快照和一次 RANKING 全市场快照。仅在主源失败或某只基金缺失时，才逐只请求 HTML 详情页。

| 数据 | 正常来源 | 降级路径 |
| --- | --- | --- |
| 申购状态、申购限额、净值 | `Fund_JJJZ_Data.aspx` | 详情页 → 对应分组的历史快照 → 失败 |
| 近一年收益率 | `rankhandler.aspx`（包含暂停申购基金） | 详情页 → 空值 |

每条结果还包含诊断字段：`source`（`jjjz` / `html` / `stale` / `none`）、`confidence`（`high` / `medium` / `low`）和 `warnings`。发生历史回退、抓取失败或主备数据不一致时，命令行和飞书会显示数据源健康提醒；没有可用数据的基金不会绘入卡片。

## 快速开始

要求：Python 3.11+。

```bash
git clone https://github.com/hangliuc/qdii-quota-radar.git
cd qdii-quota-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 本地试跑：生成卡片但不推送飞书
python3 main.py --dry-run --force --no-history
```

默认输出位置：

- 卡片：`data/cards/`
- 历史快照：`data/history.json`
- 数据源健康与限额变动：标准输出

## 常用命令

```bash
# 不发送飞书；仍会生成图片和写入历史
python3 main.py --dry-run

# 只检查指定基金
python3 main.py --dry-run --force --fund-codes 008971 019173

# 只看抓取与健康状态，不生成图片、不发通知
python3 main.py --no-image --no-notify --force

# 不写入历史，且强制忽略交易日判断
python3 main.py --dry-run --no-history --force
```

| 参数 | 说明 |
| --- | --- |
| `--config PATH` | 配置文件路径，默认 `config.json` |
| `--fund-codes CODE [CODE ...]` | 仅处理指定基金代码 |
| `--dry-run` | 不向飞书发送消息 |
| `--no-image` | 不生成日报卡片 |
| `--no-notify` | 不发送任何通知 |
| `--no-history` | 不更新历史快照和变动记录 |
| `--force` | 跳过交易日检查 |

## 配置与飞书

`config.json` 中的主要字段：

| 字段 | 说明 |
| --- | --- |
| `passive_funds` / `active_funds` | 两类基金清单 |
| `history_file` | 历史快照路径 |
| `image_base_url` | 卡片的公开访问基础 URL |
| `feishu_webhook` | 主飞书 Webhook（兼容旧字段） |
| `feishu_webhooks` | 额外 Webhook 列表，会自动去重合并 |

每只基金至少应有 `code`、`name`；`display` 用于指定卡片上的短名称。

推荐将敏感项以环境变量传入，而不要提交真实 Webhook：

```bash
export FEISHU_WEBHOOK='https://open.feishu.cn/open-apis/bot/v2/hook/…'
export IMAGE_BASE_URL='https://example.com/qdii-cards'
python3 main.py --force
```

`FEISHU_WEBHOOK`、`IMAGE_BASE_URL` 会覆盖配置文件中的对应值。没有 Webhook 时，通知内容会回退打印到控制台。

## Docker 部署

`docker-compose.yml` 包含两个服务：

- `monitor`：通过 cron 于每天北京时间 07:16 运行；非交易日自动退出。
- `nginx`：将 `data/cards/` 以静态文件方式映射到宿主机 `8900` 端口，供飞书打开卡片。

创建 `.env` 后启动：

```dotenv
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/…
IMAGE_BASE_URL=http://YOUR_SERVER_HOST:8900
```

```bash
docker compose up -d --build

# 立即验证一次
docker compose run --rm monitor python main.py --force --dry-run --no-history

# 查看 cron 日志
docker logs -f qdii-quota-radar
```

`data/` 挂载在容器外，重建容器后历史与卡片仍会保留。`IMAGE_BASE_URL` 必须能从飞书客户端访问；内网地址通常无法让外网用户打开图片。

## 自动化与测试

- 推送到 `main` 会触发 GitHub Actions，通过 SSH 更新部署服务器并重建 Compose 服务。
- `Manual Run (on server)` 工作流可在服务器上手动真实运行，支持 `dry_run`、`no_history` 开关。
- `Fund Monitor (manual test only)` 工作流可执行一次本地 dry-run。
- 官网 Adapter 测试使用本地 fixture，不依赖实时官网：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

部署工作流需要 GitHub Secrets：`SERVER_HOST`、`SERVER_USER`、`SSH_PRIVATE_KEY`、`FEISHU_WEBHOOK`。

## 项目结构

```text
main.py                         命令行入口
config.json                     基金清单和默认配置
fund_monitor/
├── cli.py                      流程编排、交易日检查、通知与卡片生成
├── config.py                   JSON / 环境变量配置加载
├── trading_day.py              周末、节假日和调休判断
├── fetch/
│   ├── aggregator.py           双主源、HTML 降级、历史兜底与诊断
│   ├── sources/                天天基金 JJJZ、RANKING、详情页来源
├── storage/history.py          30 天历史快照与变动检测
└── output/                     Pillow 卡片与飞书消息
data/                           持久化历史和运行时卡片
docs/                           数据来源、设计与适配器报告
tests/                          官网适配器 fixture 回归测试
```

## 相关文档

- [更新日志](CHANGELOG.md)
- [适配器摘要](docs/adapters-summary-2026-08-26.md)
- [各公司 Adapter 文档](docs/adapters/)
# 基金市场分布

可选抓取证监会基金信息披露网站季报中的“各个国家（地区）证券市场投资分布”。该功能独立于限额与收益率数据源，默认关闭；运行时使用 `--market-distribution`，或在 `config.json` 中将 `market_distribution_enabled` 设为 `true`。年份由 `market_distribution_year` 控制，默认 2026。

基金配置可额外提供 `main_code`（A 类/主基金代码）和 `short_name`，用于证监会检索；未提供时回退到当前代码和名称。结果新增 `market_distribution`、`market_distribution_report_id` 字段，失败时写入 `market_distribution_error`，不会影响限购日报其它字段。
