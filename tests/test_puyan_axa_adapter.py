from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.puyan_axa import PuyanAxaAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


CATALOGUE = {
    "success": True,
    "result": [
        {"fundCode": "014002", "fundName": "浦银全球智能科技（QDII）C", "fullName": "浦银全球智能科技股票型证券投资基金（QDII）C类", "investType_dictText": "QDII型"},
        {"fundCode": "014002", "fundName": "重复份额"},
        {"fundCode": "bad", "fundName": "忽略"},
    ],
}

PRODUCT = {
    "success": True,
    "result": {
        "fundCode": "014002",
        "fundName": "浦银全球智能科技（QDII）C",
        "fullName": "浦银全球智能科技股票型证券投资基金（QDII）C类",
        "investType_dictText": "QDII型",
        "riskLevel_dictText": "中高风险",
        "setupDate": "2021-11-03",
        "fundSize": "3119588548.93",
        "fundState_dictText": "暂停申购",
        "openState_dictText": "正常开放",
        "pdFdValue": {"releaseDate": "2026-08-24", "netValue": "3.3339"},
    },
}

NOTICE_PAYLOAD = {
    "success": True,
    "result": {
        "records": [
            {
                "id": "notice-1",
                "publishTime": "2026-08-20 09:10:30",
                "title": "关于浦银全球智能科技股票型证券投资基金（QDII）暂停申购及定期定额投资业务的公告",
                "attachments": [{"previewUrl": "https://www.py-axa.com/file/notice-1.pdf", "attachmentName": "notice.pdf"}],
            },
            {"publishTime": "2026-08-17 10:00:00", "title": "招募说明书更新", "attachments": []},
        ],
    },
}


class PuyanAxaAdapterTests(unittest.TestCase):
    def test_catalogue_parser_deduplicates_official_rows(self):
        funds = PuyanAxaAdapter.parse_catalogue_payload(CATALOGUE)
        self.assertEqual([fund.code for fund in funds], ["014002"])
        self.assertEqual(funds[0].share_class, "C")
        self.assertEqual(funds[0].source_type, PuyanAxaAdapter.source_type_catalogue)

    def test_product_parser_keeps_current_official_fields(self):
        fund = FundIdentity("浦银安盛", "014002", "测试")
        product = PuyanAxaAdapter.parse_product_payload(PRODUCT, fund, "https://www.py-axa.com/api", fixed_clock().isoformat())
        self.assertEqual(product.name, "浦银全球智能科技（QDII）C")
        self.assertEqual(product.full_name, "浦银全球智能科技股票型证券投资基金（QDII）C类")
        self.assertEqual(product.inception_date, "2021-11-03")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.trade_status, "暂停申购")
        self.assertEqual(product.fields["fundSize"], "3119588548.93")

    def test_discover_and_product_use_official_apis(self):
        session = FakeSession(
            gets={PuyanAxaAdapter.catalogue_url: FakeResponse(payload=CATALOGUE, url=PuyanAxaAdapter.catalogue_url)},
            posts={PuyanAxaAdapter.product_api_url: FakeResponse(payload=PRODUCT, url=PuyanAxaAdapter.product_api_url)},
        )
        adapter = PuyanAxaAdapter(session=session, clock=fixed_clock)
        funds = adapter.discover_funds()
        product = adapter.fetch_product(funds[0])
        self.assertEqual(len(funds), 1)
        self.assertEqual(product.code, "014002")
        self.assertEqual(session.calls[0][0], "GET")
        self.assertEqual(session.calls[1][0], "POST")
        self.assertEqual(session.calls[1][2]["params"], {"fundCode": "014002"})

    def test_trade_status_explicitly_marks_paused_subscription(self):
        session = FakeSession(posts={PuyanAxaAdapter.product_api_url: FakeResponse(payload=PRODUCT)})
        snapshot = PuyanAxaAdapter(session=session, clock=fixed_clock).fetch_trade_status(
            FundIdentity("浦银安盛", "014002", "测试")
        )
        self.assertFalse(snapshot.channels[0].subscription)
        self.assertIsNone(snapshot.channels[0].redemption)
        self.assertEqual(snapshot.message, "暂停申购")

    def test_announcement_parser_maps_code_to_pause_state(self):
        text = """公告基本信息 基金代码 006555 014002
        下属分级基金的交易代码 006555 014002
        该分级基金是否暂停申购及定期定额投资 是 是"""
        parsed = PuyanAxaAdapter.parse_announcement_text(
            text,
            "014002",
            "关于浦银全球智能科技股票型证券投资基金（QDII）暂停申购及定期定额投资业务的公告",
            "2026-08-20",
            "https://www.py-axa.com/file/notice.pdf",
        )
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["limit"], "暂停")
        self.assertIn("官网公告", parsed["quota_remark"])

    def test_announcement_parser_maps_numeric_limit_by_share_code(self):
        text = """下属分级基金的交易代码 006555 014002
        下属分级基金的限制申购金额（单位：人民币元） 1000.00 3000.00
        注：单日单个基金账户通过直销机构累计申购金额限额。"""
        parsed = PuyanAxaAdapter.parse_announcement_text(text, "014002", "调整大额申购限制公告")
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["limit"], "3000元")

    def test_fetch_direct_limit_downloads_latest_official_pdf(self):
        session = FakeSession(
            posts={PuyanAxaAdapter.notice_api_url: FakeResponse(payload=NOTICE_PAYLOAD)},
            gets={"https://www.py-axa.com/file/notice-1.pdf": FakeResponse(content=b"unused")},
        )
        adapter = PuyanAxaAdapter(session=session, clock=fixed_clock)
        adapter._fetch_notice_text = lambda notice: "基金代码 006555 014002。暂停申购及定期定额投资。"
        snapshot = adapter.fetch_direct_limit(FundIdentity("浦银安盛", "014002", "测试"))
        self.assertEqual(snapshot.limit, "暂停")
        self.assertEqual(snapshot.status, "ok")
        self.assertTrue(snapshot.source_url.endswith("notice-1.pdf"))
        self.assertEqual(adapter.fetch_direct_sales_record(FundIdentity("浦银安盛", "014002", "测试"))["direct_sales_limit"], "暂停")

    def test_no_current_notice_is_explicit_no_data(self):
        session = FakeSession(posts={PuyanAxaAdapter.notice_api_url: FakeResponse(payload={"success": True, "result": {"records": []}})})
        snapshot = PuyanAxaAdapter(session=session, clock=fixed_clock).fetch_direct_limit(
            FundIdentity("浦银安盛", "014002", "测试")
        )
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")

    def test_auth_boundary_only_covers_trade_entry(self):
        boundary = PuyanAxaAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public_with_trade_auth_boundary")
        self.assertTrue(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)
        self.assertFalse(boundary.requires_bank_card)


if __name__ == "__main__":
    unittest.main()
