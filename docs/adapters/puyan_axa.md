# 浦银安盛基金 Adapter

## 结论

浦银安盛新版官网提供匿名公开的基金目录、产品详情和公告列表 API。`014002`
当前可以从浦银安盛官方公告 PDF 获取明确的直销申购状态：最新公告为
2026-08-20 发布、2026-08-21 生效，明确暂停该份额申购及定期定额投资，因此
当前直销结果为 **已获取：暂停**。

这不是第三方平台数据。实际交易入口需要登录，Adapter 不登录、不提交交易，
也不绕过验证码、设备签名或银行卡绑定。

## 官方来源

| 用途 | 官方 URL | 说明 |
| --- | --- | --- |
| 当前基金目录 | [`sales/fundList`](https://www.py-axa.com/middle-platform-gateway/wechatwork-product-biz/front/api/sales/fundList) | 官网销售页面公开的当前基金/份额目录；实测 424 个去重份额 |
| 产品详情 | [`fund/info?fundCode={code}`](https://www.py-axa.com/middle-platform-gateway/wechatwork-product-biz/front/api/fund/info?fundCode=014002) | 基金名称、类型、风险、规模、净值和官网交易状态 |
| 公告列表 | [`notice/list`](https://www.py-axa.com/middle-platform-gateway/wechatwork-content-biz/front/api/notice/list) | 按 `fundCode` 查询基金业务公告；POST JSON 请求 |
| 公告附件 | `https://www.py-axa.com/middle-platform-gateway/system-file-biz/resources/file/{attachmentPath}` | 公告列表返回的官方 PDF 附件 |

## 可获取字段

### 基金目录

| 输出字段 | 官网字段 | 说明 |
| --- | --- | --- |
| `code` | `fundCode` / `marketCode` | 六位基金/份额代码 |
| `name` | `fundName` | 份额名称 |
| `full_name` | `fullName` | 基金全称 |
| `share_class` | 名称后缀 | 从 A/B/C 等名称后缀识别 |
| `fund_type` | `investType_dictText` | 官网原始类型文本 |
| `risk_level` | `riskLevel_dictText` | 官网原始风险文本 |
| `setup_date` | `setupDate` | 统一为 `YYYY-MM-DD` |
| `fund_size` | `fundSize` | 官网返回的规模字段 |
| `fund_manager` | `fundManager` | 管理人字段 |
| `fund_custodian` | `fundCustodian` | 托管人字段 |
| `fund_state` / `open_state` | `fundState_dictText` / `openState_dictText` | 当前官网交易状态 |
| 原始字段 | API `result` 全部字段 | 保存在 `ProductSnapshot.fields`，嵌套字段 JSON 序列化 |

### 产品与交易状态

`fetch_product` 使用官方 `fund/info` API；`ProductSnapshot` 标准化基金名称、全称、
类型、风险、成立日期、规模、净值日期和交易状态，同时保留 API 原始字段。

`fetch_trade_status` 输出“个人客户 / 浦银安盛官网公开产品 API”渠道。只有官网状态
明确出现“暂停申购”或“正常开放”等文本时才填写 `subscription`；不从缺失字段
推断赎回或定投状态。

### 直销限额/状态

Adapter 获取基金公告列表，按发布日期选择最新的“大额申购/暂停申购/恢复申购/定投”
类公告，读取同一官方域名的 PDF，并按公告中的份额代码定位：

| 公告明确内容 | `limit` | `status` |
| --- | --- | --- |
| 具体限制申购金额 | 人民币金额，例如 `3000元` | `ok` |
| 明确暂停申购/定期定额投资 | `暂停` | `ok` |
| 明确恢复申购/定投且未列金额 | `不限` | `ok` |
| 没有当前可验证限额/状态 | `null` | `official_api_no_data` |

产品页的起购金额不作为大额申购限额。公告中的“非直销销售机构”限额也不冒充直销；
只有正文明确包含“直销机构”或公告对该基金统一设定且未区分渠道时，才写入直销结果。

## 认证边界

| 状态 | 含义 |
| --- | --- |
| `public_with_trade_auth_boundary` | 目录、产品 API 和公告可匿名访问；实际购买入口需要登录 |
| `official_interface_requires_auth` | 官网公开接口返回登录/权限边界或 HTTP 401/403/412 |

不调用登录表单、不保存账号、不处理验证码，不绕过设备签名或银行卡绑定。

## 代码和测试

- 实现：[puyan_axa.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/puyan_axa.py)
- 单元测试：[test_puyan_axa_adapter.py](/Users/shareit/personal/qdii-quota-radar/tests/test_puyan_axa_adapter.py)

```bash
python3 -m pytest -q tests/test_puyan_axa_adapter.py
```

## 直销获取结论（统一判定）

- `config.json` 目标：**已获取直销状态——`014002=暂停`**（浦银安盛官网最新业务公告 PDF，公告发布日期 2026-08-20，生效日 2026-08-21）。
- 只有本 Adapter 当前从浦银安盛官方公开 API/PDF 解析出 `status=ok` 的直销限额或明确暂停/恢复状态，才写入“已获取”。
- API 无数据、公告无法解析、网络失败或需要认证均为“未获取/需认证”；不回退统一兼容层、历史静态值或第三方平台数据。
