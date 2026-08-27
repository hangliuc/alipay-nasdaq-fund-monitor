import json
from datetime import datetime, timezone
from pathlib import Path
import unittest

from fund_monitor.fetch.direct_sales.adapters.baoying import BaoyingAdapter
from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from tests.helpers import FakeResponse, FakeSession


FIXTURES = Path(__file__).parent / "fixtures"


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class BaoyingAdapterTests(unittest.TestCase):
    def test_sign_matches_public_frontend_rule(self):
        self.assertEqual(
            BaoyingAdapter.sign({"_msgid": "abc", "fundcode": "019737"}),
            "1E02BB9D8CD1701EEA265578232C32B5",
        )

    def test_open_session_sends_public_headers_and_extracts_token(self):
        session_url = BaoyingAdapter.api_base_url + BaoyingAdapter.session_path
        session = FakeSession({
            BaoyingAdapter.home_url: FakeResponse(content=b"home"),
            session_url: FakeResponse(payload=load_json("baoying_session.json")),
        })
        adapter = BaoyingAdapter(session=session, clock=fixed_clock)

        self.assertEqual(adapter.open_session(), "token-demo")
        self.assertEqual(session.calls[0][0], BaoyingAdapter.home_url)
        query = session.calls[1][1]["params"]
        self.assertTrue(query["_msgid"])
        self.assertEqual(session.calls[1][1]["headers"]["_sign"], BaoyingAdapter.sign(query))

    def test_discover_funds_reads_record_and_deduplicates(self):
        session_url = BaoyingAdapter.api_base_url + BaoyingAdapter.session_path
        list_url = BaoyingAdapter.api_base_url + BaoyingAdapter.catalogue_path
        session = FakeSession({
            BaoyingAdapter.home_url: FakeResponse(content=b"home"),
            session_url: FakeResponse(payload=load_json("baoying_session.json")),
            list_url: FakeResponse(payload=load_json("baoying_fund_list.json")),
        })
        funds = BaoyingAdapter(session=session, clock=fixed_clock).discover_funds()

        self.assertEqual([fund.code for fund in funds], ["019737", "213009"])
        self.assertEqual(funds[0].fund_type, "5")
        self.assertEqual(funds[0].share_class, "C")
        self.assertEqual(funds[0].source_type, "baoying_official_trade_fund_list")
        self.assertEqual(session.calls[2][1]["params"]["pagecount"], "1000")
        self.assertEqual(session.calls[2][1]["headers"]["token"], "token-demo")

    def _detail_adapter(self, detail_fixture):
        session_url = BaoyingAdapter.api_base_url + BaoyingAdapter.session_path
        detail_url = BaoyingAdapter.api_base_url + BaoyingAdapter.detail_path
        session = FakeSession({
            BaoyingAdapter.home_url: FakeResponse(content=b"home"),
            session_url: FakeResponse(payload=load_json("baoying_session.json")),
            detail_url: FakeResponse(payload=load_json(detail_fixture)),
        })
        return BaoyingAdapter(session=session, clock=fixed_clock), session

    def test_product_and_direct_limit_keep_raw_fields(self):
        adapter, session = self._detail_adapter("baoying_detail.json")
        fund = FundIdentity("宝盈", "019737", "测试基金")

        product = adapter.fetch_product(fund)
        direct = adapter.fetch_direct_limit(fund)
        trade = adapter.fetch_trade_status(fund)

        self.assertEqual(product.name, "宝盈纳斯达克100指数发起（QDII）C人民币")
        self.assertEqual(product.inception_date, "2024-01-15")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.fields["td_sum_max_20"], "100000.00")
        self.assertEqual(direct.limit, "10万元")
        self.assertEqual(direct.status, "ok")
        self.assertEqual(trade.channels[0].channel, "宝盈网上交易")
        self.assertTrue(trade.channels[0].subscription)
        # 首次详情请求建立 session，后续请求复用 token。
        self.assertEqual(len(session.calls), 5)

    def test_auth_boundary_is_explicit(self):
        adapter, _ = self._detail_adapter("baoying_auth_error.json")
        snapshot = adapter.fetch_direct_limit(FundIdentity("宝盈", "019737", "测试基金"))

        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_interface_requires_auth")
        self.assertEqual(adapter.fetch_direct_sales_record(FundIdentity("宝盈", "019737", "测试基金"))["direct_sales_status"], "official_interface_requires_auth")

    def test_empty_limits_are_not_invented_as_unlimited(self):
        adapter, _ = self._detail_adapter("baoying_no_limit.json")
        snapshot = adapter.fetch_direct_limit(FundIdentity("宝盈", "027662", "测试基金"))

        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")

    def test_auth_boundary_declares_no_bypass(self):
        boundary = BaoyingAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public_with_session_boundary")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)


if __name__ == "__main__":
    unittest.main()
