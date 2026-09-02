# QDII Fund Radar

[中文](README.md)

QDII Fund Radar is an Agent-oriented fund information query Skill with scheduled monitoring, daily report generation, and message delivery capabilities. Agents can query QDII fund quota, performance, and market-distribution data as structured JSON, while the full monitoring job can run on a server.

Repository: [github.com/hangliuc/qdii-fund-radar](https://github.com/hangliuc/qdii-fund-radar)

## Agent Skill

`skills/qdii-fund-radar/` is the core deliverable of this project. It can be copied and installed independently without the main project or a server. The Skill provides a read-only query interface through `scripts/query.py`; its standard output is structured JSON designed for Agent calls, filtering, sorting, and further analysis.

Supported actions:

- `snapshot`: return a comprehensive fund snapshot.
- `quota`: query purchase status and daily purchase limits.
- `performance`: query one-year return, latest NAV, and NAV date.
- `market-distribution`: query country/region investment distribution from CSRC quarterly reports.
- `summary`: summarize funds by group and purchase status, with return rankings.

Results can be filtered by fund code, fund group, and purchase status, and sorted by one-year return, purchase limit, or name. Each result preserves `source`, `confidence`, `warnings`, and `error` fields so an Agent can assess result quality and handle exceptions.

Examples:

```bash
python3 skills/qdii-fund-radar/scripts/query.py --action snapshot
python3 skills/qdii-fund-radar/scripts/query.py --action quota --status 限大额
python3 skills/qdii-fund-radar/scripts/query.py --action performance --sort return_1y --limit 10
python3 skills/qdii-fund-radar/scripts/query.py --action market-distribution --code 008971 --year 2026
```

See [`skills/qdii-fund-radar/SKILL.md`](skills/qdii-fund-radar/SKILL.md) for installation and complete parameter documentation.

## Features

- Retrieve fund purchase status, purchase limits, NAV, and one-year return.
- Retrieve country/region investment distribution from CSRC quarterly reports.
- Configure passive and active funds, history tracking, and purchase-limit change detection.

## Project Structure

```text
fund_monitor/             Core query, history, image, and notification logic
config.json               Fund list and runtime configuration
data/history.json         Historical snapshots (runtime state)
data/cards/               Generated daily report images (runtime output)
docs/                     Data and collection-flow documentation
fonts/                    Chinese font used for image rendering
tests/fixtures/           Data-source parsing fixtures
.github/workflows/        Automated deployment and manual-run workflows
Dockerfile                Scheduled-job image
docker-compose.yml        monitor + nginx service definition
```

## Local Usage

Requirements: Python 3.11+.

```bash
git clone https://github.com/hangliuc/qdii-fund-radar.git
cd qdii-fund-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py --dry-run --force
```

`--dry-run` disables Feishu delivery only. To also avoid writing history or generating images:

```bash
python3 main.py --dry-run --force --no-history --no-image
```

## Command-Line Options

```text
--config PATH                 Configuration file, default: config.json
--fund-codes CODE ...         Query only the specified fund codes
--dry-run                     Do not send Feishu notifications
--no-history                  Do not update data/history.json
--no-image                    Do not generate report images
--no-notify                   Disable notifications completely
--force                       Ignore weekend and holiday checks
--market-distribution         Retrieve CSRC market-distribution data
```

## Configuration

The fund list and default options are stored in `config.json`. In production, inject sensitive or environment-specific values through environment variables:

```bash
export FEISHU_WEBHOOK='https://open.feishu.cn/open-apis/bot/v2/hook/…'
export IMAGE_BASE_URL='http://your-server:8900'
```

`feishu_webhooks` supports multiple notification endpoints. Empty values are ignored and duplicates are removed. Never commit real webhook URLs to Git.

## Push Module

The push module schedules monitoring jobs, generates daily report cards, and sends notifications:

- Weekends and Chinese statutory non-trading days are skipped automatically; use `--force` to run anyway.
- Passive and active funds are monitored separately, with filtering by fund code.
- PNG daily report cards are generated for use in Feishu messages.
- Feishu bot delivery is supported today; WeChat, DingTalk, and other channels can be added at any time.
- Notifications can include purchase-status changes, purchase-limit changes, NAV, return, and market-distribution information.

Notification delivery is isolated in the `FeishuNotifier` module. Adding WeChat, DingTalk, or another channel does not require changing the fund-query or report-generation logic.

## Docker Deployment

The project includes two services: `monitor` runs scheduled jobs and generates images; `nginx` serves images from `data/cards/` over HTTP.

```bash
export FEISHU_WEBHOOK='your Feishu webhook'
export IMAGE_BASE_URL='http://your-server:8900'
docker compose up -d --build
docker compose ps
docker compose logs -f monitor
```

Run a one-off query:

```bash
docker compose run --rm monitor python main.py --force --dry-run
```

Images are served on port `8900` by default. Open the port in the firewall/security group, or set `IMAGE_BASE_URL` to an HTTPS reverse-proxy URL.

## GitHub Actions

- `deploy.yml`: after pushes to `main`, connects through SSH, pulls [qdii-fund-radar](https://github.com/hangliuc/qdii-fund-radar), and rebuilds the services.
- `manual-run.yml`: manually runs one query in the container, with options for dry-run and skipping history updates.
- `fund-monitor.yml`: the original scheduled workflow; whether it is enabled depends on the workflow configuration in the repository.

Required repository secrets for `deploy.yml`: `SERVER_HOST`, `SERVER_USER`, `SSH_PRIVATE_KEY`, and `FEISHU_WEBHOOK`.

## Checks

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
ruff check .
```

## Disclaimer

This project and its Agent Skill are intended only for querying, organizing, demonstrating, and reminding users about publicly available information. They do not constitute investment advice, a promise of returns, a recommendation to buy or sell, or any other financial service. Fund data may be delayed, incomplete, inaccurate, or unavailable due to changes in external pages and interfaces. Users should independently verify all information and bear responsibility for their own investment decisions and risks. The project authors are not liable for any direct or indirect loss resulting from use of this project.
