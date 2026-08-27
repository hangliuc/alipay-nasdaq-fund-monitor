from datetime import datetime, timezone
from pathlib import Path
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.huaan import HuaanAdapter
from tests.helpers import FakeResponse, FakeSession


FIXTURES = Path(__file__).parent / "fixtures"


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class HuaanAdapterTests(unittest.TestCase):
    def test_catalogue_parser_reads_view_fund_links_and_deduplicates(self):
        funds = HuaanAdapter.parse_catalogue_html(
            (FIXTURES / "huaan_catalogue.html").read_bytes(),
            HuaanAdapter.catalogue_url,
        )

        self.assertEqual([fund.code for fund in funds], ["014978", "040046"])
        self.assertEqual(funds[0].name, "华安纳斯达克100ETF联接C")
        self.assertEqual(funds[0].share_class, "C")
        self.assertEqual(funds[0].source_type, "huaan_official_fund_catalogue")

    def test_discover_funds_uses_official_home_page(self):
        session = FakeSession({
            HuaanAdapter.catalogue_url: FakeResponse(
                content=(FIXTURES / "huaan_catalogue.html").read_bytes(),
                url=HuaanAdapter.catalogue_url,
            )
        })
        funds = HuaanAdapter(session=session, clock=fixed_clock).discover_funds()

        self.assertEqual(len(funds), 2)
        self.assertEqual(session.calls[0][1]["headers"]["Referer"], HuaanAdapter.catalogue_url)

    def _adapter(self, html_name="huaan_product.html"):
        url = HuaanAdapter.product_url_template.format(code="014978")
        session = FakeSession({
            url: FakeResponse(content=(FIXTURES / html_name).read_bytes(), url=url),
        })
        return HuaanAdapter(session=session, clock=fixed_clock), session

    def test_product_parser_extracts_direct_distribution_and_status(self):
        adapter, _ = self._adapter()
        product = adapter.fetch_product(FundIdentity("华安", "014978", "测试基金"))

        self.assertEqual(product.name, "华安纳斯达克100ETF联接（QDII）C")
        self.assertEqual(product.full_name, "华安纳斯达克100交易型开放式指数证券投资基金联接基金(QDII)")
        self.assertEqual(product.fund_type, "QDII")
        self.assertEqual(product.risk_level, "R4")
        self.assertEqual(product.inception_date, "2022-12-09")
        self.assertEqual(product.trade_status, "单日单账户限额直销100元 代销10元 限额申购 开放赎回 开放定投")
        self.assertEqual(product.fields["直销限额"], "100元")
        self.assertEqual(product.fields["代销限额"], "10元")

    def test_channel_limits_exposes_both_columns(self):
        adapter, _ = self._adapter()
        result = adapter.fetch_channel_limits(FundIdentity("华安", "014978", "测试基金"))

        self.assertEqual(result["direct_limit"], "100元")
        self.assertEqual(result["distribution_limit"], "10元")

    def test_direct_limit_reads_direct_and_keeps_distribution_raw(self):
        adapter, session = self._adapter()
        snapshot = adapter.fetch_direct_limit(FundIdentity("华安", "014978", "测试基金"))

        self.assertEqual(snapshot.limit, "100元")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.quota_type, "单日单账户限额")
        self.assertEqual(snapshot.raw["distribution_limit"], "10元")
        self.assertEqual(len(session.calls), 1)
        record = adapter.fetch_direct_sales_record(FundIdentity("华安", "014978", "测试基金"))
        self.assertEqual(record["direct_sales_limit"], "100元")
        self.assertIn("代销限额=10元", record["direct_sales_note"])

    def test_trade_status_maps_subscription_redemption_and_sip(self):
        adapter, _ = self._adapter()
        snapshot = adapter.fetch_trade_status(FundIdentity("华安", "014978", "测试基金"))
        channel = snapshot.channels[0]

        self.assertTrue(channel.subscription)
        self.assertTrue(channel.redemption)
        self.assertTrue(channel.sip)
        self.assertEqual(channel.limit, "100元")
        self.assertIn("代销限额=10元", channel.quota_remark)

    def test_missing_direct_limit_is_explicit_no_data(self):
        html = (FIXTURES / "huaan_product.html").read_text(encoding="utf-8").replace(
            "直销100元 代销10元", "直销未披露 代销未披露"
        ).encode("utf-8")
        url = HuaanAdapter.product_url_template.format(code="014978")
        adapter = HuaanAdapter(
            session=FakeSession({url: FakeResponse(content=html, url=url)}),
            clock=fixed_clock,
        )
        snapshot = adapter.fetch_direct_limit(FundIdentity("华安", "014978", "测试基金"))

        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")

    def test_auth_boundary_is_public(self):
        boundary = HuaanAdapter(clock=fixed_clock).auth_boundary()

        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)
        self.assertFalse(boundary.requires_bank_card)


if __name__ == "__main__":
    unittest.main()
