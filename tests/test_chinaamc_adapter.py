import json
from datetime import datetime, timezone
from pathlib import Path
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.chinaamc import ChinaAMCAdapter
from tests.helpers import FakeResponse, FakeSession


FIXTURES = Path(__file__).parent / "fixtures"


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ChinaAMCAdapterTests(unittest.TestCase):
    def test_catalogue_parser_maps_current_fund_list(self):
        funds = ChinaAMCAdapter.parse_catalogue_payload(load_json("chinaamc_catalogue.json"))

        self.assertEqual([fund.code for fund in funds], ["002891", "015300"])
        self.assertEqual(funds[1].fund_type, "8")
        self.assertEqual(funds[1].share_class, "C")
        self.assertEqual(funds[1].source_type, "chinaamc_official_fund_catalogue_api")

    def test_discover_funds_uses_public_json_api(self):
        session = FakeSession({
            ChinaAMCAdapter.catalogue_api_url: FakeResponse(payload=load_json("chinaamc_catalogue.json")),
        })
        funds = ChinaAMCAdapter(session=session, clock=fixed_clock).discover_funds()

        self.assertEqual(len(funds), 2)
        self.assertEqual(session.calls[0][1]["params"]["pageSize"], 2000)
        self.assertEqual(session.calls[0][1]["headers"]["X-Requested-With"], "XMLHttpRequest")

    def test_product_parser_extracts_current_status_and_fields(self):
        snapshot = ChinaAMCAdapter.parse_product_html(
            (FIXTURES / "chinaamc_product.html").read_bytes(),
            "015300",
            "https://www.chinaamc.com/fund/015300/index.shtml",
            fixed_clock().isoformat(),
        )

        self.assertEqual(snapshot.name, "华夏纳斯达克100ETF发起式联接(QDII)C")
        self.assertEqual(snapshot.full_name, "华夏纳斯达克100交易型开放式指数证券投资基金发起式联接基金（QDII）")
        self.assertEqual(snapshot.fund_type, "指数型")
        self.assertEqual(snapshot.risk_level, "中高风险(R4)")
        self.assertEqual(snapshot.inception_date, "2022-04-14")
        self.assertEqual(snapshot.net_value_date, "2026-08-24")
        self.assertEqual(snapshot.trade_status, "暂停申购")

    def test_calendar_parser_distinguishes_pause_and_amount(self):
        rows = ChinaAMCAdapter.parse_calendar_html(
            (FIXTURES / "chinaamc_calendar.html").read_bytes(),
            source_url=ChinaAMCAdapter.calendar_url,
            observed_at=fixed_clock().isoformat(),
        )

        self.assertEqual(rows["015300"]["status"], "暂停")
        self.assertEqual(rows["015300"]["limit"], "")
        self.assertEqual(rows["002891"]["status"], "开放-有限制")
        self.assertEqual(rows["002891"]["limit"], "1万元")

    def _adapter(self, calendar_name="chinaamc_calendar.html"):
        product_url = ChinaAMCAdapter.product_url_template.format(code="015300")
        session = FakeSession({
            product_url: FakeResponse(content=(FIXTURES / "chinaamc_product.html").read_bytes()),
            ChinaAMCAdapter.calendar_url: FakeResponse(content=(FIXTURES / calendar_name).read_bytes()),
        })
        return ChinaAMCAdapter(session=session, clock=fixed_clock), session

    def test_direct_limit_uses_calendar_as_current_official_limit(self):
        adapter, session = self._adapter()
        snapshot = adapter.fetch_direct_limit(FundIdentity("华夏", "002891", "测试基金"))

        self.assertEqual(snapshot.limit, "1万元")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.channel, "华夏官网公开交易状态")
        self.assertIn("所有投资人", snapshot.quota_remark)
        self.assertEqual(len(session.calls), 1)

    def test_pause_is_a_valid_direct_sales_result(self):
        adapter, _ = self._adapter()
        snapshot = adapter.fetch_direct_limit(FundIdentity("华夏", "015300", "测试基金"))

        self.assertEqual(snapshot.limit, "暂停")
        self.assertEqual(snapshot.status, "ok")
        record = adapter.fetch_direct_sales_record(FundIdentity("华夏", "015300", "测试基金"))
        self.assertEqual(record["direct_sales_limit"], "暂停")

    def test_open_without_amount_is_not_invented_as_unlimited(self):
        adapter, _ = self._adapter("chinaamc_calendar_no_amount.html")
        snapshot = adapter.fetch_direct_limit(FundIdentity("华夏", "024239", "测试基金"))

        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")

    def test_auth_boundary_is_public(self):
        boundary = ChinaAMCAdapter(clock=fixed_clock).auth_boundary()

        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)
        self.assertFalse(boundary.requires_bank_card)


if __name__ == "__main__":
    unittest.main()
