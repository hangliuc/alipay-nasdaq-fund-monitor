# 博时基金 Adapter

## 适配器定位

`BoseraAdapter` 只读取博时基金官网公开页面：基金目录页内嵌的
`window.fundListJson`，以及单只基金产品页。它不登录交易系统、不提交订单，
也不使用第三方平台。

## 官方来源

| 数据 | 官方入口 | 读取方式 |
| --- | --- | --- |
| 全量基金/份额目录 | [博时基金目录](https://www.bosera.com/fund/index.html) | 解析 HTML 中的 `window.fundListJson`，当前实测 763 条份额 |
| 产品资料、净值、交易按钮 | `https://www.bosera.com/fund/{code}.html` | 解析产品页 HTML |
| 当前大额申购限制 | 同一目录 JSON 的 `limitLargeDesc` / `limitLarge` | 优先个人客户金额；“暂停申购”保留为暂停 |

## 可获取字段

### 目录（`FundIdentity`）

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `code` | `fundCode` / `subFundCode` | `str` | 六位基金/份额代码 |
| `name` | `shortName`，缺失时 `fundName` | `str` | 官网当前份额名称 |
| `fund_type` | `fundTypeShow` / `fundType` | `str` | 官网原始基金类型 |
| `share_class` | 名称末尾后缀 | `str` | 解析 A/C/I 等份额类别 |
| `source_url` | 代码拼接产品页 | `str` | `https://www.bosera.com/fund/{code}.html` |
| `source_type` | 固定值 | `str` | `bosera_official_fund_catalogue_json` |
| `raw` | 目录原始行 | `dict` | 限额快照中保留净值、风险、状态、起投金额和限额描述 |

### 产品页（`ProductSnapshot`）

| 标准字段 | 页面字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | `.fund-essential_head .name` | `str` | 当前份额名称 |
| `full_name` | `#fundInfo` 中“基金名称” | `str` | 基金全称 |
| `fund_type` | 产品摘要“分类” | `str` | 例如 `QDII` |
| `risk_level` | 产品摘要“风险等级” | `str` | 例如 `中高` |
| `inception_date` | `#fundInfo`“成立生效日期” | `str` | 官网原文日期 |
| `asset_scale` | `#fundInfo`“基金规模” | `str` | 未公开时为空 |
| `net_value_date` | “单位净值 / 日涨幅 (YYYY-MM-DD)” | `str` | 最新净值日期 |
| `trade_status` | `.stint-money` | `str` | 例如“暂停申购” |
| `fields[购买按钮状态]` | `.btn-primary` 是否 `disabled` | `str` | `open` / `closed` |
| `fields[定投按钮状态]` | `.btn-default` 是否 `disabled` | `str` | `open` / `closed` |
| `fields` | `#fundInfo` 表格及摘要 | `dict` | 保留产品资料原始标签和值 |

### 直销限额（`DirectLimitSnapshot`）

| 输出字段 | 来源字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `limit` | `limitLargeDesc` | `str/null` | 个人金额优先；金额统一为人民币元格式 |
| `status` | 解析结果 | `str` | `ok` / `official_api_no_data` / `official_interface_requires_auth` |
| `quota_type` | 固定口径 | `str` | 官网产品目录当前大额申购限制 |
| `quota_remark` | `limitLargeDesc` 原文 | `str` | 保留官网当前限额描述 |
| `source_url` | 目录或产品页 URL | `str` | 当前结果实际来源 |
| `observed_at` | 运行时钟 | `str` | ISO 8601 观察时间 |
| `raw` | 目录行 + 产品字段 | `dict` | 保留所有原始字段 |

限额解析状态规则：`暂停申购` → `暂停`；`不限/不设上限/无限额` → `不限`；只有起投金额或泛化“限大额”而无上限 → `official_api_no_data`。

## 认证边界

当前目录和产品页匿名可访问，`auth_boundary()` 返回 `public`。交易入口不在本
适配器范围内；若官网后续返回 401/403，结果会标记
`official_interface_requires_auth`，不会绕过认证。

## 当前实测（2026-08-26）

基金 `016057` 产品页显示“暂停申购”，目录 `limitLargeDesc` 同样为“暂停申购”，
因此直销结果为 `暂停`。产品页最新净值日期为 2026-08-24。

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销状态**——`016057=暂停`（官网目录/产品页明确“暂停申购”）。
- 只有本 Adapter 本次从博时官网公开目录或产品页解析出的明确限额/暂停/恢复状态，才写入“已获取”；页面无字段、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
