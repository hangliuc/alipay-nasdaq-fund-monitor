# 国泰基金 Adapter

## 结论

国泰基金网上基金超市的公开产品页可以直接读取直销限额。单个产品页还包含基金基本资料、风险等级、成立日期、净值日期等字段；页面侧栏同时公开当前基金超市目录，适合发现全量基金。

当前页面没有可确认的“开放申购/暂停申购”状态字段。Adapter 不会把“申购”导航文字当作交易状态；交易状态缺失时返回 `official_api_no_data`/未知状态。

## 官方来源

| 用途 | 官方 URL | 说明 |
| --- | --- | --- |
| 基金超市/目录 | [国泰官网基金超市](https://e.gtfund.com/Etrade/Jijin/view/id/160213) | 页面侧栏列出产品代码、名称和产品页链接 |
| 产品页 | `https://e.gtfund.com/Etrade/Jijin/view/id/{code}` | 直销限额和产品基本资料 |

以上是国泰基金官方域名，不使用第三方平台。

## 基金目录字段

Adapter 从超市页面侧栏解析 `/Etrade/Jijin/view/id/{code}` 链接：

| 字段 | 含义 |
| --- | --- |
| `code` | 六位基金代码 |
| `name` | 基金名称/份额名称 |
| `share_class` | 从名称末尾 A/B/C 等份额后缀识别 |
| `source_url` | 国泰官方产品页 |

目录页面实测可发现 563 只去重后的基金份额；实际数量以每次抓取结果为准。

## 产品字段

| 标准字段 | 官方页面字段/来源 |
| --- | --- |
| 基金名称 | 页面 `gtOptions.fundname` 或产品摘要 |
| 基金全称 | “基金全称” |
| 基金类型 | “基金类型” |
| 风险等级 | “风险等级” |
| 成立日期 | “基金合同生效日” |
| 基金经理 | “基金经理” |
| 最新净值 | 产品摘要“单位净值”前的数值 |
| 净值日期 | 产品摘要中的 `MM-DD` |
| 原始字段 | `ProductSnapshot.fields` |

## 直销限额规则

产品页可能显示：

- `直销累计日限额`
- `直销单笔限额`

计算规则：

1. 优先使用 `直销累计日限额`。
2. 页面没有累计日限额时，退回 `直销单笔限额`。
3. 两者都没有时返回 `official_api_no_data`，不推断为“不限”。

| 页面结果 | `limit` | `quota_type` | `status` |
| --- | --- | --- | --- |
| 有累计日限额 | 格式化金额 | `累计日限额` | `ok` |
| 只有单笔限额 | 格式化金额 | `单笔限额` | `ok` |
| 没有直销限额字段 | `null` | 空 | `official_api_no_data` |

当前产品页未发现明确的第三方/代销限额字段，因此 Adapter 不会伪造第三方限额结果；该字段应保持“未获取”。

## 交易状态和认证边界

`TradeSnapshot` 会保留产品页快照，但 `subscription` 仅在官网明确提供状态时才填写。当前实测页面未提供可确认的当前申购开放/暂停状态，因此不做推断。

目录和产品页可匿名访问，`auth_boundary()` 返回 `public`。Adapter 不访问登录交易表单、不提交订单，也不绕过验证码、设备签名或银行卡绑定。

## 代码和测试

- 实现：[guotai.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/guotai.py)
- 兼容入口：[product_pages.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/product_pages.py)
- 单元测试：[test_guotai_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_guotai_adapter.py)

```bash
python3 -m pytest -q tests/test_guotai_adapter.py
```

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`160213=50元`（国泰官网直销产品页）。
- 只有本 Adapter 本次从国泰官网产品页解析出的明确直销限额，才写入“已获取”；状态字段缺失、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据；第三方/代销限额不冒充直销。
