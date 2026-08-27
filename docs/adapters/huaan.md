# 华安基金 Adapter

## 结论

华安官网产品页直接展示“单日单账户限额直销 X 元、代销 Y 元”，同时展示申购、赎回、定投状态。该页面适合直接生成直销限额和第三方限额两列结果。

当前实现只访问华安官方域名，不使用第三方平台，不需要账号、密码、验证码或 Token。

## 官方来源

| 用途 | 官方 URL | 说明 |
| --- | --- | --- |
| 基金目录 | [华安基金首页](https://wap.huaan.com.cn/) | 首页公开基金表通过 `viewFund(code)` 列出产品 |
| 产品页 | `https://wap.huaan.com.cn/funds/{code}/index.shtml` | 产品信息、交易状态、直销/代销限额 |

示例：[014978 华安纳斯达克100ETF联接 C](https://wap.huaan.com.cn/funds/014978/index.shtml)

## 基金目录字段

Adapter 从首页公开的 `viewFund('基金代码')` 链接中发现基金：

| 字段 | 含义 |
| --- | --- |
| `code` | 六位基金代码 |
| `name` | 基金名称/份额名称 |
| `share_class` | 从名称末尾 A/B/C 等后缀识别 |
| `source_url` | 华安官方产品页 |

目录结果按代码去重，实际数量以每次官网抓取结果为准。

## 产品字段

标准化为 `ProductSnapshot`：

| 标准字段 | 页面字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | 法定名称/基金简称 | `str` | 当前基金或份额名称 |
| `full_name` | 法定名称 | `str` | 基金全称 |
| `code` | 基金代码 | `str` | 六位基金代码 |
| `inception_date` | 成立日期 | `str` | 统一为 `YYYY-MM-DD` |
| `fund_type` | 基金类型 | `str` | 官网基金类型 |
| `risk_level` | 产品风险等级 | `str` | 官网风险文本 |
| `fund_manager` | 基金经理 | `str` | 官网当前基金经理 |
| `asset_scale` | 最新规模 | `str` | 官网原始规模值 |
| `net_value_date` | 最新净值日期 | `str` | 当前净值日期 |
| `trade_status` | 当前交易状态 | `str` | 开放申购、限额申购、暂停申购等 |
| `fields` | 页面解析字段 | `dict` | 保留直销限额、代销限额及其他原始字段 |

## 直销和第三方限额

产品页典型文本：

```text
单日单账户限额直销100元 代销10元
```

解析结果：

| 页面字段 | Adapter 字段 | 示例 |
| --- | --- | --- |
| `直销` | `direct_limit` / `DirectLimitSnapshot.limit` | `100元` |
| `代销` | `distribution_limit` / `raw["distribution_limit"]` | `10元` |
| 限额类型 | `quota_type` | `单日单账户限额` |

`fetch_channel_limits()` 会同时返回直销和代销限额，便于三列结果展示。没有明确金额时返回 `null`，不猜测为“不限”。

## 交易状态

产品页公开的交易状态会映射为：

| 页面文本 | 标准字段 |
| --- | --- |
| `开放申购` / `限额申购` | `subscription=True` |
| `暂停申购` | `subscription=False` |
| `开放赎回` / `暂停赎回` | `redemption=True/False` |
| `开放定投` / `暂停定投` | `sip=True/False` |

“限额申购”表示申购功能开放但存在金额限制，不会被当作暂停。

## 认证边界

当前华安首页和产品页可匿名访问，`auth_boundary()` 返回 `public`。Adapter 不访问登录交易表单、不提交订单，也不绕过验证码、设备签名或银行卡绑定。

## 代码和测试

- 实现：[huaan.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/huaan.py)
- 兼容入口：[product_pages.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/product_pages.py)
- 单元测试：[test_huaan_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_huaan_adapter.py)

```bash
python3 -m pytest -q tests/test_huaan_adapter.py
```

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`014978=100元`（华安官网产品页明确“直销 100 元”；代销 10 元单独保留）。
- 只有本 Adapter 本次从华安官网产品页解析出的直销字段，才写入“已获取”；页面无字段、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
