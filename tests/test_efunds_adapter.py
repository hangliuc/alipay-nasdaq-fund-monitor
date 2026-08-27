import json
from datetime import datetime, timezone
from pathlib import Path
import unittest

from fund_monitor.fetch.direct_sales.adapters.efunds import EFundsAdapter
from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from tests.helpers import FakeResponse, FakeSession


FIXTURES = Path(__file__).parent / "fixtures"


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class EFundsAdapterTests(unittest.TestCase):
    def test_catalogue_parser_only_accepts_all_fund_table_and_deduplicates(self):
        html = (FIXTURES / "efunds_supermarket.html").read_bytes()
        funds = EFundsAdapter.parse_catalogue_html(html)

        self.assertEqual([fund.code for fund in funds], ["012870", "110022"])
        self.assertEqual(funds[0].manager_id, "易方达")
        self.assertEqual(funds[0].fund_type, "ETF联接基金")
        self.assertEqual(funds[0].share_class, "C")
        self.assertEqual(
            funds[1].source_url,
            "https://www.efunds.com.cn/html/fund/110022_fundinfo.htm",
        )

    def test_discover_funds_uses_official_catalogue(self):
        session = FakeSession({
            EFundsAdapter.catalogue_url: FakeResponse(
                content=(FIXTURES / "efunds_supermarket.html").read_bytes()
            )
        })
        adapter = EFundsAdapter(session=session, clock=fixed_clock)

        funds = adapter.discover_funds()

        self.assertEqual(len(funds), 2)
        self.assertEqual(session.calls[0][0], EFundsAdapter.catalogue_url)
        self.assertEqual(session.calls[0][1]["headers"]["User-Agent"].startswith("Mozilla"), True)

    def test_product_parser_extracts_product_fields_and_pause_status(self):
        snapshot = EFundsAdapter.parse_product_html(
            (FIXTURES / "efunds_product.html").read_bytes(),
            "012870",
            "https://www.efunds.com.cn/fund/012870.shtml",
            fixed_clock().isoformat(),
        )

        self.assertEqual(snapshot.code, "012870")
        self.assertEqual(snapshot.name, "易方达纳斯达克100ETF联接（QDII-LOF）C（人民币份额）")
        self.assertEqual(snapshot.fund_type, "ETF联接基金")
        self.assertEqual(snapshot.risk_level, "中风险(R3)")
        self.assertEqual(snapshot.inception_date, "2021-07-23")
        self.assertEqual(snapshot.asset_scale, "19.63 亿元")
        self.assertEqual(snapshot.net_value_date, "2026-08-24")
        self.assertEqual(snapshot.trade_status, "暂停申购")
        self.assertEqual(snapshot.fields["基金经理"], "伍臣东")

    def test_trade_status_keeps_all_channels_and_direct_limit(self):
        url = EFundsAdapter.trade_status_url_template.format(code="012870")
        session = FakeSession({
            url: FakeResponse(payload=load_json("efunds_trade_status_open.json"))
        })
        adapter = EFundsAdapter(session=session, clock=fixed_clock)
        fund = FundIdentity("易方达", "012870", "测试基金")

        trade = adapter.fetch_trade_status(fund)
        direct = adapter.fetch_direct_limit(fund)

        self.assertEqual(len(trade.channels), 4)
        self.assertEqual(trade.channels[0].channel, "网上直销")
        self.assertTrue(trade.channels[0].subscription)
        self.assertEqual(direct.channel, "网上直销")
        self.assertEqual(direct.limit, "10万元")
        self.assertEqual(direct.quota_type, "单日累计")
        self.assertEqual(direct.status, "ok")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1]["params"]["date"], "2026-08-26")

    def test_direct_limit_marks_pause_without_using_old_value(self):
        payload = load_json("efunds_trade_status_open.json")
        payload["data"]["individual"][0]["subscription"] = False
        url = EFundsAdapter.trade_status_url_template.format(code="012870")
        adapter = EFundsAdapter(
            session=FakeSession({url: FakeResponse(payload=payload)}),
            clock=fixed_clock,
        )
        snapshot = adapter.fetch_direct_limit(FundIdentity("易方达", "012870", "测试基金"))

        self.assertEqual(snapshot.limit, "暂停")
        self.assertEqual(snapshot.status, "ok")

    def test_missing_direct_row_is_explicit_no_data(self):
        url = EFundsAdapter.trade_status_url_template.format(code="999999")
        adapter = EFundsAdapter(
            session=FakeSession({
                url: FakeResponse(payload=load_json("efunds_trade_status_no_direct.json"))
            }),
            clock=fixed_clock,
        )
        fund = FundIdentity("易方达", "999999", "测试基金")

        snapshot = adapter.fetch_direct_limit(fund)

        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")
        self.assertIsNone(adapter.fetch_direct_sales_record(fund))

    def test_auth_boundary_is_public_and_not_an_auth_bypass(self):
        boundary = EFundsAdapter(clock=fixed_clock).auth_boundary()

        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)
        self.assertFalse(boundary.requires_bank_card)

    def test_config_identity_is_compatibility_only(self):
        fund = EFundsAdapter.identity_from_config({
            "code": "012870",
            "name": "易方达纳斯达克100ETF联接(QDII-LOF)C",
        })

        self.assertEqual(fund.source_type, "config_compatibility")
        self.assertEqual(fund.code, "012870")
        self.assertEqual(fund.share_class, "C")


if __name__ == "__main__":
    unittest.main()
