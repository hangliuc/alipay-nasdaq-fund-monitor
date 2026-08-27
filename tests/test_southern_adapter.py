from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.southern import SouthernAdapter
from tests.helpers import FakeResponse, FakeSession


FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class SouthernAdapterTests(unittest.TestCase):
    def test_catalogue_and_status_parsers_deduplicate_and_keep_sources(self):
        catalogue = SouthernAdapter.parse_catalogue_payload(load_json("southern_catalogue.json"))
        status = SouthernAdapter.parse_status_payload(load_json("southern_status.json"))
        self.assertEqual([fund.code for fund in catalogue], ["000001", "016453"])
        self.assertEqual(catalogue[1].source_type, SouthernAdapter.source_type_catalogue)
        self.assertEqual([fund.code for fund in status], ["000001", "000002", "016453"])
        self.assertEqual(status[-1].name, "南方纳斯达克100指数发起（QDII）C")

    def test_discover_unions_official_catalogue_and_current_status(self):
        session = FakeSession({
            SouthernAdapter.catalogue_api_url: FakeResponse(load_json("southern_catalogue.json")),
            SouthernAdapter.status_api_url: FakeResponse(load_json("southern_status.json")),
        })
        funds = SouthernAdapter(session=session, clock=fixed_clock).discover_funds()
        self.assertEqual([fund.code for fund in funds], ["000001", "000002", "016453"])
        self.assertEqual(session.calls[0][1]["headers"]["Referer"], SouthernAdapter.catalogue_url)
        self.assertEqual(session.calls[1][1]["headers"]["Referer"], SouthernAdapter.status_page_url)

    def _adapter(self, status_name="southern_status.json"):
        session = FakeSession({
            SouthernAdapter.status_api_url: FakeResponse(load_json(status_name)),
            SouthernAdapter.product_api_url: FakeResponse(load_json("southern_overreview.json")),
        })
        return SouthernAdapter(session=session, clock=fixed_clock), session

    def test_product_snapshot_maps_official_detail_fields(self):
        adapter, _ = self._adapter()
        product = adapter.fetch_product(FundIdentity("南方", "016453", "测试基金"))
        self.assertEqual(product.name, "南方纳斯达克100指数发起（QDII）C")
        self.assertIn("交易型开放式指数", product.full_name)
        self.assertEqual(product.fund_type, "股票型")
        self.assertEqual(product.risk_level, "中高风险(R4)")
        self.assertEqual(product.inception_date, "2022-11-29")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.asset_scale, "2057162940.08")
        self.assertEqual(product.fields["fundReturn.FUNDNAV"], "2.2465")
        self.assertEqual(product.trade_status, "开放申购 开放赎回 开放定投 开放转换转入 开放转换转出")

    def test_trade_status_and_limit_parse_current_remark(self):
        adapter, _ = self._adapter()
        fund = FundIdentity("南方", "016453", "测试基金")
        trade = adapter.fetch_trade_status(fund)
        channel = trade.channels[0]
        self.assertTrue(channel.subscription)
        self.assertTrue(channel.redemption)
        self.assertTrue(channel.sip)
        self.assertEqual(channel.limit, "10元")
        self.assertEqual(channel.quota_type, "大额申购（含定投和转换转入）")
        self.assertIn("限额调整为10元", channel.quota_remark)
        snapshot = adapter.fetch_direct_limit(fund)
        self.assertEqual(snapshot.limit, "10元")
        self.assertEqual(snapshot.status, "ok")
        record = adapter.fetch_direct_sales_record(fund)
        self.assertEqual(record["direct_sales_limit"], "10元")
        self.assertIn("当前日期=2026年08月26日", record["direct_sales_note"])

    def test_pause_without_amount_is_a_confirmed_status(self):
        adapter, _ = self._adapter()
        snapshot = adapter.fetch_direct_limit(FundIdentity("南方", "000001", "测试基金"))
        self.assertEqual(snapshot.limit, "暂停")
        self.assertEqual(snapshot.status, "ok")

    def test_open_without_amount_is_not_invented_as_unlimited(self):
        adapter, _ = self._adapter("southern_status_no_limit.json")
        snapshot = adapter.fetch_direct_limit(FundIdentity("南方", "000002", "测试基金"))
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")

    def test_auth_error_is_explicit_and_not_bypassed(self):
        session = FakeSession({
            SouthernAdapter.status_api_url: FakeResponse(load_json("southern_auth_error.json")),
        })
        adapter = SouthernAdapter(session=session, clock=fixed_clock)
        snapshot = adapter.fetch_direct_limit(FundIdentity("南方", "016453", "测试基金"))
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_interface_requires_auth")
        record = adapter.fetch_direct_sales_record(FundIdentity("南方", "016453", "测试基金"))
        self.assertEqual(record["direct_sales_status"], "official_interface_requires_auth")

    def test_auth_boundary_is_public(self):
        boundary = SouthernAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)
        self.assertFalse(boundary.requires_bank_card)


if __name__ == "__main__":
    unittest.main()
