# 华夏基金 Adapter

## 结论

华夏官网提供公开的基金目录 JSON、单只基金产品页和统一交易状态表。当前 Adapter 将三者组合为独立数据源：目录负责发现基金，产品页负责当前交易状态和产品字段，交易状态表负责申购限制说明。

全链路无需账号、密码、验证码、设备签名或银行卡；如果未来官网返回认证状态，适配器会记录认证边界，不绕过认证。

## 官方来源

| 用途 | 官方 URL | 作用 |
| --- | --- | --- |
| 基金产品目录页 | [华夏基金产品页](https://fund.chinaamc.com/jjcp/) | 目录前端入口 |
| 基金目录 API | `https://fund.chinaamc.com/front/front/es/fundInfo/fundList` | 当前公开基金列表，适配器请求 `pageSize=2000` |
| 基金产品页 | `https://www.chinaamc.com/fund/{code}/index.shtml` | 基金名称、类型、风险、净值日期、交易状态等 |
| 统一交易状态表 | [getCalendar](https://fund.chinaamc.com/ProductForWeb/getCalendar) | 申购状态、单日单账户累计限制和适用范围 |

以上均为华夏基金官方域名，不使用第三方基金平台数据。

## 可获取字段

### 目录 API

| 字段 | 含义 |
| --- | --- |
| `fundCode` | 基金代码 |
| `fundName` / `fundAliasName` | 基金名称/简称 |
| `fundType` | 官网原始类型码 |
| `riskLevel` / `riskLevelText` | 风险码/风险文本 |
| `tradeState` | 官网交易状态码 |
| `buildDate` | 成立日期 |
| `manager` | 基金经理 |

目录接口返回的其他字段会保留在原始响应中；适配器不对类型码做未经官方说明的转换。

### 产品页

标准化为 `ProductSnapshot`：

| 标准字段 | 页面字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | 基金名称/简称 | `str` | 当前基金或份额名称 |
| `full_name` | 基金全称 | `str` | 法定基金全称 |
| `code` | 基金代码 | `str` | 六位基金代码 |
| `fund_type` | 基金类型 | `str` | 官网基金类型 |
| `risk_level` | 风险等级 | `str` | 风险文本或原始码 |
| `inception_date` | 基金合同生效日 | `str` | 统一为 `YYYY-MM-DD` |
| `net_value` | 最新净值 | `str` | 官网当前净值 |
| `net_value_date` | 净值日期 | `str` | 最新净值日期 |
| `trade_status` | 当前交易状态 | `str` | 例如“开放申购”“暂停申购” |
| `fields` | 产品页标签字段 | `dict` | 保留产品页原始字段 |

### 交易状态与直销限额

`TradeSnapshot` 当前输出“个人客户 / 华夏官网公开交易状态”渠道记录。统一状态表中的“所有投资人、所有渠道”限制按当前产品规则作为直销结果使用。

| 官网返回 | `limit` | `status` |
| --- | --- | --- |
| 含“暂停” | `暂停` | `ok` |
| 含“单日单账户累计 1 万元”等金额 | 格式化金额，例如 `1万元` | `ok` |
| 开放但没有金额限制 | `null` | `official_api_no_data` |
| 找不到对应代码且产品页也不是暂停 | `null` | `official_api_no_data` |

开放状态没有金额时不会推断为“不限”。“产品页交易状态”与“统一状态表”均记录抓取时间和来源 URL；状态表的页面更新时间若滞后，报告中应以观察时间和页面内容同时审计。

## 认证边界

当前实测目录 API、产品页和交易状态表均可匿名访问，`auth_boundary()` 返回 `public`。适配器不访问交易输入页、不提交订单，也不尝试绕过未来可能出现的登录或验证码。

## 代码和测试

- 实现：[chinaamc.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/chinaamc.py)
- 兼容入口：[product_pages.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/product_pages.py)
- 单元测试：[test_chinaamc_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_chinaamc_adapter.py)

```bash
python3 -m pytest -q tests/test_chinaamc_adapter.py
```

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销状态/限额**——`015300=暂停`、`024239=1万元`、`002891=1万元`（华夏官网公开交易状态表；以本次抓取结果为准）。
- 只有本 Adapter 本次从华夏官网产品页/状态表解析出 `status=ok` 的当前值，才写入“已获取”；开放但无金额、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
