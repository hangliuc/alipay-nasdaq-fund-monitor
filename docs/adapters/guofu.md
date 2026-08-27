# 国富基金 Adapter

## 结论

国富官网提供三层公开数据：基金超市分类接口、单只产品页和产品页信息披露
列表/PDF。当前实现为独立 `GuofuAdapter`，可发现官网当前公开基金及份额，
读取产品页交易状态，并从最新限额公告解析申购/定投限制。全链路不需要开户、
登录、验证码、设备签名或银行卡。

## 官方来源

| 用途 | 官方来源 | 说明 |
| --- | --- | --- |
| 基金超市分类 | [国富官网](https://www.ftsfund.com/) | 前端按基金类型加载分类列表 |
| 基金目录接口 | `https://www.ftsfund.com/index/jjcs?fund_type={type}&search_value=&sortField=&sortMode=` | `201008` 等类型码对应 QDII、债券、指数、混合、股票、FOF 等分类 |
| 产品页 | `https://www.ftsfund.com/qxjj/jjxq/{code}` | 基金全称、简称、类型、风险等级、成立日期和交易状态 |
| 信息披露列表 | `https://www.ftsfund.com/qxjj/jjxq/xxplload?fundCode={code}&page=1` | 当前产品页最新公告列表 |
| 公告附件 | 国富官网 `ftp/upload/file/...pdf` | 最新大额申购/定投公告，解析生效限额 |

以上均为国海富兰克林基金官方域名，不使用东方财富、支付宝、天天基金或其他
第三方平台作为直销数据源。

## 可获取字段

### 1. 基金目录（`FundIdentity`）

| 标准字段 | 官方来源 | 说明 |
| --- | --- | --- |
| `code` | 分类表基金代码列、产品页链接 | 六位基金代码 |
| `name` | 分类表基金名称列 | 基金/份额名称 |
| `fund_type` | 分类接口 `fund_type` | QDII、债券型、指数型、混合型、股票型、FOF 等 |
| `share_class` | 名称末尾 `A/B/C/Y` | 可识别的份额后缀 |
| `source_url` | `/qxjj/jjxq/{code}` | 国富官方产品页 |
| `source_type` | — | `guofu_official_fund_supermarket` |

目录按代码去重。当前公开分类目录的数量以每次抓取为准，不推断为历史上所有
已发行或已清盘基金。

### 2. 产品详情（`ProductSnapshot`）

| 标准字段 | 官方页面字段 | 说明 |
| --- | --- | --- |
| `name` | `基金简称` | 份额名称 |
| `full_name` | `基金全称` | 法定基金全称 |
| `fund_type` | `基金类型` | 例如 `QDII` |
| `risk_level` | 页面顶部 `风险等级` | 例如“中风险” |
| `inception_date` | `成立时间` / `成立日期` | 统一为 `YYYY-MM-DD` |
| `net_value_date` | 产品页当前页未单独提供 | 无可靠字段时保持空值 |
| `trade_status` | `交易状态` | 例如“正常开放” |
| `fields` | 产品页标签表及顶部摘要 | 原始字段保留，含最新净值、基金经理、托管人等 |

### 3. 交易状态（`TradeSnapshot`）

| 页面文本 | `subscription` | `redemption` |
| --- | --- | --- |
| `正常开放` / `开放申购` | `true` | `true` |
| `暂停申购` | `false` | `true` |
| `暂停赎回` | `true` | `false` |
| `暂停交易` / `基金终止` | `false` | `false` |
| 其他或空值 | `null` | `null` |

交易状态来源是国富官方产品页，不从“立即购买”链接或交易登录页反推状态。

### 4. 直销限额（`DirectLimitSnapshot`）

| 输出字段 | 规则 |
| --- | --- |
| `limit` | 最新限额公告中优先解析“通过直销机构”后的金额；例如 `1,000.00 元` → `1000元` |
| `status` | 解析到金额或明确暂停/恢复时为 `ok`；没有可确认字段为 `official_api_no_data` |
| `quota_type` | `单日每个基金账户累计申购及定期定额投资` |
| `quota_remark` | 保留公告对直销/非直销、适用份额和业务范围的说明 |
| `source_url` | 最新国富官方公告 PDF URL |
| `observed_at` | 本次抓取时间 |
| `raw` | 产品页字段、公告标题、日期及正文 |

解析规则：

1. 公告明确区分直销机构和非直销机构时，直销金额优先；例如 021842 当前公告为直销 1000 元、非直销 100 元，输出 1000 元。
2. 公告未单列直销，但按下属基金代码列出限制申购金额时，按目标代码匹配对应金额。
3. 公告明确恢复大额申购且没有金额上限时，输出 `不限`；不能从“正常开放”单独推断不限。
4. 产品页正常开放但没有可解析的最新限额公告时，输出 `official_api_no_data`，不沿用历史公告值。

## 认证边界

当前基金超市、产品页、信息披露列表和官方 PDF 均可匿名访问，
`auth_boundary()` 返回 `public`。如果未来出现 HTTP 401/403，Adapter 返回
`official_interface_requires_auth`，不绕过登录、验证码、设备签名或银行卡绑定。

## 代码和测试

- 实现：[guofu.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/guofu.py)
- 兼容入口：[product_pages.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/product_pages.py)
- 单元测试：[test_guofu_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_guofu_adapter.py)

```bash
python3 -m pytest -q tests/test_guofu_adapter.py
```

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`021662=1000元`、`021842=1000元`（国富官网最新信息披露公告 PDF）。
- 只有本 Adapter 本次从国富官网产品页信息披露接口/公告 PDF 解析出的当前值，才写入“已获取”；公告缺失、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
