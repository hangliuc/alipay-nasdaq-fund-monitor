# 直销限额一手来源抓取结果

- 实连日期：2026-08-26（按新增独立 Adapter 规则更新）
- 覆盖：`config.json` 全部 37 只基金、24 家管理人。
- 数据源：仅基金公司自有官网、官网公开 API、官网产品页、官网公告附件；不使用东方财富、天天基金、支付宝等第三方平台。
- 规则：官网来源未明确渠道时按约定归入直销；公告明确“直销渠道”的值优先。遇到登录、验证码、设备签名或银行卡绑定，不绕过，记录为“官方接口需认证”。
- 结果规则：只有独立 Adapter 当前成功解析的官方值才标记为“已获取”；产品页/公告无法验证当前直销限额时标记为“未获取”，不再回填统一兼容层或历史静态值。

## 汇总

| 结果类别 | 数量 |
|---|---:|
| 已获取直销限额/状态 | 33 |
| 官方直销交易入口需认证 | 1 |
| 官网产品页可访问但无直销限额字段 | 3 |
| 官方公开渠道暂未取到 | 0 |
| **合计** | **37** |

## 全部结果（已获取按直销结果排序，未获取统一置于最后）

### 排序总览

以下为本报告的主排序：先列已获取结果，暂停状态优先；金额结果按人民币限额从小到大；未获取/需认证统一放在最后。

| 类别 | 基金公司 | 代码 | 基金 | 直销结果 |
|---|---|---|---|---:|
| 已获取 | 浦银安盛 | 014002 | 浦银安盛全球智能科技(QDII)C | 暂停 |
| 已获取 | 华夏 | 015300 | 华夏纳斯达克100ETF联接(QDII)C | 暂停 |
| 已获取 | 嘉实 | 016533 | 嘉实纳斯达克100ETF联接(QDII)C | 暂停 |
| 已获取 | 天弘 | 018044 | 天弘纳斯达克100指数(QDII)C | 暂停 |
| 已获取 | 易方达 | 012870 | 易方达纳斯达克100ETF联接(QDII-LOF)C | 暂停 |
| 已获取 | 易方达 | 012922 | 易方达全球成长精选(QDII)C | 暂停 |
| 已获取 | 易方达 | 161128 | 易方达标普信息科技(QDII-LOF)A | 暂停 |
| 已获取 | 博时 | 016057 | 博时纳斯达克100ETF联接(QDII)C | 暂停 |
| 已获取 | 汇添富 | 015202 | 汇添富全球移动互联(QDII)C | 暂停 |
| 已获取 | 广发 | 006479 | 广发纳斯达克100ETF联接(QDII)C | 5元 |
| 已获取 | 南方 | 016453 | 南方纳斯达克100指数(QDII)C | 10元 |
| 已获取 | 汇添富 | 018967 | 汇添富纳斯达克100ETF联接(QDII)C | 10元 |
| 已获取 | 华泰柏瑞 | 019525 | 华泰柏瑞纳斯达克100ETF联接(QDII)C | 10元 |
| 已获取 | 宝盈 | 019737 | 宝盈纳斯达克100指数(QDII)C | 10元 |
| 已获取 | 建信 | 012752 | 建信纳斯达克100指数(QDII)C | 50元 |
| 已获取 | 国泰 | 160213 | 国泰纳斯达克100指数(QDII) | 50元 |
| 已获取 | 大成 | 008971 | 大成纳斯达克100ETF联接(QDII)C | 100元 |
| 已获取 | 华安 | 014978 | 华安纳斯达克100ETF联接(QDII)C | 100元 |
| 已获取 | 招商 | 019548 | 招商纳斯达克100ETF联接(QDII)C | 100元 |
| 已获取 | 摩根 | 019173 | 摩根纳斯达克100指数(QDII)C | 300元 |
| 已获取 | 天弘 | 016665 | 天弘全球高端制造(QDII)C | 1000元 |
| 已获取 | 国富 | 021662 | 国富亚洲机会股票(QDII)C | 1000元 |
| 已获取 | 国富 | 021842 | 国富全球科技互联(QDII)C | 1000元 |
| 已获取 | 富国 | 022184 | 富国全球科技互联网股票(QDII)C | 1000元 |
| 已获取 | 广发 | 021277 | 广发全球精选(QDII)C | 2000元 |
| 已获取 | 华夏 | 002891 | 华夏移动互联灵活配置混合(QDII) | 1万元 |
| 已获取 | 华夏 | 024239 | 华夏全球科技先锋(QDII)C | 1万元 |
| 已获取 | 华宝 | 017204 | 华宝海外科技(QDII-LOF)C | 1万元 |
| 已获取 | 华宝 | 017437 | 华宝纳斯达克精选(QDII)C | 10万元 |
| 已获取 | 嘉实 | 000043 | 嘉实美国成长(QDII) | 10万元 |
| 已获取 | 嘉实 | 017731 | 嘉实全球产业升级(QDII)C | 10万元 |
| 已获取 | 建信 | 539002 | 建信新兴市场混合(QDII)A | 10万元 |
| 已获取 | 华宝 | 008254 | 华宝致远混合(QDII)C | 50万元 |
| 未获取 | 万家 | 019442 | 万家纳斯达克100指数(QDII)C | 未获取 |
| 未获取 | 景顺长城 | 017093 | 景顺长城纳斯达克科技(QDII)C | 未获取 |
| 未获取 | 长城 | 018036 | 长城全球新能源车(QDII)C | 未获取 |
| 需认证 | 银华 | 016702 | 银华海外数字经济(QDII)C | 未获取 |

### 来源明细

下表保留每只基金的官方来源链接和解析说明；排序总览是本报告的主要阅读入口。

| 类别 | 基金公司 | 代码 | 基金 | 直销结果 | 官方渠道 | API/官网链接及说明 |
|---|---|---|---|---:|---|---|
| 已获取 | 华夏 | 015300 | 华夏纳斯达克100ETF联接(QDII)C | 暂停 | 官网开放状态表 | [状态表](https://fund.chinaamc.com/ProductForWeb/getCalendar) |
| 已获取 | 嘉实 | 016533 | 嘉实纳斯达克100ETF联接(QDII)C | 暂停 | 官网申购上限表 | [限额表](https://www.jsfund.cn/main/a/20151216/191092.shtml) |
| 已获取 | 天弘 | 018044 | 天弘纳斯达克100指数(QDII)C | 暂停 | 官网最新公告 PDF（产品页公告） | [公告 PDF](https://cdn-thweb.tianhongjijin.com.cn/fundnotice/574b5473d3485eeca8990102f6098ebd.pdf)：按份额代码列明暂停申购及定期定额投资 |
| 已获取 | 浦银安盛 | 014002 | 浦银安盛全球智能科技(QDII)C | 暂停 | 官网公告 API + 官方 PDF | [公告 PDF](https://www.py-axa.com/middle-platform-gateway/system-file-biz/resources/file/2090245031624511489.pdf)：2026-08-20 公告，2026-08-21 生效，明确暂停申购及定期定额投资 |
| 已获取 | 易方达 | 012870 | 易方达纳斯达克100ETF联接(QDII-LOF)C | 暂停 | 官网交易状态 API（网上直销） | [API](https://api.efunds.com.cn/xcowch/front/fund/tradestatus/012870) |
| 已获取 | 易方达 | 012922 | 易方达全球成长精选(QDII)C | 暂停 | 官网交易状态 API（网上直销） | [API](https://api.efunds.com.cn/xcowch/front/fund/tradestatus/012922) |
| 已获取 | 易方达 | 161128 | 易方达标普信息科技(QDII-LOF)A | 暂停 | 官网交易状态 API（网上直销） | [API](https://api.efunds.com.cn/xcowch/front/fund/tradestatus/161128) |
| 已获取 | 博时 | 016057 | 博时纳斯达克100ETF联接(QDII)C | 暂停 | 官网产品页 | [产品页](https://www.bosera.com/fund/016057.html)：产品摘要公开显示“暂停申购” |
| 已获取 | 广发 | 006479 | 广发纳斯达克100ETF联接(QDII)C | 5元 | 官网最新限额公告 PDF | [公告 PDF](https://www.gffunds.com.cn/jjgg/zdsj/202607/P020260720309586829944.pdf)：A/C 类业务限额 5 元 |
| 已获取 | 汇添富 | 018967 | 汇添富纳斯达克100ETF联接(QDII)C | 10元 | 官网最新限额公告 PDF | [公告 PDF](https://www.99fund.com/announcement/zx/upload/2026/20260716/ecd854ee54854a1f8841b03de46ef33e.pdf) |
| 已获取 | 华泰柏瑞 | 019525 | 华泰柏瑞纳斯达克100ETF联接(QDII)C | 10元 | 官网公开基金目录脚本 | [公开脚本](https://www.huatai-pb.com/common/index.html.js?v=)：`FundArr.limitmoney=每日10元`；本次脚本成功读取 |
| 已获取 | 宝盈 | 019737 | 宝盈纳斯达克100指数(QDII)C | 10元 | 官网网上交易公开 API | [基金详情 API](https://ibao.byfunds.com/agate/api/v1/fund/detail?fundcode=019737)：公开返回申购业务 20/22 限额字段 10.00 元 |
| 已获取 | 国泰 | 160213 | 国泰纳斯达克100指数(QDII) | 50元 | 官网直销产品页 | [产品页](https://e.gtfund.com/Etrade/Jijin/view/id/160213) |
| 已获取 | 建信 | 012752 | 建信纳斯达克100指数(QDII)C | 50元 | 官网公告 API + DOC 附件 | [API](https://www.ccbfund.cn/website/v1/api/fund/notice?fundCode=012752&keyword=%E6%9A%82%E5%81%9C&page=1) · [DOC](https://www.ccbfund.cn/resource/upload/notice/012752/202608191043/建信纳斯达克100指数型证券投资基金（QDII）暂停大额申购、定期定额投资公告.doc) |
| 已获取 | 华安 | 014978 | 华安纳斯达克100ETF联接(QDII)C | 100元 | 官网产品页 | [产品页](https://wap.huaan.com.cn/funds/014978/index.shtml) |
| 已获取 | 招商 | 019548 | 招商纳斯达克100ETF联接(QDII)C | 100元 | 官网直销限额公告 | [公告](https://www.cmfchina.com/web/noticedetails/225156/index.html) |
| 官网产品页可访问但无直销限额字段 | 景顺长城 | 017093 | 景顺长城纳斯达克科技(QDII)C | 未获取 | 独立 Adapter | [产品页](https://www.igwfmc.com/main/jjcp/product/017093/detail.html)：申购关闭、赎回开放；当前公告未解析到可验证限额 |
| 已获取 | 汇添富 | 015202 | 汇添富全球移动互联(QDII)C | 暂停 | 官网最新限额公告 PDF | [公告 PDF](https://www.99fund.com/announcement/zx/upload/2026/20260817/146c5b387f5d4cc18ddf8f4c0bb364f2.pdf)：当前公告明确暂停申购/定投 |
| 已获取 | 大成 | 008971 | 大成纳斯达克100ETF联接(QDII)C | 100元 | 官网直销限额公告 | [公告 PDF](https://www.dcfund.com.cn/plat_files/upload/ann_upload/20260602/202606021780401593666.pdf) |
| 已获取 | 摩根 | 019173 | 摩根纳斯达克100指数(QDII)C | 300元 | 官网直销渠道公告 | [公告 PDF](https://www.cifm.com/fund/019172/announce/202607/P020260709541510891782.pdf) |
| 官网产品页可访问但无直销限额字段 | 长城 | 018036 | 长城全球新能源车(QDII)C | 未获取 | 独立 Adapter | [产品页](https://www.ccfund.com.cn/main/jjcp/cache/018036.shtml)：本次公告接口未返回可确认当前限额；不沿用历史 500 元核验值 |
| 已获取 | 富国 | 022184 | 富国全球科技互联网股票(QDII)C | 1000元 | 官网产品页 → 最新限额公告 PDF | [公告 PDF](https://www.fullgoal.com.cn/wbs-file/fund_report/20260605/CN_50100000_100055_FC190100_20260020.pdf) |
| 已获取 | 国富 | 021662 | 国富亚洲机会股票(QDII)C | 1000元 | 官网产品页信息披露公告 PDF | [最新公告 PDF](https://www.ftsfund.com/ftp/upload/file/202608/03/cDK623Rsvv5PRT1G.pdf)：2026-08-04，本次从公开信息披露接口定位并解析 |
| 已获取 | 国富 | 021842 | 国富全球科技互联(QDII)C | 1000元 | 官网产品页信息披露公告 PDF | [最新公告 PDF](https://www.ftsfund.com/ftp/upload/file/202608/20/jSXGRm1nShiKMFYT.pdf)：2026-08-20，公告明确直销机构 1000 元 |
| 已获取 | 天弘 | 016665 | 天弘全球高端制造(QDII)C | 1000元 | 官网最新公告 PDF（产品页公告） | [公告 PDF](https://cdn-thweb.tianhongjijin.com.cn/fundnotice/7884d8fa6b0bf00ed0e6106ab693b556.pdf)：直销机构个人投资者单日累计上限 1000 元 |
| 已获取 | 广发 | 021277 | 广发全球精选(QDII)C | 2000元 | 官网最新限额公告 PDF | [公告 PDF](https://www.gffunds.com.cn/jjgg/zdsj/202608/P020260810316985575569.pdf)：A/C 类个人投资者业务限额 2000 元 |
| 已获取 | 华夏 | 002891 | 华夏移动互联灵活配置混合(QDII) | 1万元 | 官网开放状态表 | [状态表](https://fund.chinaamc.com/ProductForWeb/getCalendar) |
| 已获取 | 华夏 | 024239 | 华夏全球科技先锋(QDII)C | 1万元 | 官网开放状态表 | [状态表](https://fund.chinaamc.com/ProductForWeb/getCalendar) |
| 官网产品页可访问但无直销限额字段 | 万家 | 019442 | 万家纳斯达克100指数(QDII)C | 未获取 | 独立 Adapter | [产品页](https://www.wjasset.com/products/qdii/019442/index.html)：交易状态正常开放；当前公告未解析到可验证限额 |
| 已获取 | 华宝 | 017204 | 华宝海外科技(QDII-LOF)C | 1万元 | 官网直销限额公告 PDF | [公告 PDF](https://www.fsfund.com/webimages/upload2012/2025/09/29/6af2fb6f-cf7e-4b4d-8bef-3b13c8960206/华宝海外科技股票型证券投资基金（QDII-LOF）调整大额申购（含定投）金额上限的公告.pdf)：直销柜台/网上直销 1 万元 |
| 已获取 | 南方 | 016453 | 南方纳斯达克100指数(QDII)C | 10元 | 官网交易状态 API | [状态页/API](https://www.nffund.com/new/transaction-guide/product-status-and-limits.html)：当前公开接口返回大额申购（含定投和转换转入）限额 10 元 |
| 已获取 | 嘉实 | 000043 | 嘉实美国成长(QDII) | 10万元 | 官网申购上限表 | [限额表](https://www.jsfund.cn/main/a/20151216/191092.shtml) |
| 已获取 | 嘉实 | 017731 | 嘉实全球产业升级(QDII)C | 10万元 | 官网申购上限表 | [限额表](https://www.jsfund.cn/main/a/20151216/191092.shtml) |
| 已获取 | 建信 | 539002 | 建信新兴市场混合(QDII)A | 10万元 | 官网公告 API + DOC 附件 | [API](https://www.ccbfund.cn/website/v1/api/fund/notice?fundCode=539002&keyword=%E6%9A%82%E5%81%9C&page=1) · [DOC](https://www.ccbfund.cn/resource/upload/notice/539002/202608170852/建信新兴市场优选混合型证券投资基金暂停大额申购、定期定额投资公告.doc) |
| 已获取 | 华宝 | 017437 | 华宝纳斯达克精选(QDII)C | 10万元 | 官网直销限额公告 PDF | [公告 PDF](https://www.fsfund.com/static/notice/2026/02/09/be5ff786-dc58-411b-a250-20d2c47a101b/华宝纳斯达克精选股票型发起式证券投资基金（QDII）调整大额申购（含定投）金额上限的公告.pdf)：直销柜台/网上直销 10 万元 |
| 已获取 | 华宝 | 008254 | 华宝致远混合(QDII)C | 50万元 | 官网直销限额公告 PDF | [公告 PDF](https://www.fsfund.com/webimages/upload2012/2025/03/11/996336b3-c71b-4cc2-85ca-60135a20ed9b/华宝致远混合型证券投资基金（QDII）调整大额申购（含定投）金额上限的公告.pdf)：直销柜台/网上直销 50 万元 |
| 认证需登录 | 银华 | 016702 | 银华海外数字经济(QDII)C | 未获取 | 官网直销交易入口 | [登录页](https://trade.yhfund.com.cn/yhxntrade/account/goLogin.do)：统一直销交易入口要求登录、密码和图片验证码；不绕过 |

## 本轮新增实现

1. 广发：遍历官网临时公告分页，自动定位 006479、021277 的最新 A/C 类大额申购公告 PDF，分别得到 5 元、2000 元。
2. 国富：接入产品页公开 `/qxjj/jjxq/xxplload` 信息披露加载接口，解析 021662、021842 的最新官方 PDF；分别得到 1000 元，021842 公告还明确区分直销 1000 元与非直销 100 元。
3. 华宝：补入独立 Adapter 的官网公告 PDF；008254=50 万元、017204=1 万元、017437=10 万元。官网产品页 API 当前要求网点代码时，不绕过该认证边界。
4. 博时：读取官网产品页公开摘要；016057 直接显示“暂停申购”，无需登录。
5. 华泰柏瑞：读取官网 `FundArr.limitmoney` 公开目录脚本；本次成功解析 019525=每日 10 元；脚本不可读时保持未获取。
6. 建信、长城、天弘、宝盈及其他既有 Adapter 继续使用各自官网公告附件、公开交易 API 或产品页；当前无法验证时统一标记未获取，不回填旧值。
7. 浦银安盛：接入官网 `fundList`、产品详情、公告列表和官方 PDF；014002 实测解析为“暂停”，目录发现 424 个去重份额。
8. 银华：接入官网产品/公告入口和直销交易登录边界；016702 未获取限额，确认未登录状态需认证，不绕过登录、密码或图片验证码。
9. 原有第三方限额卡片字段未修改；直销编排已移除统一静态公告回退，只有独立 Adapter 成功结果才写入，失败保持未获取/需认证。

## 可复现文件

- 兼容入口：[fund_monitor/fetch/direct_sales_official.py](../fund_monitor/fetch/direct_sales_official.py)
- 结构化实现目录：[fund_monitor/fetch/direct_sales/](../fund_monitor/fetch/direct_sales/)
- 设计方案：[direct-sales-official-design.md](direct-sales-official-design.md)
