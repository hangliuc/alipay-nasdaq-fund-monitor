# 易方达基金官网 Adapter 说明

## 1. 状态

- 模块：`fund_monitor/fetch/direct_sales/adapters/efunds.py`
- 管理人：易方达基金管理有限公司
- 当前版本：第一版，已完成离线单元测试（8 个场景）
- 网络策略：只读官网公开页面/API；不登录、不提交交易、不绕过验证码、设备签名或银行卡绑定。

本 Adapter 已从旧的 `_fetch_efunds` 逻辑中独立出来，旧日报入口仍通过兼容方法调用它，不改变现有卡片流程。

## 2. 官方来源与字段

### 2.1 基金目录

- 入口：[易方达基金超市](https://vip.efunds.com.cn/)
- 解析对象：`table#allFundTable`

| 标准字段 | 页面字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `code` | 目录行六位基金代码 | `str` | 基金/份额代码 |
| `name` | 目录行基金名称 | `str` | 当前公开名称 |
| `fund_type` | 目录行基金类型 | `str` | 官网基金类型 |
| `share_class` | 名称末尾后缀 | `str` | A/B/C 等份额类别 |
| `source_url` | 目录行详情页 URL | `str` | 易方达官方产品页 |
| `source_type` | 固定值 | `str` | 官网基金超市目录 |

Adapter 只采信包含六位基金代码的目录行，并按代码去重。这个目录代表官网当前公开基金超市结果，不能自动推断为历史上所有存续基金；全量运行必须记录发现数量、去重数量和未发现项。

### 2.2 产品页

- URL 模板：`https://www.efunds.com.cn/fund/{code}.shtml`
- 示例：[012870 产品页](https://www.efunds.com.cn/fund/012870.shtml)

| 标准字段 | 页面字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | 基金名称/基金简称 | `str` | 当前基金或份额名称 |
| `full_name` | 基金全称 | `str` | 法定基金全称 |
| `code` | 基金代码 | `str` | 六位基金代码 |
| `fund_type` | 基金类型 | `str` | 官网基金类型 |
| `risk_level` | 风险等级 | `str` | 官网风险文本 |
| `inception_date` | 成立日期 | `str` | 统一为 `YYYY-MM-DD` |
| `manager` | 基金管理人 | `str` | 管理人名称 |
| `fund_manager` | 基金经理 | `str` | 当前基金经理 |
| `trustee` | 基金托管人 | `str` | 当前托管人 |
| `asset_scale` | 基金规模/资产规模 | `str` | 官网原始规模值 |
| `net_value_date` | 基金净值日期 | `str` | 最新净值日期 |
| `trade_status` | 产品页交易提醒 | `str` | 暂停申购、开放申购等 |
| `fields` | 产品页解析字段 | `dict` | 保留其他原始标签字段 |

产品页用于产品信息和交易提醒；直销渠道限额以交易状态 API 为准。

### 2.3 当前交易状态 API

- URL 模板：`https://api.efunds.com.cn/xcowch/front/fund/tradestatus/{code}`
- 参数：`date=YYYY-MM-DD`
- 示例：[012870 交易状态 API](https://api.efunds.com.cn/xcowch/front/fund/tradestatus/012870?date=2026-08-26)

返回结构：`status`、`message`、`data.individual[]`、`data.organization[]`。

| 字段 | 含义 |
|---|---|
| `agencyName` | 网上直销、直销中心、非直销机构 |
| `subscription` | 是否开放申购 |
| `redemption` | 是否开放赎回 |
| `transferIn` / `transferOut` | 转换转入/转出状态 |
| `sip` | 定投状态 |
| `limit` | 大额申购/转换/定投限制 |
| `quotaType` / `quotaRemark` | 限额口径/说明 |
| `*OpenRemark` / `*SuspendRemark` | 对应业务说明 |

## 3. 直销限额口径

固定选择：`data.individual[].agencyName == "网上直销"`。

1. 找不到该行：`status=official_api_no_data`，不沿用旧值，也不写成“不限”。
2. `subscription == false`：结果为 `暂停`。
3. 申购开放时，优先解析 `limit`，其次解析 `quotaRemark`。
4. 空值、`-`、`不限`、`无限`：在申购确实开放时记为 `不限`。
5. 纯数字按 API 约定解释为人民币元，例如 `100000` → `10万元`。
6. 同时保留个人/机构和全部销售渠道的原始快照。

## 4. 输出对象

| 对象 | 结构化字段 | 用途 |
| --- | --- | --- |
| `FundIdentity` | `manager_id`、`code`、`name`、`fund_type`、`share_class`、`source_url`、`source_type` | 目录发现结果，供产品页/API 调用 |
| `ProductSnapshot` | `name`、`full_name`、`fund_type`、`risk_level`、`inception_date`、`asset_scale`、`net_value_date`、`trade_status`、`fields` | 产品页快照 |
| `TradeSnapshot` | `channels[]`、`api_status`、`message`、`source_url`、`observed_at`、`raw` | 个人/机构全部渠道和原始 payload |
| `DirectLimitSnapshot` | `customer_type`、`channel`、`limit`、`status`、`quota_type`、`quota_remark`、`source_url`、`observed_at`、`raw` | 直销限额和审计信息 |

## 5. 认证边界

当前实测交易状态 API 可公开返回个人/机构及渠道行，未观察到登录、验证码、设备签名或银行卡绑定，因此 `auth_boundary()` 返回 `public`。

这不等于下单接口无需认证。Adapter 不调用登录和下单接口。若未来返回认证错误，应记录 `official_interface_requires_auth`，不能绕过或复用不明 Cookie。

## 6. 当前未纳入

- 历史净值序列和收益率；
- 全量公告列表及 PDF 解析；
- 关闭/清盘/历史基金的完整生命周期目录；
- 登录后账户级可购买额度。

这些能力可在第二阶段增加，但不能降低当前 API 空数据和认证边界的状态等级。

## 7. 单元测试

测试使用固定 HTML/JSON fixture，不访问网络：

```bash
python3 -m pytest -q tests/test_efunds_adapter.py
```

覆盖：目录过滤和去重、产品字段、暂停申购、个人/机构全部渠道、开放申购限额、缺少网上直销行、认证边界及 `config.json` 兼容输入。

## 8. 建议监控指标

`universe_discovered`、`universe_deduplicated`、`product_page_ok`、`trade_api_ok`、`direct_channel_rows`、`direct_limit_ok`、`official_api_no_data`、`official_interface_requires_auth`、`network_error`。

只有在 API 成功、存在个人客户“网上直销”行且 `observed_at` 属于本次运行时，才允许标记为当前直销状态。

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销状态**——`012870=暂停`、`161128=暂停`、`012922=暂停`（易方达网上直销交易状态 API）。
- 只有本 Adapter 本次从 API 返回并识别出“网上直销”行的明确值，才写入“已获取”；缺少直销行、空字段、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
