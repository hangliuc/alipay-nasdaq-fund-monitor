# 大成基金 Adapter

## 适配器定位

`DachengAdapter` 复用大成官网前端公开的匿名 JSON 接口和公告 PDF。目录、产品
详情、公告列表均由大成官网返回；限额只采用最新的“大额申购/定投”公告，不保留
静态历史值。

## 官方来源

| 数据 | 官方入口 | 功能号/方式 |
| --- | --- | --- |
| 全量基金/份额目录 | [大成基金目录](https://www.dcfund.com.cn/main/fund/index.shtml) | `GET https://www.dcfund.com.cn/servlet/json?random=...`，`funcNo=742001` |
| 产品详情 | `https://www.dcfund.com.cn/main/fund/productdetail/index.shtml?product_code={code}` | 同一接口，`funcNo=742002` |
| 公告列表 | 同一接口 | `funcNo=742003`，传 `product_code` |
| 公告附件 | `https://www.dcfund.com.cn/plat_files/upload/.../*.pdf` | 从公告列表 `attachment_url` 取 PDF |

## 可获取字段

### 目录（`FundIdentity`）

| 标准字段 | 官网字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `code` | `product_code` / `fund_code` | `str` | 六位基金/份额代码 |
| `name` | `product_abbr` / `product_name` | `str` | 当前基金/份额名称 |
| `fund_type` | `product_type2` / `product_type` | `str` | 官网基金类型 |
| `share_class` | 名称末尾后缀 | `str` | A/C 等份额类别 |
| `source_url` | 代码拼接产品页 | `str` | 大成官方产品页 |
| `source_type` | 固定值 | `str` | `dacheng_official_fund_catalogue_api` |
| `raw` | 742001 原始行 | `dict` | 调用方可保存全部原始字段 |

### 产品详情（`ProductSnapshot`）

| 标准字段 | 742002 字段/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | `product_abbr` / `product_name` | `str` | 基金简称/份额名称 |
| `full_name` | `product_name` | `str` | 基金全称 |
| `fund_type` | `product_type2` / `product_type` | `str` | 基金类型 |
| `risk_level` | `p_risk_level_text` | `str` | 风险等级 |
| `inception_date` | `found_date` | `str` | 统一为 `YYYY-MM-DD` |
| `asset_scale` | `newest_asset` / `scale` | `str` | 官网原始规模值 |
| `net_value_date` | `nav_date` | `str` | 最新净值日期 |
| `trade_status` | `product_status_text` / `product_status` | `str` | 例如“正常” |
| `fields` | 742002 原始行 | `dict[str, str]` | 保留托管人、基金经理、单位净值、累计净值等全部字段 |

### 公告与直销限额

| 输出字段 | 来源/规则 | 类型 | 说明 |
| --- | --- | --- | --- |
| `limit` | 742003 最新相关公告 PDF | `str/null` | 优先解析“本公司直销渠道”金额，按份额代码对齐 |
| `status` | 解析结果 | `str` | `ok` / `official_api_no_data` / `official_interface_requires_auth` |
| `quota_type` | 公告口径 | `str` | 单日单个基金账户累计申购及定期定额投资 |
| `quota_remark` | 公告正文 | `str` | 保留直销渠道和适用范围说明 |
| `source_url` | `attachment_url` | `str` | 大成官方 PDF |
| `observed_at` | 运行时钟 | `str` | ISO 8601 观察时间 |
| `raw` | 产品字段 + 公告字段 | `dict` | 包含标题、公告日期和 PDF 正文 |

公告筛选规则：742003 按日期倒序筛选“调整/暂停/恢复”且涉及“大额申购/定期定额投资”；暂停输出 `暂停`，恢复输出 `不限`，没有当前有效公告输出 `official_api_no_data`。

## 认证边界

742001、742002、742003 和公告 PDF 当前均可匿名访问，`auth_boundary()` 返回
`public`。接口调用带官网前端同样的随机 query 参数和 Referer，但不需要账号、
验证码、设备签名或银行卡绑定。

## 当前实测（2026-08-26）

- 742001 返回 463 条公开基金/份额。
- `008971` 产品状态为“正常”，最新净值日期 2026-08-24。
- 最新公告为 2026-06-03 公告，明确自 2026-06-04 起大成直销渠道单日累计申购及
  定投不超过 `100元`。这会动态覆盖此前旧的 500 元公告值。

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销限额**——`008971=100元`（大成官网 2026-06-03 最新公告，直销渠道）。
- 只有本 Adapter 本次从大成官网公告 API/PDF 解析出的当前值，才写入“已获取”；没有当前公告、认证或网络失败均为“未获取/需认证”。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据；此前 `500元` 历史值不再使用。
