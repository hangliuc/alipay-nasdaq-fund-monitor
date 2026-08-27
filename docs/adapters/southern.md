# 南方基金 Adapter

## 结论

南方基金官网有一组无需登录的公开 JSON 接口：`fundList` 提供当前基金目录，
`overreview` 提供单只基金产品资料，`subscriptionAndRedemptionStatus` 提供当前
申购、赎回、定投和转换状态及公告中的大额申购限额。当前实现为独立的
`SouthernAdapter`，只发送只读请求，不提交交易，也不绕过认证边界。

## 官方来源

| 用途 | 官方入口/接口 | 说明 |
| --- | --- | --- |
| 产品状态页 | [南方基金产品状态与限额](https://www.nffund.com/new/transaction-guide/product-status-and-limits.html) | 前端公开展示状态，调用状态 API |
| 基金目录页 | [南方基金产品中心](https://www.nffund.com/new/personal-financing/fund-products.html) | 目录前端入口 |
| 基金目录 API | `https://www.nffund.com/nfwebApi/fund/fundList` | 返回 `g_classify_fhb`、`g_classify_all` 等当前目录数组 |
| 交易状态 API | `https://www.nffund.com/nfwebApi/customer/subscriptionAndRedemptionStatus` | 返回 `currentDate`、`fundlist` 和各业务状态 |
| 产品详情页 | `https://www.nffund.com/new/personal-financing/detail.html?fundCode={code}` | 动态产品资料入口 |
| 产品详情 API | `https://www.nffund.com/nfwebApi/fund/overreview` | POST `fundCode={code}`，返回产品、净值、风险和规模字段 |

以上均为南方基金官方 `nffund.com` 域名，不使用东方财富、支付宝、天天基金或
其他第三方平台作为直销数据源。

## 可获取字段

南方 Adapter 的输出按“基金目录 → 产品详情 → 交易状态 → 限额结果”分层，既
提供标准字段，也保留官网原始值。

### 1. 基金目录（`FundIdentity`）

| 标准字段 | 官网字段 | 说明 |
| --- | --- | --- |
| `code` | `fundcode` | 六位基金代码 |
| `name` | `fundname` | 基金/份额名称 |
| `fund_type` | `sectype` / `fundtype` | 官网原始基金类型 |
| `share_class` | 名称末尾 `A/B/C` | 从份额名称识别 |
| `source_url` | — | 南方官方产品详情页 |
| `source_type` | — | `southern_official_fund_catalogue_api` 或状态 API |

目录接口中的以下字段也会随原始响应保留：

| 原始字段 | 含义 |
| --- | --- |
| `riskLevel` | 风险等级 |
| `nav` / `fdate` | 最新净值 / 净值日期 |
| `asset` | 资产规模 |
| `fundManagerName` / `managerId` | 基金经理 / 经理编号 |
| `foundDateStr` | 成立日期 |
| `transactionState` | 官网交易状态码 |
| `minBuy` | 最低申购金额 |
| `status` / `zx_status` | 官网状态码 / 直销状态码 |

目录由 `fundList` 和交易状态 API 合并后按代码去重，避免单一列表遗漏份额。

### 2. 产品详情（`ProductSnapshot`）

| 标准字段 | 官方来源字段 | 输出示例/说明 |
| --- | --- | --- |
| `name` | `fund_info.fundName` | 份额名称 |
| `full_name` | `fund_info.fundNameEx` | 基金全称 |
| `fund_type` | `fund_info.basedetailType` | 例如“股票型” |
| `risk_level` | `fundRiskRating.RISKRATING` | 例如“中高风险(R4)” |
| `inception_date` | `fund_info.fundDate` | 统一为 `YYYY-MM-DD` |
| `asset_scale` | `quarter.fundsize` | 官网返回的人民币规模值 |
| `net_value_date` | `fundReturn.FDATE` | 统一为 `YYYY-MM-DD` |
| `trade_status` | 状态字段组合 | 例如“开放申购 开放赎回 开放定投” |
| `fields` | `fund_info`、`fundReturn`、`fundRiskRating`、`quarter` | 原始字段全部保留 |

嵌套字段在 `fields` 中使用前缀，例如 `fundReturn.FUNDNAV`、
`quarter.reportdate`；状态表原始字段使用 `status.` 前缀。

### 3. 交易状态（`TradeSnapshot`）

| 官方字段 | 标准字段 | 值含义 |
| --- | --- | --- |
| `sgStatus` | `subscription` | `1` 开放申购，`0` 暂停申购 |
| `shStatus` | `redemption` | `1` 开放赎回，`0` 暂停赎回 |
| `dtStatus` | `sip` | `1` 开放定投，`0` 暂停定投 |
| `zhrStatus` | `transfer_in` | 转换转入状态 |
| `zhcStatus` | `transfer_out` | 转换转出状态 |
| `transactionState=10` | `message` | `认购期` |
| `remark` | `quota_remark` | 限额及适用范围原文 |
| `currentDate` | `raw.currentDate` | 官网状态表日期 |

未知或缺失的状态值保持 `null`，不从页面导航文字推断。当前输出一条
`customer_type=individual`、`channel=南方官网公开交易状态 API` 的官方记录。

### 4. 直销限额（`DirectLimitSnapshot`）

| 输出字段 | 规则 |
| --- | --- |
| `limit` | 从 `remark` 解析明确金额；例如“限额调整为 10 元” → `10元` |
| `status` | 有金额或确认暂停时为 `ok`；开放但无金额为 `official_api_no_data` |
| `quota_type` | 有金额时为“大额申购（含定投和转换转入）” |
| `quota_remark` | 保留官网公告/状态原文 |
| `source_url` | 交易状态 API URL |
| `observed_at` | 本次抓取时间 |
| `raw` | `currentDate`、状态行和原始字段 |

南方接口未拆分直销/代销渠道；按项目规则，官方非第三方状态作为直销结果，
同时保留接口口径，避免将其误写成“已确认的直销专属字段”。

## 直销限额规则

南方这个公开状态接口没有拆出“直销/代销”两条渠道，而是官网当前交易状态和
适用范围。按照本项目“非第三方官方来源默认作为直销结果”的规则，Adapter 将
这条官方状态记录输出为“南方官网公开交易状态 API”，同时保留原始 `remark` 和
观察日期，避免把渠道口径隐藏掉。

1. 在 `remark` 中仅识别带有明确限额语义的金额，例如“限额调整为 10 元”“不超过 1 万元”“上限为 100 万元”。
2. 金额统一由人民币元格式化（`100000` → `10万元`）。
3. `sgStatus=0` 且没有金额时，当前申购状态已被官网确认，结果为 `暂停`、状态 `ok`。
4. 申购开放但 `remark` 没有明确金额时，结果为 `official_api_no_data`，不猜测为“不限”。
5. 结果中的 `currentDate`、状态原文、接口 URL 和 `observed_at` 均保留，便于判断数据是否为本次抓取的当前快照。

示例：官网当前对 016453 返回“C 类份额的大额申购（含定投和转换转入）限额调整为 10 元”，适配器输出直销限额 `10元`，`quota_type` 为“大额申购（含定投和转换转入）”。

## 认证边界

当前实测 `fundList`、`overreview` 和交易状态 API 均可匿名访问，
`auth_boundary()` 返回 `public`。如果接口未来返回 HTTP 401/403 或明确认证业务
码，适配器返回 `official_interface_requires_auth`，不会尝试猜 Cookie、绕过验证码、
伪造设备签名或使用银行卡信息。

## 代码和测试

- 实现：[southern.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/southern.py)
- 兼容入口：[product_pages.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/product_pages.py)
- 单元测试：[test_southern_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_southern_adapter.py)

```bash
python3 -m pytest -q tests/test_southern_adapter.py
```

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额/状态**——`016453=10元`，并取得开放申购/赎回/定投状态（南方官网当前交易状态 API）。
- 只有本 Adapter 本次从南方官网交易状态 API 返回的当前字段，才写入“已获取”；接口空结果、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
