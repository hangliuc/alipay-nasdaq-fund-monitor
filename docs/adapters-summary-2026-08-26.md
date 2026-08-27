# 基金公司直销 Adapter 总结报告

## 1. 总体结论

- 已为注册表中的 **24 家基金公司全部建立独立 Adapter**，每家公司拥有独立模块、来源规则和单元测试。
- `config.json` 主动型与被动型合计 **37 只基金**，本次官网实测结果为：**33 只已获取直销限额/状态、3 只官网无当前可验证限额、1 只官方接口需认证**。
- 所有 Adapter 只访问基金公司自有官网、官网公开 API、官网产品页或官网公告附件；不使用东方财富、天天基金、支付宝等第三方数据回填。
- 认证边界（登录、密码、验证码、设备签名、银行卡绑定）只记录为“需认证”，不绕过。

## 2. Adapter 清单与配置基金结果

| 基金公司 | 独立 Adapter | 官方来源/能力 | 官网目录实测 | `config.json` 结果 | 结论 |
|---|---|---|---:|---|---|
| 易方达 | [efunds.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/efunds.py) | 交易状态 API，明确返回“网上直销” | 未统计 | 012870、012922、161128：暂停 | 已获取 3/3 |
| 国泰 | [guotai.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/guotai.py) | 官网直销产品页，直销单笔/累计限额 | 未统计 | 160213：50 元 | 已获取 1/1 |
| 广发 | [guangfa.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/guangfa.py) | 产品页、个人/机构限额 API、公告 PDF | 954 | 006479：5 元；021277：2000 元 | 已获取 2/2 |
| 华安 | [huaan.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/huaan.py) | 产品页明确直销/代销限额和交易状态 | 未统计 | 014978：100 元 | 已获取 1/1 |
| 华夏 | [chinaamc.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/chinaamc.py) | 产品页和开放状态表 | 未统计 | 015300：暂停；002891、024239：1 万元 | 已获取 3/3 |
| 南方 | [southern.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/southern.py) | `fundList`、`overreview`、交易状态 API | 975 | 016453：10 元 | 已获取 1/1 |
| 国富 | [guofu.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/guofu.py) | 产品页、信息披露加载接口和官方 PDF | 104 | 021662、021842：1000 元 | 已获取 2/2 |
| 富国 | [fullgoal.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/fullgoal.py) | 基金目录 API、产品页、公告 PDF | 884 | 022184：1000 元 | 已获取 1/1 |
| 宝盈 | [baoying.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/baoying.py) | 网上交易公开详情 API，读取 `td_sum_max_20/22` | 未统计 | 019737：10 元 | 已获取 1/1 |
| 博时 | [bosera.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/bosera.py) | `fundListJson` 目录和产品页公开摘要 | 763 | 016057：暂停 | 已获取 1/1 |
| 大成 | [dacheng.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/dacheng.py) | 742001/742002/742003 API 和公告 PDF | 463 | 008971：100 元 | 已获取 1/1 |
| 天弘 | [tianhong.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/tianhong.py) | 官方 CDN 公告 PDF；目录受 WAF 影响 | 目录受 WAF 阻断 | 016665：1000 元；018044：暂停 | 已获取 2/2 |
| 嘉实 | [jiashi.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/jiashi.py) | 741010/741011/741044 接口和官网限额表 | 727 | 016533：暂停；000043、017731：10 万元 | 已获取 3/3 |
| 招商 | [cmfchina.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/cmfchina.py) | 公开目录/详情 API 和产品公告 | 695 | 019548：100 元 | 已获取 1/1 |
| 华泰柏瑞 | [huatai_pb.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/huatai_pb.py) | `FundArr` 公开目录脚本 | 407 | 019525：10 元 | 已获取 1/1 |
| 摩根 | [morgan.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/morgan.py) | 基金超市、产品页、公告搜索/PDF | 299 | 019173：300 元 | 已获取 1/1 |
| 汇添富 | [valuetf.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/valuetf.py) | 官网目录、产品页和公告 PDF | 824 | 018967：10 元；015202：暂停 | 已获取 2/2 |
| 建信 | [jianxin.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/jianxin.py) | `fundList`、详情/公告 API、DOC 附件 | 357 | 012752：50 元；539002：10 万元 | 已获取 2/2 |
| 万家 | [wanjia.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/wanjia.py) | `FundArr`、产品页、交易状态 JSONP、公告 CMS/PDF | 415 | 019442：未获取 | 官网可访问，但无当前可验证限额 |
| 华宝 | [huabao.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/huabao.py) | `fundMarketList`、产品页和限额公告 PDF | 301 | 017204：1 万元；017437：10 万元；008254：50 万元 | 已获取 3/3 |
| 浦银安盛 | [puyan_axa.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/puyan_axa.py) | `sales/fundList`、`fund/info`、`notice/list`、官方 PDF | 424 | 014002：暂停 | 已获取 1/1 |
| 长城 | [changcheng.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/changcheng.py) | 基金目录、产品页、公告 JSON/HTML/PDF | 277 | 018036：未获取 | 官网可访问，但无当前可验证限额 |
| 景顺长城 | [igwfmc.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/igwfmc.py) | 基金目录、产品页、公告详情/PDF | 495 | 017093：未获取 | 官网可访问，但无当前可验证限额 |
| 银华 | [yinhu.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/adapters/yinhu.py) | 产品/公告入口和直销交易登录边界 | 受官网防护影响 | 016702：未获取 | 官方接口需账号、密码和图片验证码 |

> 注：上表的“官网目录实测”是 Adapter 能发现的基金/份额数量，不等同于当前配置基金的限额覆盖率；“未统计”表示该 Adapter 当前已能处理配置基金，但全量目录统计尚未单独固化。

## 3. 统一结果判定

每个 Adapter 的独立文档均包含“直销获取结论（统一判定）”章节，遵循以下规则：

1. 当前官方 API、产品页或生效公告明确给出金额、暂停或恢复状态，才写入“已获取”。
2. 产品页存在但没有当前可验证限额，写入 `official_api_no_data`，不把起购金额当作限额。
3. 登录、密码、验证码、设备签名或银行卡绑定是 `official_interface_requires_auth`，不绕过。
4. Adapter 失败不回退统一兼容层、历史静态值或第三方平台数据。

## 4. 实现与验证

- 共享导出入口：[direct_sales_official.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales_official.py)
- Adapter 注册表：[registry.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/registry.py)
- 编排器：[orchestrator.py](/Users/shareit/personal/qdii-quota-radar/fund_monitor/fetch/direct_sales/orchestrator.py)
- 当前全量单元测试：**182 passed**
- 当前官网实测：**37/37 基金均有明确结果状态**；其中 33 只已获取、3 只无当前字段、1 只需认证。

## 5. 后续重点

剩余工作不是新增公司 Adapter，而是提高已有 Adapter 的全基金产品页、交易状态和直销限额覆盖率。万家、长城、景顺长城应继续跟踪最新官方公告；银华应保持认证边界，除非获得合法授权 Token，否则不写入猜测值。
