# 银华基金 Adapter

## 适配器定位

`YinhuAdapter` 独立访问银华基金管理股份有限公司（`yhfund.com.cn`）的公募基金产品页、官网公告/PDF 和网上直销入口。它不访问东方财富、天天基金、支付宝或其他第三方平台，不提交登录/交易请求，也不绕过登录、图片验证码、设备签名或银行卡校验。

## 官方来源

| 数据类别 | 官方入口 | 调用/解析方式 | 认证边界 |
| --- | --- | --- | --- |
| 基金目录 | [银华基金产品入口](https://www.yhfund.com.cn/main/fund/index.shtml) | 解析官方产品链接中的 `product_code`/`fundCode` 以及页面表格代码 | 目录若返回 401/403 或官网防护页，不伪造目录 |
| 单只产品资料 | [银华基金详情页模板](https://www.yhfund.com.cn/main/fund/funddetail/index.shtml?product_code={code}) | 解析官方 HTML 的表格、`dl` 字段、净值和申购/赎回状态 | 产品页公开可读时使用；不可读时保持 `official_api_no_data` |
| 当前直销限额 | 产品页或产品页关联的银华官方公告/PDF | 只接受“直销中心/直销渠道/网上直销”语义下的限额，或公告明确的暂停/恢复状态 | 起购金额不是限额；没有当前明确字段不猜测 |
| 网上直销交易入口 | [银华网上交易登录页](https://trade.yhfund.com.cn/yhxntrade/account/goLogin.do) | 只观察登录页的认证控件和状态，不提交任何表单 | 未登录要求账号、密码和图片验证码，记录 `official_interface_requires_auth` |

## 可获取字段

### 基金目录（`FundIdentity`）

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `manager_id` | 固定值“银华” | `str` | 管理人标识 |
| `code` | 产品链接 `product_code`/`fundCode` 或官网代码字段 | `str` | 六位基金/份额代码 |
| `name` | 产品链接文本或同一行基金名称 | `str` | 官网当前份额名称 |
| `fund_type` | 目录行/产品页基金类型 | `str` | 保留官网原始分类 |
| `share_class` | 名称末尾 A/B/C 等字母 | `str` | 份额类别 |
| `source_url` | 银华官网产品链接 | `str` | 可直接复核的一手来源 |
| `source_type` | 固定适配器标识 | `str` | `yinhu_official_fund_catalogue` |

### 产品快照（`ProductSnapshot`）

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | 页面标题、基金简称/名称 | `str` | 当前份额名称 |
| `full_name` | 基金全称/法定名称 | `str` | 法律全称 |
| `fund_type` | 基金类型/基金类别 | `str` | 官网原始类型 |
| `risk_level` | 风险等级/风险特征 | `str` | 官网原始风险字段 |
| `inception_date` | 成立日期/基金合同生效日 | `str` | 标准化为 `YYYY-MM-DD` |
| `asset_scale` | 基金规模/最新规模/资产规模 | `str` | 保留官网单位 |
| `net_value_date` | 净值日期/最新净值日期 | `str` | 保留并标准化官网日期 |
| `trade_status` | 公开申购、赎回状态 | `str` | 不从“购买”导航文字推断 |
| `fields` | 产品页全部可识别表格字段 | `dict[str, str]` | 同时保留官网特有原始字段 |

### 交易状态（`TradeSnapshot`）

| 输出字段 | 规则 | 类型 |
| --- | --- | --- |
| `customer_type` | 固定为 `individual` | `str` |
| `channel` | 银华官网公开产品页或官网直销交易入口 | `str` |
| `subscription` | 由明确“开放/正常/可申购”或“暂停/关闭”字段映射 | `bool/null` |
| `redemption` | 由明确赎回状态字段映射 | `bool/null` |
| `raw` | 产品页原始字段/错误信息 | `dict` |

### 直销限额（`DirectLimitSnapshot`）

| 输出字段 | 规则 | 类型 |
| --- | --- | --- |
| `limit` | 直销明确金额、`暂停` 或公告明确恢复时的 `不限` | `str/null` |
| `status` | `ok` / `official_api_no_data` / `official_interface_requires_auth` | `str` |
| `quota_type` | 直销渠道申购上限、申购限额或公告状态 | `str` |
| `quota_remark` | 限额口径和官方解析说明 | `str` |
| `source_url` | 银华官方产品页或公告/PDF；认证时为官方登录页 | `str` |
| `observed_at` | 运行时观察时间 | `str` |
| `raw` | 产品页字段或公告标题、正文和日期 | `dict` |

## 限额解析规则

1. 公告或产品页必须出现目标基金代码；不把其他份额、其他基金或第三方公告归给目标代码。
2. “直销中心”“直销渠道”“网上直销”“直销柜台”语义优先于泛化金额；同一段落有旧值/新值时取公告正文明确生效的新上限。
3. “暂停大额申购/定投”输出 `暂停`；“恢复大额申购/定投”且未列金额时输出 `不限`。
4. “起购金额/最低申购金额”不属于大额申购限额，不能填入 `limit`。
5. 公告无目标代码、无直销语义或官网公开入口需认证时，不猜测限额，保留失败状态。

## 当前 config 基金实测（2026-08-26）

| 基金代码 | 产品/交易入口 | 直销限额 | 结论 |
| --- | --- | --- | --- |
| `016702` | 产品目录/详情页当前匿名请求返回官网防护响应；网上交易入口可访问登录页 | 未获取 | `official_interface_requires_auth`（交易页要求账号、密码和图片验证码；当前没有可验证的匿名直销限额字段） |

## 认证边界

银华网上交易登录页公开出现账号类型、账号/密码输入框和图片验证码。适配器只记录 `public_with_trade_auth_boundary`、`requires_login=true`、`requires_captcha=true`；不尝试注册账户、提交验证码、绕过设备签名或读取银行卡绑定信息。

## 实现与测试

- 实现：[yinhu.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/yinhu.py)
- 单元测试：[test_yinhu_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_yinhu_adapter.py)
- 当前 Adapter 测试：`7 passed`

## 直销获取结论（统一判定）

- `config.json` 目标 `016702`：**未获取直销限额**。
- 官网网上交易入口已确认存在登录、密码和图片验证码认证边界；产品/公告公开入口当前没有返回可验证的当前直销限额。
- 只有本 Adapter 从银华官网当前产品页或官方公告/PDF 明确解析出直销限额/状态时才写入“已获取”。
- 失败不回填统一兼容层、历史静态值或第三方平台数据；后续运行只重试银华官方入口。

