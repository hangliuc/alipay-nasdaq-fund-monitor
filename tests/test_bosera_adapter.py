from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.bosera import BoseraAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class BoseraAdapterTests(unittest.TestCase):
    def test_catalogue_parser_deduplicates_share_rows(self):
        payload = [
            {"fundCode": "016057", "shortName": "博时纳斯达克100ETF联接（QDII）C人民币", "fundTypeShow": "QDII"},
            {"fundCode": "016057", "shortName": "博时纳斯达克100ETF联接（QDII）C人民币", "fundTypeShow": "QDII"},
            {"subFundCode": "009271", "shortName": "博时信用优选债券A", "fundTypeShow": "债券"},
        ]
        funds = BoseraAdapter.parse_catalogue_payload(payload, BoseraAdapter.catalogue_url)
        self.assertEqual([fund.code for fund in funds], ["009271", "016057"])
        self.assertEqual(funds[1].share_class, "C")
        self.assertEqual(funds[1].source_type, BoseraAdapter.source_type_catalogue)

    def test_product_parser_reads_status_nav_and_fields(self):
        html = """
        <html><title>博时基金-测试</title><body>
          <div class="fund-essential"><div class="fund-essential_head">
            <h2 class="name">博时测试基金C人民币</h2><span class="code">016057</span>
            <span class="stint-money">暂停申购</span></div>
            <ul class="fund-essential_list">
              <li class="fund-essential_item">分类：<em>QDII</em></li>
              <li class="fund-essential_item">风险等级：<em>中高</em></li>
            </ul><div class="fund-essential_return"><span class="num">2.0610</span>
            <span class="text">单位净值 / 日涨幅 (2026-08-24)</span></div>
            <div class="fund-essential_action"><span class="btn-primary disabled">我要购买</span>
            <span class="btn-default disabled">定投</span></div>
          </div>
          <div id="fundInfo"><table><tr><td>基金名称</td><td>博时测试基金</td></tr>
          <tr><td>成立生效日期</td><td>2022年07月26日</td></tr></table></div>
        </body></html>
        """.encode()
        product = BoseraAdapter.parse_product_html(html, "016057", "https://example.test/fund/016057.html", fixed_clock().isoformat())
        self.assertEqual(product.name, "博时测试基金C人民币")
        self.assertEqual(product.fund_type, "QDII")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.trade_status, "暂停申购")
        self.assertEqual(product.fields["购买按钮状态"], "closed")

    def test_limit_parser_prefers_personal_limit_and_pause(self):
        parsed = BoseraAdapter.parse_limit_row({"limitLargeDesc": "个人单日基金账号限额1,000,000.00元；机构单日基金账号限额5,000,000.00元"})
        self.assertEqual(parsed["limit"], "100万元")
        paused = BoseraAdapter.parse_limit_row({"limitLargeDesc": "暂停申购"})
        self.assertEqual(paused["limit"], "暂停")
        self.assertIsNone(BoseraAdapter.parse_limit_row({"limitLargeDesc": ""}))

    def test_direct_limit_combines_catalogue_and_product_page(self):
        catalogue = "window.fundListJson = [{\"fundCode\":\"016057\",\"shortName\":\"测试C\",\"limitLargeDesc\":\"暂停申购\"}];".encode()
        product_url = BoseraAdapter.product_url_template.format(code="016057")
        product = '<div class="fund-essential_head"><h2 class="name">测试C</h2><span class="code">016057</span><span class="stint-money">暂停申购</span></div><div class="fund-essential_action"><span class="btn-primary disabled"></span><span class="btn-default disabled"></span></div>'.encode()
        session = FakeSession({BoseraAdapter.catalogue_url: FakeResponse(catalogue, BoseraAdapter.catalogue_url), product_url: FakeResponse(product, product_url)})
        adapter = BoseraAdapter(session=session, clock=fixed_clock)
        snapshot = adapter.fetch_direct_limit(FundIdentity("博时", "016057", "测试"))
        self.assertEqual(snapshot.limit, "暂停")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(len(session.calls), 2)

    def test_auth_boundary_is_public(self):
        boundary = BoseraAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)


if __name__ == "__main__":
    unittest.main()
