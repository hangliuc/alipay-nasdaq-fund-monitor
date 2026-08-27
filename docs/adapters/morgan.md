# 摩根基金 Adapter

## 范围

`MorganAdapter` 只读取摩根基金管理（中国）有限公司（`cifm.com`）及其
官方电子直销入口公开的只读内容，不登录、不提交交易、不伪造设备签名，
也不使用第三方基金平台。实现文件为
[`morgan.py`](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/morgan.py)。

## 官方来源

| 用途 | 官方来源 | 读取内容 |
| --- | --- | --- |
| 基金目录 | [基金超市](https://www.cifm.com/fund/) | 产品/份额链接、代码、名称和目录分类 |
| 产品资料 | [产品页示例（019173）](https://www.cifm.com/fund/019173/) | 基金简称、法定名称、类型、风险等级、成立日期、电子直销入口，以及页面可见的购买按钮 |
| 净值补充 | `https://www.cifm.com/images/week/net_value_week.xml` | 官网静态 XML 中匹配代码的最新净值、累计净值、日期和基金状态（可选） |
| 公告检索 | [官网搜索](https://www.cifm.com/web/search) | 基金公告标题、公告日期和附件 URL |
| 电子直销入口 | [网上交易登录](https://etrade.51fund.com/etrading/account/login/init) | 仅记录为产品页公开入口，不执行交易 |

公告附件仅在官网搜索结果或公告页给出官方 URL 时读取；PDF 使用本地文本
提取。公告中的份额代码列与限额列按列序匹配，避免把同一基金的其他份额
金额错配。

## 输出与保留字段

### `discover_funds()`

解析基金超市中的 `/fund/{code}/` 链接并按代码去重，输出
`FundIdentity(manager_id="摩根", code, name, fund_type, share_class,
source_url, source_type)`。支持人民币/美元、A/B/C 等名称中的份额标记；
目录没有公开类型时保持空字符串，不猜测。

### `fetch_product()`

产品页表格字段全部进入 `ProductSnapshot.fields`，并附加 `基金代码`、
`基金简称`、`电子直销交易入口`、`购买按钮状态`、`定投按钮状态` 等结构化
字段。若净值 XML 可用，还附加 `最新净值`、`最新累计净值`、`最新净值日期`、
`官网基金状态` 和 `净值数据源URL`。快照始终带有产品页 `source_url` 与
`observed_at`。

页面购买按钮只表示“官网入口可见”，不是已登录账户可以下单；按钮状态的
枚举为 `open`、`closed`、`unknown`。产品页没有可确认的交易状态时，
`trade_status` 为“官网产品页未公开当前交易状态”。

### `fetch_trade_status()`

摩根产品页没有匿名的独立交易状态 API，当前输出一条
`customer_type="individual"`、`channel="摩根基金官网电子直销入口"` 的
`ChannelTradeStatus`。`subscription` 只由页面购买按钮映射为 `true`、
`false` 或 `null`；赎回和定投没有公开状态则保持 `null`。页面原始字段进入
`TradeSnapshot.raw`，不从导航文字推断交易开放。

### `fetch_direct_limit()` / `fetch_direct_sales_record()`

1. 公告标题或正文明确含“直销渠道”时优先使用该公告；同一口径按公告日期
   取最新记录。
2. 依据公告中的“下属分级基金的交易代码”和“限制申购金额”列，按当前份额
   代码取金额。人民币金额统一为 `元`，公告明确的美元份额保留为 `美元`；
   “暂停”保留为 `limit="暂停"`、`status="paused"`，“不限”保留为
   `limit="不限"`、`status="ok"`。
3. 无法按当前代码解析时输出 `limit=None`、`status="official_api_no_data"`，
   不把缺失字段猜测为不限。

结果保留公告标题、日期、公告/附件 `source_url`、限额口径和适用范围原文，
并由 `fetch_direct_sales_record()` 映射为项目通用的
`direct_sales_*` 字段。

## 认证边界与匿名可用性

在 2026-08-26 的官网页面探测中，基金超市、产品页和官网公告页面可匿名
查看，产品页明确展示官方电子交易登录入口。电子直销交易本身要求账户登录；
官网图表使用的 `ecmob.cifm.com` 产品详情接口需要
`tk-trans-signature`，本 Adapter 不调用该受保护接口，也不尝试生成签名。

因此 `auth_boundary()` 返回：

- `status="public_with_trade_auth_boundary"`
- `requires_login=True`
- `requires_device_signature=True`
- `requires_captcha=False`、`requires_bank_card=False`（适配器未观察到匿名
  产品/公告读取需要这两项）

若目录、产品页、公告搜索或附件未来返回 HTTP 401/403，Adapter 会抛出明确
的认证边界错误；公告限额快照会使用 `official_interface_requires_auth`，
不使用 Cookie、验证码、银行卡或第三方数据绕过限制。

净值 XML 只是可选补充。若该静态文件不存在、返回认证错误或解析失败，产品
快照仍返回产品页字段，并明确缺少净值补充字段。

## 当前 config 基金实测（2026-08-26）

| 基金代码 | 目录/产品页 | 直销限额 | 官方来源 |
| --- | --- | --- | --- |
| `019173` | 产品页与公告搜索可匿名访问 | `300元` | [2026-07-09 直销渠道公告 PDF](https://www.cifm.com/fund/019172/announce/202607/P020260709541510891782.pdf) |

同日官网基金超市目录去重发现 299 只基金/份额；`019173` 产品页购买入口可见，
申购状态为正常开放，赎回和定投状态未公开。限额为最新可解析的官网公告值，
不是未登录交易接口实时返回值；电子直销下单仍需要登录。

## 测试

单测覆盖目录去重、产品字段与净值 XML、公告搜索解析、人民币/美元限额列、
直销公告优先级、无数据状态和认证边界：

```bash
python3 -m pytest -q tests/test_morgan_adapter.py
```

本 Adapter 没有改动共享注册表或编排入口。

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`019173=300元`（摩根官网 2026-07-09 直销渠道公告 PDF）。
- 只有本 Adapter 本次从摩根官网公告搜索及附件解析出的当前直销口径，才写入“已获取”；公告缺失、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
