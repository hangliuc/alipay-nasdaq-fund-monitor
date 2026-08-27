# 招商基金（Cmfchina）官方 Adapter

实现文件：`fund_monitor/fetch/direct_sales/adapters/cmfchina.py`  
单元测试：`tests/test_cmfchina_adapter.py`  
管理人：招商基金管理有限公司（招商）

## 数据来源

| 来源类别 | 官方入口 | 用途 | 认证边界 |
|---|---|---|---|
| 全量基金目录 API | `https://common.cmfchina.com/ecwebbff/fund/fundInfo/queryAllFundList` | 当前公开基金/份额目录、基金名称、类型、净值摘要、风险码、交易状态摘要 | 当前可匿名访问；请求遵循招商 H5 的公开 JSON 请求头 |
| 产品详情 API | `https://common.cmfchina.com/ecwebbff/fund/fundInfo/queryFundInfoDetail`，参数 `fundId` | 最新净值、净值日期、基金类型、风险等级、成立日期、资产规模、购买/赎回/定投状态及原始详情字段 | 当前可匿名访问 |
| 产品交易规则 API | `https://common.cmfchina.com/ecwebbff/fund/fundInfo/queryFundTradeRule`，参数 `fundId` | 官网公开的最低申购、最低定投、费率和交易流程字段（适配器保留来源说明，未将最低申购额当作限额） | 当前可匿名访问；未用于伪造大额限额 |
| 产品页 | `https://www.cmfchina.com/web/fundDetail/{基金代码}/index.html` | 服务端渲染的基金名称、全称、净值、概况、购买/定投按钮、产品公告列表 | 当前可匿名访问 |
| 官方公告详情 | `https://www.cmfchina.com/web/noticedetails/{公告编号}/index.html` | 解析“调整/暂停/恢复大额申购（含定期定额投资）”正文及份额限额 | 当前可匿名访问 |

## 可获取字段

### 基金目录（`FundIdentity`）

| 字段 | 输出含义 | 解析来源 |
|---|---|---|
| `manager_id` | 固定为 `招商` | Adapter 常量 |
| `code` | 六位基金/份额代码 | 目录 API `data.fundInfoList[].fundId` |
| `name` | 当前基金/份额简称 | 目录 API `fundName` |
| `fund_type` | 基金类型中文名；未知类型保留官网类型码 | 目录 API `fundType`，适配器映射 0/1/2/3/4/5/6/7 |
| `share_class` | A、C、E、Y 等份额后缀 | 简称末尾大写字母 |
| `source_url` | 对应官网产品页 | `/web/fundDetail/{code}/index.html` |
| `source_type` | `cmfchina_official_public_fund_catalogue_api` | 固定来源标识 |

### 产品详情（`ProductSnapshot`）

| 字段 | 输出含义 | 解析来源 |
|---|---|---|
| `name` | 当前份额简称 | 产品详情 API `fundName` |
| `full_name` | 基金全称 | 产品页“基金全称” |
| `fund_type` | 基金类型中文名 | 详情 API `fundTypeDesc` |
| `risk_level` | 风险等级中文名，如中高风险 | 详情 API `fundRiskLevelDesc` |
| `inception_date` | 成立日期 | 详情 API `establishDate` |
| `asset_scale` | 资产规模摘要 | 详情 API `fundSum` |
| `net_value_date` | 最新净值日期 | 详情 API `fundNavInfoVoList[0].navDate` |
| `trade_status` | 购买、赎回、定投当前状态摘要 | 详情 API `tradeStatusVo` |
| `fields[API_*]` | 详情 API 返回的全部非空顶层字段，嵌套对象 JSON 化保留 | 详情 API `data` |
| `fields[购买按钮状态]` | `open`/`closed` | 产品页按钮或详情 API `canBuyFlag` |
| `fields[定投按钮状态]` | `open`/`closed` | 产品页按钮或详情 API `canMipFlag` |
| `fields[最新净值]` | 净值显示值 | 产品页/详情 API |
| `fields[产品公告条数]` | 产品页当前列出的公告数 | 产品页公告列表 |

### 交易状态（`TradeSnapshot`）

| 字段 | 输出含义 | 解析来源 |
|---|---|---|
| `customer_type` | `individual` | 官网公开个人交易入口口径 |
| `channel` | `招商基金官网产品页/官方详情 API` | 官网产品页及详情 API |
| `subscription` | 是否可购买 | `tradeStatusVo.canBuyFlag` 或购买按钮 |
| `redemption` | 当前适配器不猜测未展示值，通常为 `None` | 未登录交易提交页不访问 |
| `sip` | 是否可定投 | `tradeStatusVo.canMipFlag` 或定投按钮 |
| `raw` | 产品字段全量快照 | 详情 API + 产品页解析字段 |

### 直销限额（`DirectLimitSnapshot`）

| 字段 | 输出含义 | 解析规则 |
|---|---|---|
| `limit` | 当前直销机构公告口径的金额，如 `100元`、`100000元`、`不限` | 公告“下属分级基金的限制申购金额”按代码映射；恢复日已到则为 `不限` |
| `status` | `ok`、`paused`、`official_api_no_data` 或认证边界状态 | 由公告正文和请求结果决定 |
| `quota_type` | 公告限制、恢复或暂停状态 | 固定结构化分类 |
| `quota_remark` | 生效日期、份额映射和当前状态说明 | 公告正文 |
| `source_url` | 官方公告详情 URL | 产品页公告链接 |
| `raw` | 公告标题、日期、有效日、恢复日及原始解析字段 | 官方公告详情 |

## 限额获取规则

1. 访问对应基金官方产品页，读取产品页当前公开公告列表。
2. 只筛选标题包含“大额”且包含“申购/定投/转换转入”的招商公告。
3. 按公告日期倒序读取公告详情；正文有份额表时，按“下属分级基金的交易代码”定位当前代码，再读取对应“下属分级基金的限制申购金额”。
4. 公告正文明确“某日恢复大额申购/定投”，且恢复日期不晚于观察日时，返回 `不限`；金额仅作为历史限制，不继续当作当前上限。
5. 公告未给出可解析的当前金额时返回 `official_api_no_data`，不把“最低投资额”误认为大额限额。

这里的“直销”依据公告正文的“本公司直销机构”口径。招商官网产品页的公开交易按钮只用于当前是否可购买/定投，不访问 `direct.cmfchina.com` 的交易提交页面。

## 认证边界

| 状态 | 说明 |
|---|---|
| `public_with_auth_trade_boundary` | 目录、产品详情、交易状态、产品公告均可匿名读取 |
| 交易提交需认证 | 购买/定投链接可能进一步要求登录、验证码、设备签名或银行卡绑定；适配器不访问、不猜测、不绕过 |
| 接口 HTTP 401/403 | 返回 `official_interface_requires_auth`，保留官方产品页作为来源，不降级到第三方数据 |

## 实测结果（2026-08-26）

| 项目 | 结果 |
|---|---:|
| 官网公开目录 API 返回基金份额 | 695 |
| `config.json` 招商基金 | 019548 招商纳斯达克100ETF联接(QDII)C |
| 019548 最新匹配公告 | 2026-07-24，招商官网公告详情 `225156` |
| 019548 直销限额 | 100元 |
| 019548 公告口径 | “本公司直销机构”单日单个基金账户单笔或累计超过100元可拒绝 |

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`019548=100元`（招商官网最新直销公告）。
- 只有本 Adapter 本次从招商官网公开 API/公告解析出明确直销口径，才写入“已获取”；公告缺失、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
