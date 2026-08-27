from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.igwfmc import IgwfmcAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


CATALOGUE = """
<html><body><table>
  <tr><td><a href="/main/jjcp/product/001975/detail.html">景顺长城环保优势股票</a></td>
      <td>股票型</td><td>R4 中高风险</td><td>正常</td></tr>
  <tr><td><a href="https://www.igwfmc.com/main/jjcp/product/001975/detail.html">景顺长城环保优势股票</a></td>
      <td>股票型</td><td>R4 中高风险</td><td>正常</td></tr>
  <tr><td><a href="/main/jjcp/product/012752/detail.html">景顺长城全球芯片股票C</a></td>
      <td>QDII</td><td>暂停申购</td></tr>
</table></body></html>
""".encode("utf-8")

PRODUCT = """
<html><body>
  <h1>景顺长城环保优势股票（基金代码：001975）</h1>
  <div>最新净值 4.6100 净值日期：2026-08-20</div>
  <div>申购状态：开放 | 赎回状态：开放 | 起购金额：1元起</div>
  <table><tr><th>基金全称</th><td>景顺长城环保优势股票型证券投资基金</td>
    <th>基金类型</th><td>股票型</td></tr>
  <tr><th>风险等级</th><td>中高风险</td><th>成立日期</th><td>2016年3月15日</td></tr>
  <tr><th>基金规模</th><td>3303401942.75元（2026-06-30）</td><th>基金经理</th><td>测试</td></tr></table>
</body></html>
""".encode("utf-8")


class IgwfmcAdapterTests(unittest.TestCase):
    def test_catalogue_parser_deduplicates_official_product_links(self):
        funds = IgwfmcAdapter.parse_catalogue_html(CATALOGUE, IgwfmcAdapter.catalogue_url)
        self.assertEqual([fund.code for fund in funds], ["001975", "012752"])
        self.assertEqual(funds[0].share_class, "")
        self.assertEqual(funds[1].share_class, "C")
        self.assertEqual(funds[0].source_type, IgwfmcAdapter.source_type_catalogue)
        self.assertTrue(funds[0].source_url.endswith("/001975/detail.html"))

    def test_product_parser_preserves_official_fields_and_current_status(self):
        product = IgwfmcAdapter.parse_product_html(
            PRODUCT,
            "001975",
            IgwfmcAdapter.product_url_template.format(code="001975"),
            fixed_clock().isoformat(),
        )
        self.assertEqual(product.name, "景顺长城环保优势股票")
        self.assertEqual(product.full_name, "景顺长城环保优势股票型证券投资基金")
        self.assertEqual(product.fund_type, "股票型")
        self.assertEqual(product.risk_level, "中高风险")
        self.assertEqual(product.inception_date, "2016-03-15")
        self.assertEqual(product.net_value_date, "2026-08-20")
        self.assertEqual(product.fields["基金经理"], "测试")
        self.assertEqual(product.trade_status, "申购开放；赎回开放")

    def test_discover_funds_uses_official_catalogue(self):
        session = FakeSession({IgwfmcAdapter.catalogue_url: FakeResponse(CATALOGUE, url=IgwfmcAdapter.catalogue_url)})
        adapter = IgwfmcAdapter(session=session, clock=fixed_clock)
        self.assertEqual(len(adapter.discover_funds()), 2)
        self.assertEqual(session.calls[0][0], IgwfmcAdapter.catalogue_url)

    def test_notice_parser_maps_share_code_and_direct_limit(self):
        text = (
            "公告基本信息 基金代码 012751 012752。"
            "下属分级基金的交易代码 012751 012752 "
            "下属分级基金的限制申购金额（单位：人民币元） 100.00 1,000.00。"
        )
        parsed = IgwfmcAdapter.parse_announcement_text(
            text, "012752", "调整大额申购（含定投）限制的公告", "2026年8月20日", "https://www.igwfmc.com/a.pdf"
        )
        self.assertEqual(parsed["limit"], "1000元")
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["announcement_date"], "2026-08-20")
        self.assertIn("source_url", parsed)

    def test_pause_and_resume_are_explicit_states(self):
        self.assertEqual(
            IgwfmcAdapter.parse_announcement_text(
                "基金代码 012752，本基金暂停大额申购和定期定额投资。", "012752", "暂停大额申购公告"
            )["limit"],
            "暂停",
        )
        self.assertEqual(
            IgwfmcAdapter.parse_announcement_text(
                "基金代码 012752，本基金恢复大额申购和定期定额投资。", "012752", "恢复大额申购公告"
            )["limit"],
            "不限",
        )

    def test_trade_status_uses_public_product_status_fields(self):
        url = IgwfmcAdapter.product_url_template.format(code="001975")
        adapter = IgwfmcAdapter(
            session=FakeSession({url: FakeResponse(PRODUCT, url=url)}),
            clock=fixed_clock,
        )
        snapshot = adapter.fetch_trade_status(FundIdentity("景顺长城", "001975", "测试"))
        self.assertTrue(snapshot.channels[0].subscription)
        self.assertTrue(snapshot.channels[0].redemption)
        self.assertEqual(snapshot.message, "申购开放；赎回开放")

    def test_direct_limit_no_current_notice_is_explicit_no_data(self):
        url = IgwfmcAdapter.product_url_template.format(code="001975")
        adapter = IgwfmcAdapter(
            session=FakeSession({url: FakeResponse(PRODUCT, url=url)}),
            clock=fixed_clock,
        )
        snapshot = adapter.fetch_direct_limit(FundIdentity("景顺长城", "001975", "测试"))
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")
        self.assertIsNone(adapter.fetch_direct_sales_record(FundIdentity("景顺长城", "001975", "测试")))

    def test_direct_limit_and_record_keep_official_notice_fields(self):
        url = IgwfmcAdapter.product_url_template.format(code="012752")
        adapter = IgwfmcAdapter(
            session=FakeSession({url: FakeResponse(PRODUCT, url=url)}),
            clock=fixed_clock,
        )
        adapter._fetch_notice = lambda code: {
            "limit": "1000元",
            "status": "ok",
            "quota_type": "直销渠道单日累计申购上限",
            "quota_remark": "公告正文明确直销中心。",
            "announcement_title": "调整大额申购公告",
            "announcement_date": "2026-08-20",
            "source_url": "https://www.igwfmc.com/a.pdf",
            "announcement_text": "直销中心上限1000元",
        }
        snapshot = adapter.fetch_direct_limit(FundIdentity("景顺长城", "012752", "测试"))
        self.assertEqual(snapshot.limit, "1000元")
        self.assertEqual(snapshot.status, "ok")
        self.assertIn("announcement_text", snapshot.raw)
        self.assertEqual(adapter.fetch_direct_sales_record(FundIdentity("景顺长城", "012752", "测试"))["direct_sales_limit"], "1000元")

    def test_auth_boundary_records_login_and_captcha(self):
        boundary = IgwfmcAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public_with_trade_auth_boundary")
        self.assertTrue(boundary.requires_login)
        self.assertTrue(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)
        self.assertFalse(boundary.requires_bank_card)


if __name__ == "__main__":
    unittest.main()
