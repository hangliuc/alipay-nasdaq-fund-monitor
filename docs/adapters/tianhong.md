# 天弘基金 Adapter

## 适配器定位

`TianhongAdapter` 将天弘官网的公开目录/API、产品页和公告 CDN 分开处理：
目录/API 若返回官网 WAF challenge，则明确记录边界，不执行 JavaScript challenge
计算、不伪造 Cookie、不绕过验证；官方 CDN 公告 PDF 仍可匿名读取。

## 官方来源

| 数据 | 官方入口 | 读取方式 |
| --- | --- | --- |
| 基金目录 API | [天弘全部基金](https://www.thfund.com.cn/fundlist) 前端调用 `/thfund/fundlist/api?limit=any_limit&type=0` | 公开 JSON；当前网络返回阿里云 WAF challenge 时停止 |
| 产品页 | `https://www.thfund.com.cn/fundinfo/{code}` | 公开 HTML；当前网络返回同一 WAF challenge 时停止 |
| 公告附件 | `https://cdn-thweb.tianhongjijin.com.cn/fundnotice/*.pdf` | 读取官网产品页公告链接对应的 PDF，解析公告正文 |

## 可获取字段

### 目录（`FundIdentity`）

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `code` | `fundCode` / `fund_code` | `str` | 六位基金/份额代码 |
| `name` | `fundName` / `fund_name` / `shortName` | `str` | 当前基金/份额名称 |
| `fund_type` | `fundType` / `fund_type` | `str` | 官网原始基金类型 |
| `share_class` | 名称末尾后缀 | `str` | A/C/D 等份额类别 |
| `source_url` | 代码拼接产品页 | `str` | 天弘官方产品页 |
| `source_type` | 固定值 | `str` | `tianhong_official_fund_catalogue_api` |

### 产品详情（`ProductSnapshot`）

| 标准字段 | 页面字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | 页面标题或 `h1` / `.fund-name` | `str` | 份额名称 |
| `full_name` | “基金名称”标签 | `str` | 基金全称 |
| `fund_type` | “基金类型”标签 | `str` | 官网基金类型 |
| `risk_level` | “风险等级”标签 | `str` | 官网风险等级 |
| `inception_date` | “成立日期”/“基金合同生效日” | `str` | 统一为 `YYYY-MM-DD` |
| `asset_scale` | “资产规模”标签 | `str` | 未公开时为空 |
| `net_value_date` | “净值日期”标签 | `str` | 最新净值日期 |
| `trade_status` | “交易状态”/“申购状态” | `str` | 正常、开放、暂停等原文 |
| `fields` | 产品页表格 | `dict[str, str]` | 保留产品页原始标签和值 |

### 公告限额

| 输出字段 | 来源/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `limit` | 官方 CDN 公告 PDF | `str/null` | 公告明确“直销机构”时优先采用直销个人金额 |
| `status` | 解析结果 | `str` | `ok` / `official_api_no_data` / `official_interface_requires_auth` |
| `quota_type` | 公告口径 | `str` | 直销机构单账户单日累计申购及定期定额投资 |
| `quota_remark` | 公告正文 | `str` | 保留直销/代销、个人/机构适用范围 |
| `source_url` | 官方 CDN PDF URL | `str` | 天弘官方公告附件 |
| `observed_at` | 运行时钟 | `str` | ISO 8601 观察时间 |
| `raw` | 公告解析结果 | `dict` | 保留标题、公告日期和正文摘要 |

状态规则：目标代码出现且公告暂停申购/定投 → `暂停`；恢复申购/恢复大额申购 → `不限`；无可验证官方 CDN 附件 → `official_api_no_data`，不从第三方或旧快照推断。

## 认证/WAF 边界

`auth_boundary()` 返回 `public_with_waf_boundary`：这不是交易认证，但官网当前公开
目录和产品页会返回阿里云 WAF JavaScript challenge。适配器不会绕过该挑战；需要
目录/产品实时字段时应由具备合规浏览器会话的运行环境处理。公告 CDN PDF 不受此
边界影响，仍可直接抓取。

## 当前实测（2026-08-26）

- `016665`：官方 2026-07-24 公告，直销个人投资者单日累计申购/定投上限 `1000元`。
- `018044`：官方 2026-05-29 公告，自 2026-06-01 起暂停申购及定期定额投资，结果为 `暂停`。
- 两份 PDF 均来自天弘官方 CDN；未使用东方财富、支付宝、天天基金或其他第三方数据。

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销状态/限额**——`016665=1000元`、`018044=暂停`（天弘官网产品页公告 CDN PDF）。
- 只有本 Adapter 本次从天弘官网公开公告附件解析出的当前值，才写入“已获取”；WAF、无公告、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
