# 汇添富基金（Valuetf）Adapter

## 适配器概览

| 项目 | 内容 |
|---|---|
| 管理公司 | 汇添富基金管理股份有限公司 |
| Adapter | `ValuetfAdapter` |
| 实现文件 | `fund_monitor/fetch/direct_sales/adapters/valuetf.py` |
| 测试文件 | `tests/test_valuetf_adapter.py` |
| 官方域名 | [99fund.com](https://www.99fund.com/) |
| 目录入口 | [`/main/products/jijinhb/index.shtml`](https://www.99fund.com/main/products/jijinhb/index.shtml) |
| 产品页模板 | [`/main/products/pofund/{code}/fundgk.shtml`](https://www.99fund.com/main/products/pofund/018967/fundgk.shtml) |
| 基本信息页模板 | [`/main/products/pofund/{code}/fundinfo.shtml`](https://www.99fund.com/main/products/pofund/018967/fundinfo.shtml) |
| 公告页模板 | [`/main/products/pofund/{code}/fundgg.shtml`](https://www.99fund.com/main/products/pofund/018967/fundgg.shtml) |
| 数据性质 | 基金公司官网公开页面及官网公告附件；不使用第三方平台 |

## 可获取字段

### 基金目录字段

目录页面是服务端渲染的基金产品中心，当前按基金份额代码列出基金。适配器解析全部 `tr#六位代码`，不是只读取 `config.json`。

| 标准字段 | 官网字段/位置 | 说明 |
|---|---|---|
| `code` | 表格第一列、行 `id` | 六位基金/份额交易代码 |
| `name` | 基金名称列链接 | 官网当前基金简称，含 A/C/D/Y 等份额后缀 |
| `share_class` | 名称末尾份额字母 | 仅在名称明确以份额字母结尾时提取 |
| `source_url` | 基金名称链接 | 转为官方产品概况页绝对 URL |
| `source_type` | 适配器常量 | `valuetf_official_fund_catalogue_html` |
| `目录原始字段` | 更新日期、单位净值、累计净值、状态、网上交易 | 由 `parse_catalogue_rows()` 结构化保留，便于审计 |

### 产品资料和净值字段

资料主要取 `fundinfo.shtml`，净值和交易按钮补充取 `fundgk.shtml`。

| 标准字段 | 官网字段/位置 | 说明 |
|---|---|---|
| `name` | `h1.H1Pro`、基本信息“基金简称” | 基金简称/份额名称 |
| `full_name` | `table.infotable` 的“基金全称” | 基金法律文件全称 |
| `fund_type` | “基金类型” | 官网原始分类 |
| `risk_level` | “产品风险等级” | 如 `中高风险(R4)` |
| `inception_date` | “成立日期” | 统一为 `YYYY-MM-DD` |
| `asset_scale` | 页面公开时的“基金规模” | 页面未公开时为空，不以第三方补齐 |
| `net_value_date` | 概况页“净值日期” | 统一为 `YYYY-MM-DD` |
| `fields` | `infotable` 全部标签值 + 概况页净值字段 | 保留原始字段，便于后续开源复用 |
| `trade_status` | 概况页购买/定投按钮 | 例如 `购买可用；定投不可用` |
| `最新净值` | 概况页“基金净值” | 官网页面公开的最新单位净值 |
| `购买按钮状态` | `#buy` 交易入口 | `open` / `closed` / `unknown` |
| `定投按钮状态` | `#aip` 或 `operateType=1` 入口 | `open` / `closed` / `unknown` |
| `购买入口`、`定投入口` | `trade.99fund.com` 链接 | 仅记录公开入口，不跟随提交交易 |

### 直销限额字段

汇添富产品页本身公开的是交易入口，当前限额来自该产品官网公告页中最新一条相关公告的官方 PDF；公告 PDF 按“交易代码”排列不同份额的限制金额。

| 标准字段 | 来源/解析规则 | 说明 |
|---|---|---|
| `limit` / `direct_sales_limit` | PDF“下属基金份额的限制申购金额” | 按当前 `code` 的列序提取，示例 `10元`、`300元` |
| `status` / `direct_sales_status` | PDF暂停表和公告标题 | `ok`、`paused`；暂停且无金额时输出 `暂停` |
| `quota_type` | 公告表格 | `官网公告限制申购金额` |
| `quota_remark` | 解析规则说明 | 明确是否按交易代码表读取或公告明确暂停 |
| `announcement_title` | 公告页标题列 | 保存最新相关公告标题 |
| `announcement_date` | 公告页发布日期列 | 统一为 `YYYY-MM-DD` |
| `source_url` | 公告 PDF 下载地址 | `https://www.99fund.com/announcement/...pdf` |
| `raw` | PDF 解析结果 | 保存公告文本、金额表和来源元数据 |

### 交易快照字段

| 字段 | 内容 |
|---|---|
| `customer_type` | `individual` |
| `channel` | `汇添富官网产品页` |
| `subscription` | 购买按钮可见/可用状态 |
| `sip` | 定投按钮可见/可用状态 |
| `redemption` | 官网公开产品页未提供时为 `None` |
| `raw` | 产品页全部结构化字段 |

## 公告追踪规则

1. 请求当前基金的 `fundgg.shtml`，只保留标题含“大额申购”“大额定投”“定期定额投资”“限制申购”“暂停申购”或“恢复申购”的公告。
2. 按官网发布日期倒序处理，下载每条公告的官网 PDF 附件。
3. 解析 PDF 的“下属基金份额的交易代码”“限制申购金额”“是否暂停上述业务”表格，并按代码下标映射金额，不把主代码或其他份额的金额套用到当前份额。
4. 最新暂停公告没有金额但明确当前份额暂停时，返回 `limit=暂停`、`status=paused`；解析失败则继续尝试更早的相关公告。
5. 没有可解析的当前公告时返回 `official_api_no_data`，不使用历史结果或第三方数据推断。

## 认证边界

| 页面/接口 | 匿名状态 | 处理 |
|---|---|---|
| 官网基金目录 | 可匿名访问 | 解析全量目录 |
| 官网产品资料/净值页 | 可匿名访问 | 解析字段、净值、交易按钮 |
| 官网公告页和 PDF | 可匿名访问 | 追踪当前限制 |
| `trade.99fund.com` 申购/定投提交页 | 可能要求登录、验证码、银行卡绑定 | 只保存入口 URL，不访问或绕过 |

## 2026-08-26 匿名实测

| 项目 | 结果 |
|---|---:|
| 官网当前目录份额 | 824 |
| `018967` 产品页 | 可访问；购买可用；最新净值日期 `2026-08-24` |
| `018967` 最新相关公告 | 2026-07-17 官方 PDF；限制申购金额 `10元` |
| `015202` 产品页 | 可访问；购买可用；最新净值日期 `2026-08-24` |
| `015202` 最新相关公告 | 2026-08-17 官方 PDF；人民币 C 份额暂停申购/定投，结果 `暂停` |
| 单元测试 | 5 项通过 |

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销状态/限额**——`018967=10元`、`015202=暂停`（汇添富官网产品页公告 PDF）。
- 只有本 Adapter 本次从汇添富官网公告页/PDF 解析出的当前值，才写入“已获取”；公告无数据、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
