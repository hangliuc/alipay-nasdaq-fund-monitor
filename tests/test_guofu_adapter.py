from datetime import datetime, timezone
from pathlib import Path
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.guofu import GuofuAdapter
from tests.helpers import FakeResponse, FakeSession


FIXTURES = Path(__file__).parent / "fixtures"


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class GuofuAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue_html = (FIXTURES / "guofu_catalogue.html").read_bytes()
        cls.product_html = (FIXTURES / "guofu_product.html").read_bytes()
        cls.notice_html = (FIXTURES / "guofu_notice_list.html").read_bytes()

    def test_catalogue_parser_reads_product_links_and_deduplicates(self):
        funds = GuofuAdapter.parse_catalogue_html(self.catalogue_html, "201008", GuofuAdapter.catalogue_api_url)
        self.assertEqual([fund.code for fund in funds], ["021662", "021842"])
        self.assertEqual(funds[1].fund_type, "QDII")
        self.assertEqual(funds[1].share_class, "C")
        self.assertEqual(funds[1].source_type, GuofuAdapter.source_type_catalogue)

    def test_discover_unions_all_category_endpoints(self):
        session = FakeSession({
            f"{GuofuAdapter.catalogue_api_url}": FakeResponse(
                content=self.catalogue_html,
                url=f"{GuofuAdapter.catalogue_api_url}?fund_type=201008",
            )
        })
        adapter = GuofuAdapter(session=session, clock=fixed_clock)
        # All categories intentionally share a fixture; the Adapter still de-duplicates.
        funds = adapter.discover_funds()
        self.assertEqual([fund.code for fund in funds], ["021662", "021842"])
        self.assertEqual(len(session.calls), len(GuofuAdapter.category_types))

    def _adapter(self):
        product_url = GuofuAdapter.product_url_template.format(code="021842")
        disclosure_url = GuofuAdapter.disclosure_url_template.format(code="021842", page=1)
        session = FakeSession({
            product_url: FakeResponse(content=self.product_html, url=product_url),
            disclosure_url: FakeResponse(content=self.notice_html, url=disclosure_url),
            "https://www.ftsfund.com/ftp/upload/file/demo.pdf": FakeResponse(content=b"%PDF-demo", url="https://www.ftsfund.com/ftp/upload/file/demo.pdf"),
        })
        return GuofuAdapter(session=session, clock=fixed_clock), session

    def test_product_parser_maps_current_status_and_product_fields(self):
        adapter, _ = self._adapter()
        product = adapter.fetch_product(FundIdentity("国富", "021842", "测试基金"))
        self.assertEqual(product.name, "国富全球科技互联混合（QDII）人民币C")
        self.assertIn("全球科技互联", product.full_name)
        self.assertEqual(product.fund_type, "QDII")
        self.assertEqual(product.risk_level, "中风险")
        self.assertEqual(product.inception_date, "2024-07-25")
        self.assertEqual(product.trade_status, "正常开放")
        self.assertEqual(product.fields["最新净值"], "6.5498")

    def test_trade_status_maps_normal_open(self):
        adapter, _ = self._adapter()
        trade = adapter.fetch_trade_status(FundIdentity("国富", "021842", "测试基金"))
        channel = trade.channels[0]
        self.assertTrue(channel.subscription)
        self.assertTrue(channel.redemption)
        self.assertEqual(trade.message, "正常开放")

    def test_notice_parser_prefers_direct_sales_amount(self):
        text = """下属分级基金的交易代码 006373 021842 006374 006843
下属分级基金的限制申购金额 100.00 元 100.00 元 100.00 美元 100.00 美元
通过直销机构单日每个基金账户的累计申购及定期定额投资人民币 A 类份额或 C 类份额的金额应等于或低于 1,000.00 元。
通过非直销机构单日每个基金账户的累计申购及定期定额投资人民币 A 类份额或 C 类份额的金额应等于或低于 100.00 元。"""
        parsed = GuofuAdapter.parse_announcement_text(text, "021842", "调整大额申购公告", "2026-08-20", "https://example.test/a.pdf")
        self.assertEqual(parsed["limit"], "1000元")
        self.assertEqual(parsed["status"], "ok")
        self.assertIn("直销", parsed["quota_remark"])
        self.assertEqual(parsed["announcement_date"], "2026-08-20")

    def test_direct_limit_fetches_latest_notice_pdf(self):
        adapter, session = self._adapter()
        # Replace the fake PDF with a parser-friendly response; PDF downloading is
        # isolated from the text parser in the unit test.
        adapter._fetch_notice = lambda code: {
            "limit": "1000元",
            "status": "ok",
            "quota_type": "单日每个基金账户累计申购及定期定额投资",
            "quota_remark": "公告明确区分直销机构，优先采用直销限额。",
            "announcement_title": "调整大额申购公告",
            "announcement_date": "2026-08-20",
            "source_url": "https://www.ftsfund.com/ftp/upload/file/demo.pdf",
        }
        snapshot = adapter.fetch_direct_limit(FundIdentity("国富", "021842", "测试基金"))
        self.assertEqual(snapshot.limit, "1000元")
        self.assertEqual(snapshot.status, "ok")
        self.assertIn("直销", snapshot.quota_remark)
        self.assertEqual(snapshot.source_url, "https://www.ftsfund.com/ftp/upload/file/demo.pdf")
        self.assertEqual(len(session.calls), 1)

    def test_open_without_notice_is_explicit_no_data(self):
        adapter, _ = self._adapter()
        adapter._fetch_notice = lambda code: None
        snapshot = adapter.fetch_direct_limit(FundIdentity("国富", "021842", "测试基金"))
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")

    def test_auth_boundary_is_public(self):
        boundary = GuofuAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)
        self.assertFalse(boundary.requires_bank_card)


if __name__ == "__main__":
    unittest.main()
