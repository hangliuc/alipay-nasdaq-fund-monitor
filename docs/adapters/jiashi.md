# 嘉实基金 Adapter

## 适配器定位

`JiashiAdapter` 只访问嘉实基金管理有限公司官网公开页面和公开接口，负责
当前基金目录、产品资料、交易状态以及直销申购上限。它不登录嘉实网上交易、
不提交订单、不绕过验证码，也不使用支付宝、天天基金等第三方数据覆盖直销
结果。

## 官方来源

| 数据 | 官方入口 | 调用/解析方式 | 认证边界 |
| --- | --- | --- | --- |
| 基金目录与净值 | [嘉实旗下基金](https://www.jsfund.cn/main/fund/index.shtml) | `GET/POST https://www.jsfund.cn/servlet/json`，`funcNo=741010`；嘉实前端 `productService.queryFundList` 的公开参数 | 当前可匿名读取 |
| 单只产品资料 | [产品页模板](https://www.jsfund.cn/main/fund/{code}/fundManager.shtml) | `/servlet/json`，`funcNo=741011`，使用 741010 返回的 `product_id` | 当前可匿名读取 |
| 交易状态 | [产品页模板](https://www.jsfund.cn/main/fund/{code}/fundManager.shtml) | `/servlet/json`，`funcNo=741044&product_codes={code}&query_type=1`；对应官网购买、赎回、定投按钮 | 当前可匿名读取；不进入交易提交页 |
| 直销申购上限 | [嘉实官网申购上限表](https://www.jsfund.cn/main/a/20151216/191092.shtml) | 解析表格“单日单户累计申购（含）”列；优先读取“直销：”或“除代销机构投资者外：”部分 | 当前可匿名读取 |

## 可获取字段

### 基金目录（`FundIdentity`）

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `manager_id` | 固定值“嘉实” | `str` | 管理人标识 |
| `code` | `product_code` | `str` | 六位基金/份额代码 |
| `name` | `product_abbr`，缺失时 `product_name` | `str` | 官网当前份额简称 |
| `fund_type` | `fund_class` 或 `product_type` | `str` | 官网原始分类码，如 `HFM02` |
| `share_class` | 名称末尾 A/B/C/H/I 等份额字母 | `str` | 份额类别；币种后缀会被忽略 |
| `source_url` | 代码拼接产品页 | `str` | 嘉实官网产品页模板 |
| `source_type` | 固定值 | `str` | `jiashi_official_catalogue_api_741010` |

### 产品快照（`ProductSnapshot`）

| 标准字段 | 官网字段 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | `product_abbr` | `str` | 当前份额名称 |
| `full_name` | `product_name` | `str` | 基金全称 |
| `fund_type` | `fund_class_text` / `product_type_text` / `fund_class` | `str` | 官网分类名称或原始码 |
| `risk_level` | `risk_level_text` / `risk_level` | `str` | 风险等级及原始码 |
| `inception_date` | `found_date` | `str` | 统一为 `YYYY-MM-DD` |
| `asset_scale` | `newest_asset` / `scale` | `str` | 官网原始资产规模；不改写单位 |
| `net_value_date` | `nav_date` | `str` | 最新净值日期 |
| `trade_status` | `fund_status` / `product_status` | `str` | 官网原始交易状态码 |
| `fields` | 741011 `results[0]` 全部字段 | `dict[str, str]` | 保留基金经理、托管人、投资目标、投资范围、收益、净值、状态等 90+ 个字段 |

### 交易状态（`ChannelTradeStatus`）

| 输出字段 | 741044 字段 | 类型 | 说明 |
| --- | --- | --- | --- |
| `customer_type` | 固定值 `individual` | `str` | 个人公开状态 |
| `channel` | 固定描述 | `str` | 嘉实官网 741044 公开交易状态 API |
| `subscription` | `canbuy` | `bool/null` | `1` 为可申购，`0` 为不可申购 |
| `redemption` | `redeemstatus` | `bool/null` | `1` 为可赎回，`0` 为不可赎回 |
| `sip` | `fixstatus` | `bool/null` | `1` 为可定投，`0` 为不可定投 |
| `raw` | 741011 + 741044 字段 | `dict` | 保留原始状态字段，便于审计 |

### 直销限额（`DirectLimitSnapshot`）

| 输出字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `limit` | 申购上限表“单日单户累计申购（含）”列直销部分 | `str/null` | 例如 `10万` → `10万元`；“暂停申购”→`暂停` |
| `status` | 解析结果 | `str` | `ok` / `official_api_no_data` / `official_interface_requires_auth` |
| `quota_type` | 固定口径 | `str` | 单日单户累计申购（含）直销上限 |
| `quota_remark` | 解析规则 | `str` | 明确说明排除了非直销/代销机构值 |
| `source_url` | 实际表格 URL | `str` | 可直接复核的嘉实官网来源 |
| `observed_at` | 运行时钟 | `str` | ISO 8601 观察时间 |
| `raw` | 代码组、名称、申购/转入/定投、日期、备注及原始申购列 | `dict` | 保留完整表格行，便于核对共享额度、币种和生效日期 |

## 限额规则

1. 表格代码单元格可能以 `/` 合并多个份额，适配器会展开为每个六位代码。
2. `直销：10万 非直销：1000元` 只取 `10万`；`除代销机构投资者外：800万
   代销机构投资者：10万` 只取前者。非直销/代销机构值永远不会写入直销列。
3. “暂停申购”会输出 `暂停`；空白、“暂未开通”或没有对应代码输出
   `official_api_no_data`，不猜测为“不限”。
4. 申购上限表同时包含转入、定投、起始日期和备注；这些字段在 `raw` 中保留，
   但 `limit` 的标准口径是申购列直销上限。

## 认证边界

嘉实目录、产品详情、交易状态接口和申购上限表当前均可匿名读取。
嘉实网上交易入口位于 `https://e.jsfund.cn/lcj/trade/login`，可能要求登录、
验证码或其他账户信息；本适配器不访问交易提交接口。若公开接口返回 401/403，
结果标记为 `official_interface_requires_auth`，不绕过认证。

## 当前实测（2026-08-26）

| 项目 | 结果 |
| --- | ---: |
| 官网目录 API 返回基金/份额 | 727 |
| 申购上限表可解析代码行 | 153 |

| 基金代码 | 官网产品资料 | 直销申购上限 | 来源 |
| --- | --- | --- | --- |
| `016533` | 产品详情正常；净值日期 `2026-08-25` | `暂停` | 嘉实官网申购上限表 |
| `017731` | 产品详情正常；净值日期 `2026-08-25` | `10万元` | 嘉实官网申购上限表 |
| `000043` | 产品详情正常；净值日期 `2026-08-25` | `10万元` | 嘉实官网申购上限表 |

## 实现与测试

- 实现：[jiashi.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/jiashi.py)
- 单元测试：[test_jiashi_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_jiashi_adapter.py)
- 相关测试：`8 passed`

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销状态/限额**——`016533=暂停`、`017731=10万元`、`000043=10万元`（嘉实官网申购上限表）。
- 只有本 Adapter 本次从嘉实官网申购上限表解析出的当前直销口径，才写入“已获取”；表格缺行、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
