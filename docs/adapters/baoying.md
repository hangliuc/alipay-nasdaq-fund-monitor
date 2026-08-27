# 宝盈基金 Adapter

## 结论

宝盈有可匿名调用的官网网上交易 API。当前实现为独立的 `BaoyingAdapter`，不依赖支付宝、天天基金等第三方平台，也不要求账号、密码、验证码或银行卡。

匿名接口首先创建官网前端使用的 session，再查询交易页基金目录和单只基金详情。接口可能在会话失效时返回“会话超时”，此时适配器记录 `official_interface_requires_auth`，不会尝试绕过认证。

## 官方来源和请求

| 用途 | 官方 URL | 说明 |
| --- | --- | --- |
| 网上交易首页 | [ibao.byfunds.com](https://ibao.byfunds.com/) | 官网网上交易前端，建立 cookie/origin 上下文 |
| 匿名 session | `https://ibao.byfunds.com/agate/api/v1/common/session` | 前端公开建立 session，返回短期 `token` |
| 当前基金目录 | `https://ibao.byfunds.com/agate/api/v1/trade/fund/list` | 前端公开的分页列表；适配器请求 `pagecount=1000` 并记录返回的全部基金份额 |
| 基金详情和交易字段 | `https://ibao.byfunds.com/agate/api/v1/fund/detail?fundcode={code}` | 单只基金的净值、规模、状态和业务限额 |

请求中的 `_sign`、`_msgid`、`token` 和 `g_systemtype=2` 是官网前端已有的调用参数。适配器只复现公开请求，不发起任何交易。

## 可获取字段

### 基金目录

| 字段 | 含义 | 备注 |
| --- | --- | --- |
| `fundcode` | 基金代码 | 六位代码 |
| `fundname` / `fund_shortname` | 基金名称/简称 | 用于展示和匹配 |
| `fundtype` | 基金类型 | 保留官网原始类型码，不自行猜测中文含义 |
| `risklevel` | 风险等级 | 保留官网原始风险码 |
| `sharetype` | 份额类型 | 同时保留官网原始值；展示份额优先从名称后缀识别 |
| `fundstatus` | 基金状态 | 保留官网原始状态码 |
| `nav` / `navdate` | 最新净值/净值日期 | 日期统一为 `YYYY-MM-DD` |
| `fundsize` | 基金规模 | 保留官网返回的人民币数值 |
| `week1ud`、`month1ud`、`year1ud` 等 | 收益字段 | 原始字段全部保留 |
| `tano` | TA/登记机构标识 | 原始字段 |
| `fundbizflag` | 基金业务标识 | 原始字段 |
| `minamount` | 最低申购金额 | 不与申购限额混淆 |
| `td_sum_max_20` / `td_sum_max_22` | 申购业务限额字段 | 详情接口中的核心限额字段 |

目录接口返回的其他字段也会保留，不因未列在表中而丢弃。

### 详情和交易状态

| 输出对象 | 内容 |
| --- | --- |
| `ProductSnapshot` | 基金名称、全称、类型码、风险码、成立日期、资产规模、净值日期、官网交易状态 |
| `ProductSnapshot.fields` | 详情接口返回的全部原始字段，便于后续扩展和审计 |
| `TradeSnapshot` | 当前按“个人客户 / 宝盈网上交易”输出一条交易记录 |
| `ChannelTradeStatus.raw` | 该基金详情接口的原始交易字段 |

### 直销限额

#### 计算规则

1. 读取 `td_sum_max_20` 和 `td_sum_max_22`。
2. 忽略空值、非数字值和小于等于 0 的值。
3. 如果存在多个正值，取较小值作为当前申购限额。
4. 按人民币金额格式化，例如 `100000.00` 输出 `10万元`。

#### 结果状态

| 条件 | `limit` | `status` |
| --- | --- | --- |
| 至少一个字段有正数 | 格式化后的金额 | `ok` |
| 两个字段均为空、非数字或非正数 | `null` | `official_api_no_data` |
| 官网返回会话超时/需要认证 | `null` | `official_interface_requires_auth` |

空字段不会被推断为“不限”，避免把“官网没有返回限额”误报成无限额。

## 认证边界和状态

| 状态 | 含义 |
| --- | --- |
| `ok` | 官网详情成功，且至少一个申购限额字段为正数 |
| `official_api_no_data` | 详情成功但没有可解析的限额字段 |
| `official_interface_requires_auth` | 官网返回 `9999999`/“会话超时”等认证边界 |
| `official_api_error` | 其他非成功业务码或返回异常 |

“官网交易目录”代表当前网上交易页面公开的基金集合，不等同于历史上所有已发行或已清盘基金。运行时应记录发现数量和观察时间。

## 代码和测试

- 实现：[baoying.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/baoying.py)
- 兼容入口：[trade_api.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/trade_api.py)
- 单元测试：[test_baoying_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_baoying_adapter.py)

```bash
python3 -m pytest -q tests/test_baoying_adapter.py
```

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`019737=10元`（官网网上交易详情 API）。
- 只有本 Adapter 本次从宝盈官网公开接口解析出 `status=ok` 的直销字段，才写入“已获取”；会话超时、认证、空字段或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
