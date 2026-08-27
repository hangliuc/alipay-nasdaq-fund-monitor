from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.jianxin import JianxinAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class JianxinAdapterTests(unittest.TestCase):
    def test_catalogue_parser_flattens_categories_and_deduplicates_codes(self):
        payload = {
            "errcode": 0,
            "data": [
                {"name": "海外基金", "id": 16, "list": [
                    {"fundCode": "012752", "fundName": "建信纳斯达克100指数（QDII）C", "fundTypeCode": "4", "riskStr": "中高风险"},
                ]},
                {"name": "热销基金", "id": -1, "list": [
                    {"fundCode": "012752", "fundName": "建信纳斯达克100指数（QDII）C", "fundTypeCode": "4"},
                    {"fundCode": "bad", "fundName": "坏数据"},
                ]},
            ],
        }
        funds = JianxinAdapter.parse_catalogue_payload(payload, JianxinAdapter.catalogue_api_url)
        self.assertEqual([(item.code, item.name) for item in funds], [("012752", "建信纳斯达克100指数（QDII）C")])
        self.assertEqual(funds[0].fund_type, "海外基金")
        self.assertEqual(funds[0].share_class, "C")

    def test_product_parser_preserves_standard_and_raw_fields(self):
        payload = {"errcode": 0, "data": {"detail": {"fund": {
            "fundName": "建信纳斯达克100指数型证券投资基金（QDII）",
            "fundShortName": "建信纳斯达克100指数（QDII）C人民币",
            "fundCode": "012752", "fundType": "海外基金", "riskLevel": "中高风险",
            "fundTypeCode": "4", "issueDateStr": "2021-09-22", "managerName": "建信基金管理有限责任公司",
        }, "profit": {"dataSource": "银河证券", "lastYear": "15.98", "date": "2026-08-21"},
        "netValue": "3.2872", "totalNetValue": "3.2872", "netValueDateStr1": "2026-08-24"},
        "fareStructure": [{"name": "申购费率", "list": [{"nrate": 0.0}]}],
        "fundManager": [{"name": "李博涵"}], "category": [{"id": 876, "name": "临时公告"}]}}
        product = JianxinAdapter.parse_product_payload(payload, "012752", JianxinAdapter.product_api_url, fixed_clock().isoformat())
        self.assertEqual(product.name, "建信纳斯达克100指数（QDII）C人民币")
        self.assertEqual(product.full_name, "建信纳斯达克100指数型证券投资基金（QDII）")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.fields["基金经理"], "李博涵")
        self.assertEqual(product.fields["profit_lastYear"], "15.98")
        self.assertIn("fareStructure", product.fields)

    def test_direct_channel_exception_has_priority_over_general_table(self):
        text = """下属分级基金的交易代码 539001 012752 下属分级基金的限制申购金额（单位：人民币元） 10元 10元
        其他需要提示的事项：针对在建信基金直销渠道投资建信纳斯达克100指数型证券投资基金（QDII）人民币份额（基金代码：539001、012752），如投资者单日单个基金账户累计申购金额高于50元，本基金管理人有权拒绝高于50元的部分金额。"""
        parsed = JianxinAdapter.parse_announcement_text(text, "012752", "暂停大额申购公告", "2026年8月19日", "https://example.test/a.doc")
        self.assertEqual(parsed["limit"], "50元")
        self.assertIn("直销渠道", parsed["quota_type"])

    def test_announcement_table_maps_share_code_and_units(self):
        text = """下属分级基金的交易代码 012751 539001 012753 012752 023422
        下属分级基金的限制申购金额 1美元 10元 1美元 10元 10元
        下属分级基金的限制定期定额投资金额 1美元 10元 1美元 10元 10元"""
        parsed = JianxinAdapter.parse_announcement_text(text, "012752", "暂停大额申购公告", "2026-08-19", "https://example.test/a.doc")
        self.assertEqual(parsed["limit"], "10元")

    def test_fetch_product_uses_public_detail_api(self):
        payload = {"errcode": 0, "data": {"detail": {"fund": {"fundShortName": "测试A", "fundCode": "539002"}, "profit": {}, "netValueDateStr1": "2026-08-25"}}}
        session = FakeSession({JianxinAdapter.product_api_url: FakeResponse(payload=payload, url=JianxinAdapter.product_api_url)})
        adapter = JianxinAdapter(session=session, clock=fixed_clock)
        product = adapter.fetch_product(FundIdentity("建信", "539002", "测试A"))
        self.assertEqual(product.name, "测试A")
        self.assertEqual(session.calls[0][1]["params"], {"fundCode": "539002"})

    def test_fetch_direct_limit_returns_no_data_without_historical_fallback(self):
        adapter = JianxinAdapter(session=FakeSession({}), clock=fixed_clock)
        adapter._fetch_notice = lambda fund: None
        snapshot = adapter.fetch_direct_limit(FundIdentity("建信", "999999", "测试"))
        self.assertEqual(snapshot.status, "official_api_no_data")
        self.assertIsNone(snapshot.limit)

    def test_auth_boundary_is_public_but_trade_submission_is_out_of_scope(self):
        boundary = JianxinAdapter().auth_boundary()
        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)


if __name__ == "__main__":
    unittest.main()
