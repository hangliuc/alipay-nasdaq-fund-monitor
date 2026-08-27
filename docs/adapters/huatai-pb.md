# 华泰柏瑞基金 Adapter

## 结论

华泰柏瑞官网首页公开脚本 `common/index.html.js` 内嵌 `FundArr` 基金目录。
每个目录行同时包含基金基本信息、净值、风险、当前交易状态和产品页展示的
申购上限 `limitmoney`。当前实现已将原来的单基金脚本解析提升为独立
`HuataiPBAdapter`，不需要账号、开户、验证码、设备签名或银行卡。

## 官方来源

| 用途 | 官方来源 | 说明 |
| --- | --- | --- |
| 官网首页 | [华泰柏瑞基金](https://www.huatai-pb.com/) | `FundArr` 脚本的页面入口 |
| 基金目录、产品快照 | [`common/index.html.js?v=`](https://www.huatai-pb.com/common/index.html.js?v=) | `var FundArr = [...]`，当前公开基金及份额数组 |
| 产品页 | `https://www.huatai-pb.com/products/{category}/{code}/index.html` | 目录行 `url` 字段提供具体产品页 |

以上均为华泰柏瑞官方域名，不使用东方财富、支付宝、天天基金或其他第三方
平台作为直销数据源。

## 可获取字段

### 1. 基金目录（`FundIdentity`）

| 标准字段 | 官网字段 | 说明 |
| --- | --- | --- |
| `code` | `fundcode` | 六位基金代码 |
| `name` | `fundname` | 基金/份额名称 |
| `fund_type` | `fundtype` / `property` | 官网基金类型 |
| `share_class` | 名称末尾 `A/B/C/I` | 从名称识别份额类别 |
| `source_url` | `url` | 具体产品页绝对 URL |
| `source_type` | — | `huatai_pb_official_fundarr_catalogue` |

目录按基金代码去重，重复记录优先保留字段更完整的一行。官网当前数组的
实际数量以每次抓取为准，不把它推断为历史上所有已发行基金。

### 2. 产品详情（`ProductSnapshot`）

| 标准字段 | 官网字段 | 说明 |
| --- | --- | --- |
| `name` | `fundname` | 基金简称/份额名称 |
| `full_name` | `fundFullName` | 基金全称 |
| `fund_type` | `fundtype` / `property` | 基金类型 |
| `risk_level` | `levelofriskStr` / `levelofrisk` | 风险等级 |
| `inception_date` | `setupdate` | 统一为 `YYYY-MM-DD` |
| `asset_scale` | `lastasset` | 官网原始规模值 |
| `net_value_date` | `valuedate` | 最新净值日期 |
| `trade_status` | `statusStr` | 例如“正常开放”“暂停申购” |
| `fields` | `FundArr` 当前行 | 保留全部原始字段 |

其他常用原始字段包括 `todaynetvalue`、`managerName`、`trusteeName`、
`operationMode`、`buypoint`、`limitStartDate`、`limitEndDate`、`status`、
`curStatus` 以及收益字段。`buypoint` 仅表示最低申购金额，不作为限额使用。

### 3. 交易状态（`TradeSnapshot`）

| 官网 `statusStr` | `subscription` | `redemption` | 说明 |
| --- | --- | --- | --- |
| `正常开放` | `true` | `true` | 官网显示正常开放 |
| `认购期` | `true` | `null` | 处于认购期，官网未给出赎回判断 |
| `暂停申购` | `false` | `true` | 申购暂停、赎回仍按开放处理 |
| `暂停赎回` | `true` | `false` | 赎回暂停 |
| `暂停交易` / `基金终止` | `false` | `false` | 交易不可用或基金终止 |
| `敬请期待` / 空值 | `null` | `null` | 不从文案猜测状态 |

当前输出一条 `customer_type=individual`、`channel=华泰柏瑞官网 FundArr
公开目录脚本` 的官方记录，并保留完整目录行。

### 4. 直销限额（`DirectLimitSnapshot`）

| 输出字段 | 规则 |
| --- | --- |
| `limit` | 解析 `limitmoney`，例如 `每日10元` → `10元`、`500万` → `500万元` |
| `status` | 有金额或官网明确暂停申购时为 `ok`；开放但无金额为 `official_api_no_data` |
| `quota_type` | 有金额时为“产品页申购上限” |
| `quota_remark` | 保留 `limitmoney` 和 `statusStr` |
| `source_url` | 华泰柏瑞官方 `FundArr` 脚本 URL |
| `observed_at` | 本次抓取时间 |
| `raw` | 当前基金目录行全部原始字段 |

`limitmoneytemp` 暂不作为当前限额覆盖字段；`buypoint` 明确不作为申购上限。
如果 `limitmoney` 是 `--`，且状态为正常开放，不推断为“不限”；如果状态为
`暂停申购`，即使限额文本是 `每日10` 这类没有货币单位的文案，也只输出官网
确认的 `暂停`，不猜测金额。

## 认证边界

当前公开脚本可匿名访问，`auth_boundary()` 返回 `public`。若未来出现 HTTP
401/403，Adapter 返回 `official_interface_requires_auth`，不会绕过登录、验证码、
设备签名或银行卡绑定。

## 代码和测试

- 实现：[huatai_pb.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/huatai_pb.py)
- 兼容入口：[product_pages.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/product_pages.py)
- 单元测试：[test_huatai_pb_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_huatai_pb_adapter.py)

```bash
python3 -m pytest -q tests/test_huatai_pb_adapter.py
```

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`019525=10元`（华泰柏瑞官网公开 `FundArr.limitmoney`）。
- 只有本 Adapter 本次从官网公开目录脚本解析出的当前字段，才写入“已获取”；脚本不可访问、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据；临时核验值仅可作为审计记录，不得覆盖本次未获取状态。
