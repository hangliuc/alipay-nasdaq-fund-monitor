# 建信基金 Adapter

## 适配器定位

`JianxinAdapter` 独立读取建信基金官网公开的基金目录、产品详情和公告附件。
目录、产品详情和公告接口均为匿名公开接口；直销限额采用建信官网最新大额
申购/定投公告附件中的直销渠道口径。适配器不登录、不提交交易、不绕过验证码、
设备签名或银行卡绑定，也不使用第三方平台。

## 官方来源

| 数据类别 | 官方 URL/API | 方法与备注 |
| --- | --- | --- |
| 全量基金/份额目录 | [建信基金目录 API](https://www.ccbfund.cn/website/v1/api/fundList) | `GET`；返回按基金类型分组的当前基金份额、净值、收益、风险和基金经理字段 |
| 产品详情、净值、收益、费率 | [建信产品详情 API](https://www.ccbfund.cn/website/v1/api/fund/detail?fundCode={code}) | `GET fundCode={code}`；返回 `detail.fund`、`detail.profit`、费率结构和基金经理 |
| 净值历史（可选） | `https://www.ccbfund.cn/website/v1/api/fund/chart?fundCode={code}` | 官网产品页公开净值曲线接口；当前 Adapter 产品快照使用详情接口中的最新净值 |
| 公告列表 | [建信公告 API](https://www.ccbfund.cn/website/v1/api/fund/notice?fundCode={code}&keyword=暂停&page=1) | `GET`；按代码和关键词定位最新“调整/暂停/恢复大额申购、定投”公告 |
| 公告详情 | `https://www.ccbfund.cn/resource/static/content/{cntId}.html` | 读取标题、日期和附件链接 |
| 公告附件 | 由详情页 `a[href]` 返回的建信官网 `.doc`/`.docx`/`.pdf` | 官方附件转文本后解析；多数历史公告为 `application/msword` |
| 产品页 | `https://www.ccbfund.cn/#/fund?fundCode={code}` | 用作产品详情页来源标识；数据请求由上述官方 API 完成 |

## 可获取字段

### 基金目录：`FundIdentity`

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `manager_id` | 固定值“建信” | `str` | 管理人标识 |
| `code` | `fundCode` | `str` | 六位基金/份额代码 |
| `name` | `fundName` | `str` | 官网当前份额名称 |
| `fund_type` | `fundTypeName` / `fundTypeCode` | `str` | 保留官网类型名称，例如“海外基金” |
| `share_class` | 名称末尾 A/B/C/D 等份额后缀 | `str` | 份额类别 |
| `source_url` | 代码拼接产品页 | `str` | 建信官网产品页 |
| `source_type` | 固定值 | `str` | `jianxin_official_fund_catalogue_api` |
| 目录原始字段 | `_catalogue_rows[code]` | `dict` | 包含净值、净值日期、涨跌幅、收益、风险、基金经理、分类等字段 |

### 产品快照：`ProductSnapshot`

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | `data.detail.fund.fundShortName` | `str` | 当前份额简称 |
| `full_name` | `data.detail.fund.fundName` | `str` | 基金全称 |
| `fund_type` | `data.detail.fund.fundType` | `str` | 官网基金类型 |
| `risk_level` | `data.detail.fund.riskLevel` | `str` | 风险等级 |
| `inception_date` | `issueDateStr` | `str` | 统一为 `YYYY-MM-DD` |
| `asset_scale` | 详情接口当前未提供标准资产规模字段 | `str` | 空值，不以其他来源猜测 |
| `net_value_date` | `netValueDateStr1` | `str` | 最新净值日期 |
| `trade_status` | 详情接口未返回当前申购开关 | `str` | 明确记录“未公开当前申购/赎回交易状态” |
| `fields[基金代码]` | `fundCode` | `str` | 原始代码 |
| `fields[基金管理人]` | `managerName` | `str` | 管理人 |
| `fields[基金托管人]` | `trusteeName` | `str` | 托管人 |
| `fields[最新净值]` | `netValue` | `str` | 最新单位净值 |
| `fields[累计净值]` | `totalNetValue` | `str` | 最新累计净值 |
| `fields[收益数据来源]` | `profit.dataSource` | `str` | 官网披露的收益数据来源 |
| `fields[近一年收益率]` | `profit.lastYear` | `str` | 产品信息字段，不作为直销限额 |
| `fields[基金经理]` | `fundManager[].name` | `str` | 多名经理用“、”连接 |
| `fields[fund_*]`、`profit_*` | 详情 JSON 递归展开 | `dict[str, str]` | 保留官网全部基础字段、投资目标、投资范围、基金特色等 |
| `fields[fareStructure]` | 费率结构数组 JSON | `str` | 保留认购、申购、赎回、管理、托管、销售服务费率原始结构 |
| `fields[category]` | 产品公告分类数组 JSON | `str` | 保留法律文件、定期公告、临时公告分类 |

### 交易快照：`TradeSnapshot`

| 字段 | 来源/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `channels[].customer_type` | 固定 `individual` | `str` | 个人客户观察口径 |
| `channels[].channel` | 建信产品详情 API | `str` | 官方公开产品信息渠道 |
| `channels[].subscription` | 详情 API 未公开 | `bool/null` | `null`，不猜测当前可申购 |
| `channels[].redemption` | 详情 API 未公开 | `bool/null` | `null`；`showRedemption` 仅为页面展示属性，不当作交易状态 |
| `channels[].sip` | 详情 API 未公开 | `bool/null` | `null`，不猜测定投状态 |
| `channels[].raw` | 产品字段 | `dict` | 保存可审计的产品详情字段 |

### 直销限额快照：`DirectLimitSnapshot`

| 输出字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `limit` | 公告附件直销渠道例外，或公告表格对应代码的限制申购金额 | `str/null` | 例如 `50元`、`1万元`、`1美元`、`暂停`、`不限` |
| `status` | 解析结果 | `str` | `ok` / `official_api_no_data` / `official_interface_requires_auth` |
| `quota_type` | 公告正文/表格口径 | `str` | 直销渠道优先；无直销例外时记录公告通用限制申购金额 |
| `quota_remark` | 解析规则 | `str` | 说明是否命中“建信基金直销渠道”例外 |
| `source_url` | 实际 DOC/PDF 附件 | `str` | 可复核的一手来源 |
| `observed_at` | 运行时钟 | `str` | ISO 8601 观察时间 |
| `raw` | 公告元数据与附件文本 | `dict` | 保留公告标题、日期、`cntId`、附件 URL 和解析文本 |

## 限额解析规则

1. 按基金代码请求公告 API，关键词依次使用“暂停”“大额申购”“恢复”，并按官网
   返回顺序从最新公告开始检查。
2. 只接受标题包含“大额申购”“大额定投”或“定期定额投资”的公告；详情页附件
   优先于详情页正文。
3. 公告正文同时出现通用限额和“建信基金直销渠道”限额时，优先采用直销渠道
   例外。例如 012752 的同一公告中通用限额为 10 元，但直销渠道限额为 50 元，
   输出 `50元`。
4. 没有直销例外时，按“下属分级基金的交易代码”与“限制申购金额”表格位置
   对齐当前份额；美元份额保留美元单位。
5. 只有公告明确恢复且未给出金额时输出 `不限`；两条官方链路都没有当前可解析
   结果时输出 `official_api_no_data`，不沿用历史数据、不使用第三方数据。

## 认证边界

建信基金目录、产品详情、公告列表、公告详情和 DOC/PDF 附件当前可匿名访问，
`auth_boundary()` 返回 `public`。交易提交可能要求登录、验证码、设备签名或
银行卡绑定；适配器不访问或绕过这些页面。如果官方公开接口后续返回 401/403，
将返回 `official_interface_requires_auth`。

## 当前实测（2026-08-26）

| 基金代码 | 产品快照 | 直销限额 | 最新官方公告附件 |
| --- | --- | --- | --- |
| `012752` | 建信纳斯达克100指数（QDII）C人民币；净值日期 `2026-08-24`，净值 `3.2872` | `50元` | [2026-08-19 公告 DOC](https://www.ccbfund.cn/resource/upload/notice/012752/202608191043/%E5%BB%BA%E4%BF%A1%E7%BA%B3%E6%96%AF%E8%BE%BE%E5%85%8B100%E6%8C%87%E6%95%B0%E5%9E%8B%E8%AF%81%E5%88%B8%E6%8A%95%E8%B5%84%E5%9F%BA%E9%87%91%EF%BC%88QDII%EF%BC%89%E6%9A%82%E5%81%9C%E5%A4%A7%E9%A2%9D%E7%94%B3%E8%B4%AD%E3%80%81%E5%AE%9A%E6%9C%9F%E5%AE%9A%E9%A2%9D%E6%8A%95%E8%B5%84%E5%85%AC%E5%91%8A.doc) |
| `539002` | 建信新兴市场混合（QDII）A；净值日期 `2026-08-24`，净值 `2.305` | `1万元` | [2026-08-26 公告 DOC](https://www.ccbfund.cn/resource/upload/notice/539002/202608260908/%E5%BB%BA%E4%BF%A1%E6%96%B0%E5%85%B4%E5%B8%82%E5%9C%BA%E4%BC%98%E9%80%89%E6%B7%B7%E5%90%88%E5%9E%8B%E8%AF%81%E5%88%B8%E6%8A%95%E8%B5%84%E5%85%AC%E5%91%8A.doc) |

官网 `fundList` 接口本次返回 357 个去重后的当前基金/份额代码。

## 实现与测试

- 实现：[jianxin.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/jianxin.py)
- 单元测试：[test_jianxin_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_jianxin_adapter.py)

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`012752=50元`、`539002=1万元`（建信官网公告 API + 官方 DOC 附件）。
- 只有本 Adapter 本次从建信官网公告及附件解析出的当前值，才写入“已获取”；公告无可验证字段、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
