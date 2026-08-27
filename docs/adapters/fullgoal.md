# 富国基金 Adapter

## 结论

富国已经接入独立的 `FullgoalAdapter`。它可以匿名发现富国官网公开目录中的全部
当前基金份额，并读取每只产品页的资料、净值和购买/定投按钮状态；直销限额通过
产品页公告列表定位最新“大额申购/定投”公告，再下载富国官网 PDF 解析。

2026-08-26 实测：官网目录 API 返回 **884 只**公开基金/份额，包含配置中的
`022184`；该基金产品页显示购买、定投按钮可用，最新相关公告为 2026-06-05，
公告 PDF 对 C 类（022184）的限制申购金额为 **1,000 元**。

## 官方来源

| 用途 | 官方来源 | 说明 |
| --- | --- | --- |
| 基金目录 | [`getFundList`](https://www.fullgoal.com.cn/ws-business-server/fund/getFundList) | 富国官网前端公开 JSON 接口，分页参数 `pageNum/pageSize`，返回当前公开基金份额、名称、类型、风险、净值摘要 |
| 目录页面 | [富国旗下基金](https://www.fullgoal.com.cn/main/fund/index.html) | 目录接口的官方页面入口；HTML 只作为接口不可用时的有限兜底 |
| 产品详情 | `https://www.fullgoal.com.cn/fundDetail/{code}/index.html` | 基金全称、简称、代码、管理人、托管人、份额生效日、类型、风险、当前净值 |
| 公告列表 | 产品页内 `ul.notice_list` | 按官网页面顺序列出公告；筛选标题中的“大额申购/大额定投/定期定额投资”及“暂停/调整/恢复” |
| 公告详情/PDF | [富国公告详情](https://www.fullgoal.com.cn/noticedetails/105775/index.html) → 官方 `wbs-file` PDF | 下载公告附件，按份额代码列与限制申购金额列对齐解析 |

以上全部是富国基金官方域名，不使用东方财富、支付宝、天天基金等第三方平台，
也不访问需要登录的富国网上交易入口。

## 可获取字段

### 1. 基金目录

| 标准字段 | 官网字段 | 说明 |
| --- | --- | --- |
| `FundIdentity.code` | `productCode` | 六位基金/份额代码 |
| `FundIdentity.name` | `productAbbr` / `productName` | 份额简称；API 返回的当前公开名称 |
| `FundIdentity.fund_type` | `productTypeText` | 主动股票型、股票指数型、混合型、债券型、FOF、QDII、货币型等 |
| `FundIdentity.share_class` | 份额简称末尾 A/B/C/Y 等 | 可识别时输出份额后缀 |
| `FundIdentity.source_url` | 代码拼接产品页 | 富国官方产品页 |
| `FundIdentity.source_type` | — | `fullgoal_official_fund_catalogue_api` |

目录使用 `pageSize=1000`，若官网返回多页会继续分页；实测接口返回 `total=884`。
目录数量表示当前官网公开列表，不推断历史上所有已发行或已清盘基金。

### 2. 产品页快照

| 标准字段 | 页面字段/规则 | 说明 |
| --- | --- | --- |
| `name` | `#fundAbbr` | 当前份额简称 |
| `full_name` | `#fundName` | 基金全称 |
| `fund_type` | `.fund_tag_01` 或“基金类型” | 官网展示类型 |
| `risk_level` | `.fund_tag_02` 或“基金风险等级” | 例如 `中高风险(R4)` |
| `inception_date` | “基金份额生效日”/“基金合同生效日” | 统一为 `YYYY-MM-DD` |
| `net_value_date` | `单位净值(YYYY-MM-DD)` | 当前产品页净值日期 |
| `asset_scale` | 页面有明确“资产规模”时 | 未公开则为空，不猜测 |
| `fields` | 产品资料表、当前净值、按钮状态 | 保留原始字段，便于后续开源扩展 |

### 3. 交易状态

`TradeSnapshot` 只读取产品页公开的按钮状态，不跟随购买/定投链接：

| 页面按钮 | 输出 |
| --- | --- |
| `购买` 按钮存在且未标记 `disabled` | `subscription=true` |
| `购买` 按钮标记 `disabled` | `subscription=false` |
| `定投` 按钮存在且未标记 `disabled` | `sip=true` |
| `定投` 按钮标记 `disabled` | `sip=false` |
| 页面没有可判断按钮 | 对应字段为 `null` |

赎回状态不在该产品页公开按钮中，因此保持 `null`。按钮“可用”不等同于已经完成
交易，也不绕过登录；它只是官网产品页当前展示状态。

### 4. 直销限额

| 输出字段 | 规则 |
| --- | --- |
| `limit` | 在最新相关公告 PDF 中按“下属分级基金的交易代码”匹配目标代码，再读取“限制申购金额”；例如 `1,000.00` → `1000元` |
| `status` | 成功解析金额或明确恢复/暂停时为 `ok`；未解析到当前公告为 `official_api_no_data`；HTTP 401/403 为 `official_interface_requires_auth` |
| `quota_type` | `单日单个基金账户累计申购及定期定额投资` |
| `quota_remark` | 说明公告是否区分渠道、目标份额和公告口径 |
| `source_url` | 富国官网公告 PDF |
| `raw` | 产品页字段、公告标题、日期和 PDF 正文 |

公告选择规则：

1. 读取产品页当前 HTML 的 `notice_list`，页面顺序视为官网给出的新到旧顺序。
2. 过滤标题含“大额申购/大额定投/定期定额投资”，且含“暂停/调整/恢复”的公告。
3. 从第一条候选公告进入官方详情页，定位 `.pdf` 附件；若 PDF 无法解析，继续尝试下一条候选。
4. 公告明确恢复大额业务且没有金额时输出 `不限`；明确暂停但没有金额时输出 `暂停`。
5. 产品页可访问但没有当前可解析公告时输出 `official_api_no_data`，不沿用旧公告值。

富国公告通常没有把直销与非直销拆成两列。本项目约定：未使用第三方数据、且公告
来自基金公司官方披露时，按官网一手来源归入直销结果，并在 `quota_remark` 中保留
该事实；不把交易登录入口当作限额来源。

## 认证边界

目录 API、产品页、公告列表、公告详情和公开 PDF 当前均可匿名访问，
`auth_boundary()` 返回 `public`。若将来出现登录、验证码、设备签名或银行卡绑定，
适配器只返回 `official_interface_requires_auth`，不绕过或猜测。

## 代码和测试

- 实现：[fullgoal.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/fullgoal.py)
- 兼容编排入口：[announcements.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/announcements.py)
- 单元测试：[test_fullgoal_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_fullgoal_adapter.py)

```bash
python3 -m pytest -q tests/test_fullgoal_adapter.py
```

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`022184=1000元`（富国官网产品页定位的最新限额公告 PDF）。
- 只有本 Adapter 本次从富国官网产品页/公告 PDF 解析出的当前值，才写入“已获取”；公告缺失、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据。
