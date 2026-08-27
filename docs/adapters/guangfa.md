# 广发基金 Adapter

## 适配器定位

`GuangfaAdapter` 将广发基金官网的全量基金目录、产品页、公开
`JsonService` 基金资料接口、个人/机构限额接口和临时公告 PDF 分开处理。
限额优先使用产品页脚本实际调用的个人限额 API；该接口没有当前上限时才
追踪官网公告。适配器不登录、不提交交易、不访问第三方平台，也不绕过交易
页可能出现的验证码。

## 官方来源

| 数据 | 官方入口 | 读取方式 |
| --- | --- | --- |
| 全量基金/份额目录 | [广发基金基金超市](https://www.gffunds.com.cn/funds/) | 解析服务端渲染的 `tbody#all-funds`；2026-08-26 实测 954 条份额 |
| 产品资料与公开按钮 | `https://www.gffunds.com.cn/funds/?fundcode={code}` | 解析 `.table04`、页面内嵌变量和购买/定投按钮 |
| 当前基金资料/净值/交易状态 | `https://www.gffunds.com.cn/apistore/JsonService` | `service=BaseInfo&method=Fund&op=queryFundByGFFundcode&fundcode={code}` |
| 个人当前申购上限 | `https://www.gffunds.com.cn/api/v1/funds/fund-person-limit.shtml?fundcode={code}` | 读取官网脚本使用的 `MAX_ALLOT_BALA`；这是直销产品页当前个人限额 |
| 机构限额（审计辅助） | `https://www.gffunds.com.cn/api/v1/funds/fund-org-limit.shtml?fundcode={code}` | 与个人 API 同时保存，不冒充个人直销限额 |
| 最新限额公告 | [广发临时公告列表](https://www.gffunds.com.cn/jjgg/zdsj/) | 按日期倒序扫描 28 页，下载官方 PDF 并按基金代码解析 |

## 可获取字段

### 目录（`FundIdentity`）

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `manager_id` | 固定值“广发” | `str` | 管理人标识 |
| `code` | `data-fundcode` | `str` | 六位基金/份额代码 |
| `name` | `.js-select-fund[data-fundname]`，缺失时第三列链接文本 | `str` | 官网当前份额名称 |
| `fund_type` | 行 class `fund-typeid-{id}` | `str` | 保存为 `官网类型码:{id}`，不把内部类型码误称为中文类型 |
| `share_class` | 名称末尾 A/C/I 等后缀 | `str` | 份额类别 |
| `source_url` | 代码拼接产品页 | `str` | 广发官方产品页 |
| `source_type` | 固定值 | `str` | `guangfa_official_fund_catalogue_html` |
| `raw` | 目录行（内部缓存） | `dict` | 含风险、净值、净值日期、操作链接和原始 class |

### 产品快照（`ProductSnapshot`）

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | `基金简称` / 页面 `fundName` | `str` | 当前份额名称 |
| `full_name` | `基金全称` | `str` | 基金合同全称 |
| `fund_type` | 页面 `fundType`，或 `CATEGORYNAME` | `str` | 例如“海外型”“混合型” |
| `risk_level` | `FUNDLEVELSHOW`，或页面风险等级 | `str` | 例如“中风险” |
| `inception_date` | `CREATEDATE` / `成立日期` | `str` | 统一为 `YYYY-MM-DD` |
| `asset_scale` | `FUNDASSETSCALE`（若接口返回） | `str` | 官网原始资产规模 |
| `net_value_date` | `NAVDATE` | `str` | 最新净值日期 |
| `trade_status` | `FUNDSTATUS` | `str` | 当前交易状态，例如“正常开放” |
| `fields[购买按钮状态]` | `WEBISOPEN` 或产品页购买按钮 | `str` | `open` / `closed` / `unknown` |
| `fields[定投按钮状态]` | `WEBFIXOPEN` 或产品页定投按钮 | `str` | `open` / `closed` / `unknown` |
| `fields` | `.table04` + `API_*` 原始字段 | `dict[str, str]` | 保留基金经理、托管人、净值、收益、状态等全部可读字段 |

### 直销限额快照（`DirectLimitSnapshot`）

| 输出字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `limit` | 个人 API `MAX_ALLOT_BALA` | `str/null` | 统一为人民币元格式；例如 `2,000` → `2000元` |
| `status` | 解析结果 | `str` | `ok` / `official_api_no_data` / `official_interface_requires_auth` |
| `quota_type` | 固定口径 | `str` | 产品页个人客户 `MAX_ALLOT_BALA` |
| `quota_remark` | API 与公告规则 | `str` | 说明个人 API 为主、机构值仅审计保存 |
| `source_url` | 实际响应接口或 PDF | `str` | 可直接复核的一手来源 |
| `observed_at` | 运行时钟 | `str` | ISO 8601 观察时间 |
| `raw` | 个人/机构 JSON + 公告字段 | `dict` | 保留 `MIN_ALLOT_BALA`、`MAX_ALLOT_BALA`、公告标题和正文摘要 |

## 限额与状态规则

1. 个人限额 API 的正数 `MAX_ALLOT_BALA` 直接视为官网产品页显示的直销个人
   限额；`MIN_ALLOT_BALA` 只表示起购金额，不作为上限。
2. API 无个人上限时，按公告列表日期倒序筛选“调整/暂停/恢复大额申购、定投”，
   下载官方 PDF，按基金代码与金额列对齐；暂停输出 `暂停`，恢复且未列金额输出
   `不限`。
3. 两条官方链路都没有可验证当前值时输出 `official_api_no_data`，不沿用历史
   快照、不把第三方分销数据写入直销列。

## 认证边界

目录、产品页、`JsonService` 和个人/机构限额接口当前可匿名访问，
`auth_boundary()` 返回 `public`。购买和定投链接位于
`trade.gffunds.com.cn`，可能要求登录或验证码；本适配器不访问交易提交页，若
官方公开接口返回 401/403 则记录 `official_interface_requires_auth`，不会绕过认证。

## 当前实测（2026-08-26）

| 基金代码 | 产品页状态 | 个人实时限额 | 来源 |
| --- | --- | --- | --- |
| `006479` | 正常开放 | `5元` | 广发官网 `fund-person-limit.shtml` |
| `021277` | 正常开放 | `2000元` | 广发官网 `fund-person-limit.shtml` |
| `270001`（非 config 例） | 正常开放 | 未公开当前上限 | API 返回空上限，公告未解析到当前限额 |

## 实现与测试

- 实现：[guangfa.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/guangfa.py)
- 单元测试：[test_guangfa_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_guangfa_adapter.py)

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`006479=5元`、`021277=2000元`（广发官网个人限额 API）。
- 只有本 Adapter 本次从个人限额 API 或当前公告解析出明确值，才写入“已获取”；接口空结果、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
