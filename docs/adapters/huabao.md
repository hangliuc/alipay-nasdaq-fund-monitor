# 华宝基金 Adapter

## 适配器定位

`HuabaoAdapter` 独立访问华宝基金管理有限公司官网公开页面、官网产品页和官网限额公告 PDF。它不访问支付宝、天天基金等第三方平台，不登录网上交易，不提交订单，也不绕过网点代码、验证码、设备签名或银行卡认证。

## 官方来源

| 数据类别 | 官方入口 | 调用/解析方式 | 认证边界 |
| --- | --- | --- | --- |
| 基金目录、净值、状态 | [华宝基金超市](https://www.fsfund.com/fund/fundMarket.shtml) | 读取服务端页面 hidden input `#fundMarketList` 中的官网全量记录 | 当前可匿名访问 |
| 单只产品资料 | [华宝产品页模板](https://www.fsfund.com/fund/{code}/fundDetail.shtml) | 读取公开服务端 HTML 表格及隐藏产品字段 | 当前可匿名访问 |
| 产品净值历史/动态资料 | [华宝产品页脚本](https://www.fsfund.com/static/js/fund/fundDetail.js) | 官网公开脚本声明 `/v2/webzk/queryController/queryFundNavs`、`getFundNavList`；Adapter保留产品页字段，不提交交易 | 公开查询接口可能要求网点代码 |
| 直销申购/定投限额 | [华宝官网公告](https://www.fsfund.com/helpCenter/notice.shtml) | 解析华宝官网限额公告 PDF 正文中“直销柜台及网上直销平台”金额 | 公告查询 API 当前返回“网点代码不能为空”，不绕过 |
| 交易入口边界 | [华宝网上交易](https://e.fsfund.com/etrading/) | 仅记录官网产品页的“立即购买”链接；不打开交易提交页 | 可能要求登录、验证码或银行卡 |

## 可获取字段

### 基金目录（`FundIdentity`）

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `manager_id` | 固定值“华宝” | `str` | 管理人标识 |
| `code` | `FUNDCODE` | `str` | 六位基金/份额代码 |
| `name` | `SHORTNAME` | `str` | 官网当前份额简称 |
| `fund_type` | `FUNDSTYLE` | `str` | 官网原始基金类型码 |
| `share_class` | 名称末尾 A/B/C 等字母 | `str` | 份额类别；币种后缀会被忽略 |
| `source_url` | 代码拼接产品页 | `str` | 可直接复核的华宝官网产品页 |
| `source_type` | 固定适配器标识 | `str` | `huabao_official_fund_catalogue_html` |

### 产品快照（`ProductSnapshot`）

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | `#shortName` 或“基金简称” | `str` | 当前份额名称 |
| `full_name` | “基金全称” | `str` | 基金法律全称 |
| `fund_type` | “基金类型” | `str` | 官网展示分类，如“海外基金” |
| `risk_level` | “风险等级” | `str` | 官网风险等级，如 R4 |
| `inception_date` | “基金合同生效日” | `str` | 统一为 `YYYY-MM-DD` |
| `asset_scale` | “最新规模” | `str` | 保留官网单位，如“56.2亿” |
| `net_value_date` | 产品页“最新净值日期”（如有） | `str` | 保留官网日期 |
| `trade_status` | “立即购买”按钮 | `str` | 公开页面可观察到的购买状态 |
| `fields` | 产品页全部成对表格字段及按钮状态 | `dict[str, str]` | 保留基金代码、托管人、基金经理、风险、规模等原始字段 |

### 交易状态（`ChannelTradeStatus`）

| 输出字段 | 来源/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `customer_type` | 固定值 `individual` | `str` | 个人公开状态 |
| `channel` | 固定描述 | `str` | 华宝基金官网产品页公开购买入口 |
| `subscription` | “立即购买”链接存在且非 JavaScript | `bool/null` | 只能判断公开产品页入口，不代表交易提交一定成功 |
| `redemption` | 未在匿名产品页稳定公开 | `bool/null` | 不猜测 |
| `sip` | 产品页未稳定公开定投按钮 | `bool/null` | 不猜测 |
| `raw` | `ProductSnapshot.fields` | `dict` | 保留按钮及产品原始字段 |

### 直销限额（`DirectLimitSnapshot`）

| 输出字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `limit` | 公告正文“直销柜台及网上直销平台”金额 | `str/null` | 例如 `50万元`、`10万元`；不取代销机构金额 |
| `status` | 解析结果 | `str` | `ok` / `official_api_no_data` / `official_interface_requires_auth` |
| `quota_type` | 固定口径 | `str` | 直销柜台及网上直销平台单日单户累计申购（含定投）上限 |
| `quota_remark` | 公告解析说明 | `str` | 明确限额是直销口径，排除代销金额 |
| `source_url` | 实际官网 PDF 或公告 API | `str` | 可直接复核的一手来源 |
| `observed_at` | 运行时钟 | `str` | ISO 8601 观察时间 |
| `raw` | 公告标题、日期、正文和原始解析字段 | `dict` | 便于审计、复核和后续更新 |

## 限额解析规则

1. 公告正文必须同时出现目标份额代码和直销语义（“直销柜台”“网上直销平台”或“直销渠道”）。
2. 同一公告含有代销金额和直销金额时，只取直销语义所在段落中的最后一个金额；例如 `代销 3000 元、直销 10 万元` 输出 `10万元`。
3. “暂停大额申购/定投”但未给出直销金额时输出 `暂停`；空白或无法验证时不猜测为“不限”。
4. 官网公告查询 API 当前要求网点代码。未提供网点代码时结果标记 `official_interface_requires_auth`，不猜网点代码、不绕过认证。

## 当前 config 基金实测

| 基金代码 | 官网产品页 | 直销限额 | 官方来源 |
| --- | --- | --- | --- |
| `017437` | 可匿名访问；“立即购买”可见 | `10万元` | [华宝纳斯达克精选限额公告 PDF](https://www.fsfund.com/static/notice/2026/02/09/be5ff786-dc58-411b-a250-20d2c47a101b/华宝纳斯达克精选股票型发起式证券投资基金（QDII）调整大额申购（含定投）金额上限的公告.pdf) |
| `017204` | 可匿名访问；“立即购买”可见 | `1万元` | [华宝海外科技限额公告 PDF](https://www.fsfund.com/webimages/upload2012/2025/09/29/6af2fb6f-cf7e-4b4d-8bef-3b13c8960206/华宝海外科技股票型证券投资基金（QDII-LOF）调整大额申购（含定投）金额上限的公告.pdf) |
| `008254` | 可匿名访问；“立即购买”可见 | `50万元` | [华宝致远限额公告 PDF](https://www.fsfund.com/webimages/upload2012/2025/03/11/996336b3-c71b-4cc2-85ca-60135a20ed9b/华宝致远混合型证券投资基金（QDII）调整大额申购（含定投）金额上限的公告.pdf) |

上述三份公告均为华宝基金官网 PDF；公告发布时间分别为 2026-02-09、2025-09-30、2025-03-12。它们是公开公告的最近可验证值，并不声称官网匿名接口能够实时返回所有份额的限额。

## 认证边界

华宝基金产品目录和产品页当前可匿名访问。官网公告查询和网上交易入口可能要求网点代码、登录、验证码、设备签名或银行卡信息；适配器只记录 `official_interface_requires_auth`，不尝试绕过。

## 实现与测试

- 实现：[huabao.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/huabao.py)
- 单元测试：[test_huabao_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_huabao_adapter.py)
- 相关测试：`8 passed`

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`017437=10万元`、`017204=1万元`、`008254=50万元`（华宝官网直销限额公告 PDF）。
- 只有本 Adapter 本次从华宝官网公告 PDF 解析出的明确直销口径，才写入“已获取”；公告查询接口认证、无当前公告或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
