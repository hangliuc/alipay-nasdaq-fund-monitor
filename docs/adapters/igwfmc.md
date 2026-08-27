# 景顺长城基金（IGWFMC）官方 Adapter

## 适配器状态

| 项目 | 内容 |
|---|---|
| 管理人 | 景顺长城基金管理有限公司（景顺长城） |
| 代码模块 | `fund_monitor.fetch.direct_sales.adapters.igwfmc.IgwfmcAdapter` |
| 兼容别名 | `IGWFMCAdapter` |
| 公开基金目录 | [景顺长城基金产品](https://www.igwfmc.com/main/jjcp/product.html) |
| 产品页模板 | `https://www.igwfmc.com/main/jjcp/product/{code}/detail.html` |
| 公告入口 | [景顺长城基金公告](https://www.igwfmc.com/main/zxzx/info-4.html) |
| 登录交易入口 | [景顺长城网上交易登录](https://www.igwfmc.com/fundorg/login/login.html)（不访问、不提交） |
| 直销渠道口径 | 官网产品页和公告正文明确写出的“本公司直销中心/直销渠道/网上直销” |
| 第三方来源 | 不使用 |

所有来源均限制为 `igwfmc.com` 官方域名及其官方静态资源。适配器不登录、不下单，
不猜测 Cookie、验证码、设备签名或银行卡绑定信息。

## 可获取字段

### 基金目录

`discover_funds()` 访问产品目录页，解析指向 `/main/jjcp/product/{code}/detail.html`
的产品链接；页面若以“基金名称/代码：六位代码”形式输出，也支持文本兜底。相同份额
代码去重并按代码排序。

| 标准字段 | 官网位置/规则 |
|---|---|
| `code` | 产品链接中的六位代码或目录文本“基金代码” |
| `name` | 产品链接/目录行中的基金简称 |
| `fund_type` | 目录行中的股票型、混合型、债券型、QDII、FOF 等原始文本 |
| `share_class` | 名称末尾的 A/B/C 等份额后缀 |
| `source_url` | 景顺长城官方产品详情页 |
| `source_type` | `igwfmc_official_fund_catalogue_page` |

目录行中的风险、净值日期、交易状态等原始文本会保存在适配器的内部目录快照中，
产品标准字段之外的页面字段则保存在 `ProductSnapshot.fields` 或 `raw` 中。

### 产品页与交易状态

产品页公开的概况表和摘要中，适配器保留全部可识别的键值字段，包括基金全称、基金
类型、风险等级、成立日期、基金规模、基金经理、净值日期、申购状态、赎回状态等。
日期统一为 `YYYY-MM-DD`；金额和费率只保留官网原文，不将“起购金额”转换为大额
限额。

`fetch_trade_status()` 仅读取产品页明确展示的“申购状态”和“赎回状态”：

| 官网文本 | 输出 |
|---|---|
| 开放、正常、可申购/可购买 | `True` |
| 暂停、关闭、封闭、不可用 | `False` |
| 页面缺少字段或文本无法判断 | `None` |

交易快照的 `channel` 为 `景顺长城官网产品页`，`raw` 保留页面字段，
`source_url` 和 `observed_at` 分别记录官方 URL 与抓取时刻。产品页显示“开放”不代表
交易已经提交成功，也不绕过网上交易登录。

### 直销限额与公告

`fetch_direct_limit()` 先抓取目标产品页，再按官网产品页公告入口筛选标题含“大额申购”、
“大额定投”、“定期定额投资”、“限制申购”、“暂停申购”或“恢复申购”，且含暂停、
调整、恢复或限制语义的公告。公告详情可以是 HTML，也可以是官方 PDF。

解析优先级如下：

1. 公告正文明确写出“本公司直销中心/直销渠道/网上直销”的金额；
2. 公告写明“所有销售机构（含各代销机构及本公司直销中心）”的金额；
3. 公告按“下属分级基金的交易代码”和“限制申购金额”列出表格时，按目标份额代码
   对齐金额；人民币元才转换为项目标准金额，美元份额不会静默换算成人民币；
4. 公告明确恢复或暂停大额申购/定投但没有金额时，分别输出 `不限` 或 `暂停`。

`DirectLimitSnapshot` 的状态枚举如下：

| 情形 | `status` | `limit` |
|---|---|---|
| 官网公告解析到当前金额 | `ok` | 例如 `1000元` |
| 官网公告明确恢复大额业务 | `ok` | `不限` |
| 官网公告明确暂停大额业务 | `ok` | `暂停` |
| 页面可访问但无可验证当前公告/限额 | `official_api_no_data` | `null` |
| 官网响应 401/403 或公告查询要求认证 | `official_interface_requires_auth` | `null` |

`raw` 保留产品页字段、公告标题、公告日期、正文文本、来源类型和官方 `source_url`。
开放产品没有公告时，不复用历史值、不把起购金额或费率表当成限额，也不猜测为“不限”。
`fetch_direct_sales_record()` 对 `official_api_no_data` 返回 `None`，其余状态保留同样的
`direct_sales_status`。

## 认证边界与实测说明

官网产品目录、产品详情和公告入口在公开网页中可访问；网上交易登录页明确要求个人/机构
登录并显示图形验证码。因此 `auth_boundary()` 返回：

```text
status=public_with_trade_auth_boundary
requires_login=true
requires_captcha=true
requires_device_signature=false
requires_bank_card=false
```

景顺长城公告入口的部分列表由前端动态加载。本实现只解析产品页/官方公告入口中实际返回
的官方详情或 PDF；没有可按目标代码验证的公告时使用 `official_api_no_data`。本实现没有
硬编码一个未经页面确认的公告 API，也没有为了取得交易状态而调用登录或下单接口。若未来
官网把公告列表或详情置于认证之后，代码会将 401/403 记录为
`official_interface_requires_auth`，不尝试绕过。

2026-08-26 官网实测：公开目录去重发现 495 只基金/份额；配置基金 `017093`
产品页可访问，但公告区没有返回能按当前代码验证的最新限额公告，因此 Adapter 返回
`official_api_no_data`。统一编排不会保留或回填旧公告值，因此该基金本次直销限额保持
未获取。该基金产品页同时显示申购关闭、赎回开放；交易状态可读与直销限额
是否可验证是两个独立字段。

## 测试

对应测试文件为 [`tests/test_igwfmc_adapter.py`](/Users/shareit/personal/qdii-quota-radar/tests/test_igwfmc_adapter.py)，覆盖目录去重、产品字段、公开申购/赎回状态、公告金额与份额代码对齐、暂停/恢复、无数据状态、原始字段保留和认证边界。

```bash
python3 -m unittest tests.test_igwfmc_adapter -v
```

## 直销获取结论（统一判定）

- `config.json` 目标：**未获取直销限额**——`017093` 产品页可读，交易状态为申购关闭/赎回开放，但当前官网公告未解析出可验证限额，Adapter 返回 `official_api_no_data`。
- 只有本 Adapter 本次从景顺长城官网公告/产品页解析出的明确当前直销限额或暂停/不限状态，才写入“已获取”；本次没有可验证限额，不能以旧公告值替代。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据；认证边界按 `official_interface_requires_auth` 记录。
