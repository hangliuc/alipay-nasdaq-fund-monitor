from datetime import datetime, timezone
from pathlib import Path
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.huatai_pb import HuataiPBAdapter
from tests.helpers import FakeResponse, FakeSession


FIXTURES = Path(__file__).parent / "fixtures"


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class HuataiPBAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (FIXTURES / "huatai_pb_catalogue.js").read_bytes()

    def test_catalogue_parser_reads_fundarr_and_deduplicates(self):
        funds = HuataiPBAdapter.parse_catalogue_script(self.script)
        self.assertEqual([fund.code for fund in funds], ["001097", "019454", "019525"])
        target = next(fund for fund in funds if fund.code == "019525")
        self.assertEqual(target.name, "华泰柏瑞纳斯达克100ETF发起式联接(QDII)C")
        self.assertEqual(target.share_class, "C")
        self.assertEqual(target.fund_type, "指数型")
        self.assertEqual(target.source_type, HuataiPBAdapter.source_type_catalogue)
        self.assertEqual(target.source_url, "https://www.huatai-pb.com/products/zhishu/019525/index.html")

    def _adapter(self, status_code=200):
        session = FakeSession({
            HuataiPBAdapter.catalogue_url: FakeResponse(
                content=self.script,
                status_code=status_code,
                url=HuataiPBAdapter.catalogue_url,
            )
        })
        return HuataiPBAdapter(session=session, clock=fixed_clock), session

    def test_discover_funds_uses_official_script_once(self):
        adapter, session = self._adapter()
        funds = adapter.discover_funds()
        self.assertEqual(len(funds), 3)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][1]["headers"]["Referer"], HuataiPBAdapter.home_url)

    def test_product_snapshot_maps_public_fundarr_fields(self):
        adapter, _ = self._adapter()
        product = adapter.fetch_product(FundIdentity("华泰柏瑞", "019525", "测试基金"))
        self.assertEqual(product.name, "华泰柏瑞纳斯达克100ETF发起式联接(QDII)C")
        self.assertIn("纳斯达克100交易型", product.full_name)
        self.assertEqual(product.fund_type, "指数型")
        self.assertEqual(product.risk_level, "R3中风险")
        self.assertEqual(product.inception_date, "2023-10-19")
        self.assertEqual(product.asset_scale, "586640128.4100")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.trade_status, "正常开放")
        self.assertEqual(product.fields["limitmoney"], "每日10元")

    def test_direct_limit_uses_limitmoney_not_buy_point(self):
        adapter, _ = self._adapter()
        fund = FundIdentity("华泰柏瑞", "019525", "测试基金")
        snapshot = adapter.fetch_direct_limit(fund)
        self.assertEqual(snapshot.limit, "10元")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.quota_type, "产品页申购上限")
        self.assertEqual(snapshot.raw["buypoint"], "10")
        record = adapter.fetch_direct_sales_record(fund)
        self.assertEqual(record["direct_sales_limit"], "10元")

    def test_trade_status_maps_normal_and_preserves_raw_fields(self):
        adapter, _ = self._adapter()
        trade = adapter.fetch_trade_status(FundIdentity("华泰柏瑞", "019525", "测试基金"))
        channel = trade.channels[0]
        self.assertTrue(channel.subscription)
        self.assertTrue(channel.redemption)
        self.assertEqual(channel.limit, "10元")
        self.assertEqual(trade.message, "正常开放")
        self.assertEqual(trade.raw["limitmoney"], "每日10元")

    def test_pause_status_is_confirmed_even_without_currency_unit(self):
        adapter, _ = self._adapter()
        snapshot = adapter.fetch_direct_limit(FundIdentity("华泰柏瑞", "019454", "测试基金"))
        self.assertEqual(snapshot.limit, "暂停")
        self.assertEqual(snapshot.status, "ok")

    def test_open_without_limit_is_explicit_no_data(self):
        adapter, _ = self._adapter()
        snapshot = adapter.fetch_direct_limit(FundIdentity("华泰柏瑞", "001097", "测试基金"))
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")

    def test_auth_boundary_is_public(self):
        boundary = HuataiPBAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)
        self.assertFalse(boundary.requires_bank_card)

    def test_auth_error_is_not_bypassed(self):
        adapter, _ = self._adapter(status_code=403)
        snapshot = adapter.fetch_direct_limit(FundIdentity("华泰柏瑞", "019525", "测试基金"))
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_interface_requires_auth")
        record = adapter.fetch_direct_sales_record(FundIdentity("华泰柏瑞", "019525", "测试基金"))
        self.assertEqual(record["direct_sales_status"], "official_interface_requires_auth")


if __name__ == "__main__":
    unittest.main()
