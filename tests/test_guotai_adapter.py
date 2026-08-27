from datetime import datetime, timezone
from pathlib import Path
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.guotai import GuotaiAdapter
from tests.helpers import FakeResponse, FakeSession


FIXTURES = Path(__file__).parent / "fixtures"


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class GuotaiAdapterTests(unittest.TestCase):
    def test_catalogue_parser_reads_side_menu_and_deduplicates(self):
        funds = GuotaiAdapter.parse_catalogue_html(
            (FIXTURES / "guotai_catalogue.html").read_bytes(),
            GuotaiAdapter.catalogue_url,
        )

        self.assertEqual([fund.code for fund in funds], ["001645", "020007", "160213"])
        self.assertEqual(funds[2].name, "国泰纳斯达克100指数(QDII)")
        self.assertEqual(funds[2].share_class, "")
        self.assertEqual(funds[2].source_type, "guotai_official_fund_supermarket")

    def test_discover_funds_uses_official_supermarket_page(self):
        session = FakeSession({
            GuotaiAdapter.catalogue_url: FakeResponse(
                content=(FIXTURES / "guotai_catalogue.html").read_bytes(),
                url=GuotaiAdapter.catalogue_url,
            )
        })
        funds = GuotaiAdapter(session=session, clock=fixed_clock).discover_funds()

        self.assertEqual(len(funds), 3)
        self.assertTrue(session.calls[0][1]["headers"]["Referer"].startswith("https://e.gtfund.com"))

    def test_product_parser_extracts_limits_and_product_fields(self):
        snapshot = GuotaiAdapter.parse_product_html(
            (FIXTURES / "guotai_product.html").read_bytes(),
            "160213",
            GuotaiAdapter.product_url_template.format(code="160213"),
            fixed_clock().isoformat(),
        )

        self.assertEqual(snapshot.name, "国泰纳斯达克100指数(QDII)")
        self.assertEqual(snapshot.full_name, "国泰纳斯达克100指数证券投资基金")
        self.assertEqual(snapshot.fund_type, "海外")
        self.assertEqual(snapshot.risk_level, "中高（R4）")
        self.assertEqual(snapshot.inception_date, "2010-04-29")
        self.assertEqual(snapshot.net_value_date, "08-24")
        self.assertEqual(snapshot.trade_status, "未知")
        self.assertEqual(snapshot.fields["直销累计日限额"], "50元")

    def _adapter(self, html_name="guotai_product.html"):
        url = GuotaiAdapter.product_url_template.format(code="160213")
        session = FakeSession({
            url: FakeResponse(content=(FIXTURES / html_name).read_bytes(), url=url),
        })
        return GuotaiAdapter(session=session, clock=fixed_clock), session

    def test_direct_limit_prefers_daily_limit(self):
        adapter, session = self._adapter()
        snapshot = adapter.fetch_direct_limit(FundIdentity("国泰", "160213", "测试基金"))

        self.assertEqual(snapshot.limit, "50元")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.channel, "国泰官网直销")
        self.assertEqual(snapshot.quota_type, "累计日限额")
        self.assertEqual(len(session.calls), 1)
        record = adapter.fetch_direct_sales_record(FundIdentity("国泰", "160213", "测试基金"))
        self.assertEqual(record["direct_sales_limit"], "50元")

    def test_missing_limit_is_explicit_no_data(self):
        html = (FIXTURES / "guotai_product.html").read_text(encoding="utf-8").replace(
            "直销单笔限额50元 直销累计日限额50元", ""
        ).encode("utf-8")
        url = GuotaiAdapter.product_url_template.format(code="160213")
        adapter = GuotaiAdapter(
            session=FakeSession({url: FakeResponse(content=html, url=url)}),
            clock=fixed_clock,
        )

        snapshot = adapter.fetch_direct_limit(FundIdentity("国泰", "160213", "测试基金"))

        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")
        self.assertIsNone(adapter.fetch_direct_sales_record(FundIdentity("国泰", "160213", "测试基金")))

    def test_trade_status_does_not_guess_from_navigation_text(self):
        adapter, _ = self._adapter()
        snapshot = adapter.fetch_trade_status(FundIdentity("国泰", "160213", "测试基金"))

        self.assertIsNone(snapshot.channels[0].subscription)
        self.assertIn("未公开", snapshot.message)

    def test_auth_boundary_is_public(self):
        boundary = GuotaiAdapter(clock=fixed_clock).auth_boundary()

        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)
        self.assertFalse(boundary.requires_bank_card)


if __name__ == "__main__":
    unittest.main()
