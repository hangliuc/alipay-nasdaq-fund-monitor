from datetime import datetime, timezone
from pathlib import Path
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.fullgoal import FullgoalAdapter
from tests.helpers import FakeResponse, FakeSession


FIXTURES = Path(__file__).parent / "fixtures"


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class FullgoalAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.product_html = (FIXTURES / "fullgoal_product.html").read_bytes()
        cls.notice_detail = (FIXTURES / "fullgoal_notice_detail.html").read_bytes()

    def test_catalogue_payload_maps_and_deduplicates_current_funds(self):
        payload = {"code": 0, "data": {"total": 2, "pages": 1, "list": [
            {"productCode": "022184", "productAbbr": "富国全球科技互联网股票（QDII）C", "productTypeText": "股票型"},
            {"productCode": "022184", "productAbbr": "富国全球科技互联网股票（QDII）C", "productTypeText": "股票型"},
            {"productCode": "100025", "productAbbr": "富国天时货币A", "productTypeText": "货币型"},
        ]}}
        funds = FullgoalAdapter.parse_catalogue_payload(payload, FullgoalAdapter.catalogue_api_url)
        self.assertEqual([fund.code for fund in funds], ["022184", "100025"])
        self.assertEqual(funds[0].share_class, "C")
        self.assertEqual(funds[0].source_type, FullgoalAdapter.source_type_catalogue)

    def test_discover_uses_official_json_api(self):
        payload = {"code": 0, "data": {"total": 1, "pages": 1, "list": [
            {"productCode": "022184", "productAbbr": "富国全球科技互联网股票（QDII）C", "productTypeText": "股票型"},
        ]}}
        session = FakeSession({FullgoalAdapter.catalogue_api_url: FakeResponse(
            url=FullgoalAdapter.catalogue_api_url, payload=payload,
        )})
        adapter = FullgoalAdapter(session=session, clock=fixed_clock)
        funds = adapter.discover_funds()
        self.assertEqual([fund.code for fund in funds], ["022184"])
        self.assertEqual(session.calls[0][1]["params"]["pageSize"], 1000)

    def test_product_parser_maps_nav_risk_inception_and_buttons(self):
        adapter = FullgoalAdapter(clock=fixed_clock)
        product = adapter.parse_product_html(self.product_html, "022184", FullgoalAdapter.product_url_template.format(code="022184"), adapter._observed_at())
        self.assertEqual(product.name, "富国全球科技互联网股票（QDII）C")
        self.assertEqual(product.full_name, "富国全球科技互联网股票型证券投资基金（QDII）")
        self.assertEqual(product.fund_type, "股票型")
        self.assertEqual(product.risk_level, "中高风险(R4)")
        self.assertEqual(product.inception_date, "2024-09-18")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.fields["最新净值"], "5.0156")
        self.assertEqual(product.trade_status, "购买可用；定投可用")

    def test_trade_status_does_not_open_transaction_entry(self):
        product_url = FullgoalAdapter.product_url_template.format(code="022184")
        session = FakeSession({product_url: FakeResponse(self.product_html, url=product_url)})
        trade = FullgoalAdapter(session=session, clock=fixed_clock).fetch_trade_status(FundIdentity("富国", "022184", "测试"))
        self.assertTrue(trade.channels[0].subscription)
        self.assertTrue(trade.channels[0].sip)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][0], product_url)

    def test_notice_list_skips_newer_non_limit_notice(self):
        candidates = FullgoalAdapter.parse_notice_list_html(self.product_html, FullgoalAdapter.product_url_template.format(code="022184"))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][1], "2026-06-05")
        self.assertIn("暂停大额申购", candidates[0][0])

    def test_announcement_parser_aligns_code_and_amount_columns(self):
        text = ("下属分级基金的交易代码 100055 022184 026228 "
                "该分级基金是否暂停大额申购、定期定额投资 是 是 是 "
                "下属分级基金的限制申购金额（单位：元） 1,000.00 1,000.00 1,000.00 "
                "下属分级基金的限制定期定额投资金额（单位：元） 1,000.00 1,000.00 1,000.00")
        parsed = FullgoalAdapter.parse_announcement_text(text, "022184", "暂停大额申购及定投", "2026 年 06 月 05 日", "https://example.test/a.pdf")
        self.assertEqual(parsed["limit"], "1000元")
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["announcement_date"], "2026-06-05")

    def _adapter(self, notice=None):
        product_url = FullgoalAdapter.product_url_template.format(code="022184")
        session = FakeSession({product_url: FakeResponse(self.product_html, url=product_url)})
        adapter = FullgoalAdapter(session=session, clock=fixed_clock)
        adapter._fetch_notice = lambda code, product_html=None, product_url="": notice
        return adapter, session

    def test_direct_limit_returns_latest_notice_result(self):
        adapter, _ = self._adapter({
            "limit": "1000元", "status": "ok", "quota_type": "单日单个基金账户累计申购及定期定额投资",
            "quota_remark": "公告按份额代码列出金额。", "source_url": "https://www.fullgoal.com.cn/demo.pdf",
        })
        snapshot = adapter.fetch_direct_limit(FundIdentity("富国", "022184", "测试"))
        self.assertEqual(snapshot.limit, "1000元")
        self.assertEqual(snapshot.status, "ok")
        self.assertIn("公告 PDF", snapshot.channel)

    def test_no_notice_is_explicit_no_data(self):
        adapter, _ = self._adapter(None)
        snapshot = adapter.fetch_direct_limit(FundIdentity("富国", "022184", "测试"))
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")

    def test_auth_boundary_is_public(self):
        boundary = FullgoalAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)
        self.assertFalse(boundary.requires_bank_card)


if __name__ == "__main__":
    unittest.main()
