from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.valuetf import ValuetfAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class ValuetfAdapterTests(unittest.TestCase):
    def test_catalogue_parser_reads_all_fund_rows(self):
        html = """
        <table><thead><tr><th>基金代码</th><th>基金名称</th><th>更新日期</th>
        <th>单位净值</th><th>累计净值</th><th>状态</th><th>网上交易</th></tr></thead>
        <tbody>
          <tr id="018967"><td>018967</td><td><a href="/main/products/pofund/018967/fundgk.shtml">汇添富纳斯达克100ETF发起式联接（QDII）C</a></td><td>2026-08-25</td><td>1.2</td><td>1.2</td><td>正常</td><td>申购 定投</td></tr>
          <tr id="bad"><td>bad</td></tr>
        </tbody></table>
        """.encode("gb18030")
        funds = ValuetfAdapter.parse_catalogue_html(html, ValuetfAdapter.catalogue_url)
        self.assertEqual([fund.code for fund in funds], ["018967"])
        self.assertEqual(funds[0].share_class, "C")
        rows = ValuetfAdapter.parse_catalogue_rows(html)
        self.assertEqual(rows[0]["当前状态"], "正常")

    def test_product_parser_reads_info_and_overview_fields(self):
        html = """
        <h1 class="H1Pro">汇添富全球移动互联混合（QDII）人民币C<span class="fundNum">( 基金代码 015202 )</span></h1>
        <table class="infotable">
          <tr><th>基金全称：</th><td>汇添富全球移动互联灵活配置混合型证券投资基金人民币C类份额</td></tr>
          <tr><th>基金简称：</th><td>汇添富全球移动互联混合（QDII）人民币C</td></tr>
          <tr><th>基金代码：</th><td>015202</td></tr>
          <tr><th>基金类型：</th><td>混合型</td></tr>
          <tr><th>成立日期：</th><td>2021年07月27日</td></tr>
          <tr><th>产品风险等级：</th><td>中高风险(R4)</td></tr>
        </table>
        <table><tr><th>基金类型</th><th>基金净值</th><th>涨跌幅</th><th>净值日期</th></tr>
          <tr><td>混合型</td><td>1.234</td><td>1.2%</td><td>2026-08-25</td></tr></table>
        <div id="buy"><a href="https://trade.99fund.com/trade/purchase-apply?fundId=015202"><img src="buy.png" /></a></div>
        """.encode("gb18030")
        product = ValuetfAdapter.parse_product_html(html, "015202", "https://example.test/info", fixed_clock().isoformat())
        self.assertEqual(product.name, "汇添富全球移动互联混合（QDII）人民币C")
        self.assertEqual(product.inception_date, "2021-07-27")
        self.assertEqual(product.net_value_date, "2026-08-25")
        self.assertEqual(product.fields["购买按钮状态"], "open")
        self.assertEqual(product.fields["定投按钮状态"], "unknown")

    def test_notice_parser_reads_amounts_by_trade_code(self):
        html = """
        <table class="sharetable"><tr><th>标 题</th><th>发布日期</th><th>附件下载</th></tr>
        <tr><td>· 关于汇添富全球移动互联混合调整大额申购、定期定额投资业务限制金额的公告</td><td>2026-08-11</td><td><a href="/announcement/zx/upload/2026/a.pdf">下载</a></td></tr>
        </table>
        """.encode("gb18030")
        rows = ValuetfAdapter.parse_notice_list_html(html, ValuetfAdapter.home_url)
        self.assertEqual(rows[0][1], "2026-08-11")
        self.assertTrue(rows[0][2].endswith("/announcement/zx/upload/2026/a.pdf"))
        text = "下属基金份额的交易代码 001668 015202 015203 006426 下属基金份额的限制申购金额 300.00 300.00 300.00 50.00"
        parsed = ValuetfAdapter.parse_announcement_text(text, "015202", rows[0][0], rows[0][1], rows[0][2])
        self.assertEqual(parsed["limit"], "300元")
        self.assertEqual(parsed["status"], "ok")
        usd_text = "金额单位 人民币元 美元 下属基金份额的交易代码 015202 006426 下属基金份额的限制申购金额 300.00 50.00"
        usd = ValuetfAdapter.parse_announcement_text(usd_text, "006426", rows[0][0], rows[0][1], rows[0][2])
        self.assertEqual(usd["limit"], "50美元")

    def test_notice_parser_reads_pause_without_amount(self):
        text = "下属基金份额的交易代码 001668 015202 015203 006426 该基金份额是否暂停上述业务 是 是 是 否"
        parsed = ValuetfAdapter.parse_announcement_text(text, "015202", "关于暂停申购、定期定额投资业务的公告")
        self.assertEqual(parsed["limit"], "暂停")
        self.assertEqual(parsed["status"], "paused")

    def test_direct_limit_uses_official_notice_and_auth_boundary_is_public(self):
        session = FakeSession({
            "https://www.99fund.com/main/products/pofund/015202/fundgg.shtml": FakeResponse(
                b"", "https://www.99fund.com/main/products/pofund/015202/fundgg.shtml"
            )
        })
        adapter = ValuetfAdapter(session=session, clock=fixed_clock)
        adapter._fetch_notice = lambda fund: {
            "limit": "300元", "status": "ok", "quota_type": "官网公告限制申购金额",
            "quota_remark": "按代码列出", "source_url": "https://www.99fund.com/a.pdf",
        }
        snapshot = adapter.fetch_direct_limit(FundIdentity("汇添富", "015202", "测试"))
        self.assertEqual(snapshot.limit, "300元")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(adapter.auth_boundary().status, "public")


if __name__ == "__main__":
    unittest.main()
