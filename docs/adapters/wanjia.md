# 万家基金 Adapter

## 结论

万家官网提供匿名可读的基金目录脚本、基金产品页、信息披露公告列表和公告 PDF。当前独立 `WanjiaAdapter` 只访问 `wjasset.com` 及其官方交易状态公开接口，不使用第三方平台，不登录、不提交交易，也不绕过验证码、设备签名或银行卡校验。

交易状态接口在不同公开部署中可能返回 JSONP、普通 JSON 或错误页；适配器只在返回成功码 `ETS-5BP0000` 且包含 `fundState` 时使用接口字段，否则保留产品页状态并将错误写入原始字段。

## 官方来源

| 用途 | 官方来源 | 适配器行为 |
| --- | --- | --- |
| 当前基金/份额目录 | `https://www.wjasset.com/common/index.html.js` | 解析 `FundArr`，只接受六位基金代码；每行的原始字段全部保留 |
| 产品页 | `https://www.wjasset.com/products/{kind}/{code}/index.html` | 使用目录返回的官方 URL，读取标题、公告分类 ID 和公开产品字段 |
| 交易状态 | `https://www.wjasset.com/wanjia-web/trade/trade!fundState?fundCode={code}` | 匿名 JSONP/JSON；不访问交易提交入口 |
| 公告列表 | `https://www.wjasset.com/common-web/cms/content/getContents` | 按产品页公告分类 ID 分页读取，筛选大额申购/定投调整、暂停、恢复公告 |
| 公告附件 | `https://www.wjasset.com/upload/pdf/...` | 仅下载公告列表返回的万家官网 PDF，并从 PDF 文本按份额代码解析限额 |
| 交易提交入口（边界） | `https://trade.wjasset.com/etrading/` | 不调用；可能要求登录或其他交易校验 |

## 输出和状态

`discover_funds()` 返回 `FundIdentity`，包括管理人、六位代码、份额后缀、基金类型、官方产品页 URL 和 `source_type`。`fetch_product()` 返回 `ProductSnapshot`；`fields` 同时保存 `FundArr` 每个原始字段、产品页标题、公告分类 ID 和规范化字段。

`fetch_trade_status()` 返回个人客户这一条万家官网渠道记录，并在 `TradeSnapshot.raw` / `ChannelTradeStatus.raw` 中保留交易接口字段。`subscription`、`redemption`、`sip` 只有官网明确给出可解释值时才填 `true/false`，未知保持 `null`。

`fetch_direct_limit()` 从最新可解析公告按当前份额代码选择金额。公告写明 `50 万` 时输出 `50万元`；“恢复大额申购/定投”输出 `不限`；明确暂停输出 `暂停`，状态枚举为 `paused`。没有当前可解析公告时不沿用历史值，并返回 `official_api_no_data`。

| 状态 | 含义 |
| --- | --- |
| `ok` | 官网公告/产品字段成功解析 |
| `paused` | 官网公告明确暂停大额申购/定投，可能同时给出金额上限 |
| `official_api_no_data` | 官网可访问，但当前响应未提供该份额可解析的限额 |
| `official_interface_requires_auth` | 官网目录、产品页或公告接口返回认证边界 |

所有快照都带 `source_url` 和 UTC `observed_at`。空字段不会被推断为“不限”。

## 认证边界和实测说明

公开产品页、公告 PDF 可通过官网搜索索引匿名读取。2026-08-26 在获授权网络环境中实测目录去重发现 415 只基金/份额，配置基金 `019442` 产品页可访问；但当前公告列表未解析出能按份额代码验证的最新大额申购/定投限额，因此 Adapter 返回 `official_api_no_data`，不会沿用旧公告值。适配器对运行时的 401/403、错误 JSONP、缺少公告分类或无可解析字段均显式记录。

同次实测 `019442` 的产品页交易状态为“正常开放”，匿名 `fundState` 返回申购和定投可用、赎回字段未公开。交易状态可用不代表已经取得当前直销限额。

实际申购/赎回/定投提交属于 `trade.wjasset.com` 交易入口，可能需要账户登录、验证码、设备校验或绑定银行卡。该入口不属于本 Adapter 的匿名采集范围，代码不会尝试绕过这些边界。

## 测试

```bash
python3 -m pytest -q tests/test_wanjia_adapter.py
```

实现：[wanjia.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/wanjia.py)

## 直销获取结论（统一判定）

- `config.json` 目标：**未获取直销限额**——`019442` 产品页和交易状态可读（正常开放、申购/定投可用），但当前官网公告未解析出能按份额代码验证的限额，Adapter 返回 `official_api_no_data`。
- 只有本 Adapter 本次从万家官网公告/PDF 解析出的明确当前直销限额或暂停/不限状态，才写入“已获取”；本次没有可验证限额，不能以旧公告值替代。
- 失败时不回填统一兼容层、历史静态值或第三方平台数据；认证边界按官方接口返回状态记录。
