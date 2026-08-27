import unittest
from datetime import datetime, timezone

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.jiashi import JiashiAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class JiashiAdapterTests(unittest.TestCase):
    def test_catalogue_payload_preserves_current_public_fields(self):
        payload = {
            "error_no": "0",
            "results": [
                {
                    "product_code": "016533",
                    "product_abbr": "嘉实纳斯达克100ETF发起联接（QDII）C人民币",
                    "product_name": "嘉实纳斯达克100交易型开放式指数证券投资基金发起式联接基金（QDII）",
                    "fund_class": "HFM02",
                    "product_id": "1951",
                    "fund_status": "5",
                    "unit_nav": "2.1305",
                    "nav_date": "2026-08-25",
                },
                # Duplicate code should resolve to one current identity.
                {"product_code": "016533", "product_abbr": "重复行"},
                {"product_code": "bad", "product_abbr": "忽略"},
            ],
        }
        funds = JiashiAdapter.parse_catalogue_payload(payload, JiashiAdapter.api_url)
        self.assertEqual(len(funds), 1)
        self.assertEqual(funds[0].code, "016533")
        self.assertEqual(funds[0].share_class, "C")
        self.assertEqual(funds[0].fund_type, "HFM02")
        self.assertEqual(funds[0].source_type, JiashiAdapter.source_type_catalogue)

    def test_catalogue_params_match_official_frontend_business_code(self):
        params = JiashiAdapter.catalogue_params(page=2, page_size=500)
        self.assertEqual(params["funcNo"], "741010")
        self.assertEqual(params["cur_page"], "2")
        self.assertEqual(params["num_per_page"], "500")
        self.assertEqual(params["sessionId"], "2")

    def test_parse_limit_table_extracts_direct_and_expands_code_groups(self):
        html = """
        <table><tr><th>基金代码</th><th>基金名称</th>
        <th>单日单户累计申购（含）</th><th>单日单户累计转入（含）</th>
        <th>单日单户累计定投（含）</th><th>暂停/限制/恢复起始日</th><th>备注</th></tr>
        <tr><td>017730/017731</td><td>嘉实全球产业升级 A/C</td>
        <td>直销：10万 非直销：1000元</td><td>暂未开通</td><td>直销：10万 非直销：1000元</td><td>2025/11/3</td><td>A/C不共用</td></tr>
        <tr><td>016532/016533/021838</td><td>嘉实纳指 C</td>
        <td>暂停申购</td><td>暂未开通</td><td>暂停定投</td><td>2026/2/3</td><td>三类不共用</td></tr>
        <tr><td>000043</td><td>嘉实美国成长</td>
        <td>直销：10万元 非直销：100元</td><td>暂未开通</td><td>直销：10万元 非直销：100元</td><td>2025/11/3</td><td></td></tr>
        </table>
        """
        rows = JiashiAdapter.parse_limit_table(html.encode(), JiashiAdapter.limit_table_url)
        self.assertEqual(rows["017730"]["limit"], "10万元")
        self.assertEqual(rows["017731"]["limit"], "10万元")
        self.assertEqual(rows["016533"]["limit"], "暂停")
        self.assertEqual(rows["000043"]["limit"], "10万元")
        self.assertIn("非直销", rows["000043"]["purchase_raw"])

    def test_direct_cell_keeps_direct_side_only(self):
        self.assertEqual(JiashiAdapter._direct_cell("除代销机构投资者外：800万 代销机构投资者：10万"), "800万")
        self.assertEqual(JiashiAdapter._direct_cell("不限"), "不限")
        self.assertEqual(JiashiAdapter._direct_cell("暂停申购"), "暂停")

    def test_fetch_direct_limit_uses_official_table_and_raw_row(self):
        html = """<table><tr><th>基金代码</th><th>基金名称</th><th>单日单户累计申购（含）</th></tr>
        <tr><td>016533</td><td>嘉实纳斯达克100 C</td><td>暂停申购</td></tr></table>""".encode()
        session = FakeSession({JiashiAdapter.limit_table_url: FakeResponse(content=html, url=JiashiAdapter.limit_table_url)})
        adapter = JiashiAdapter(session=session, clock=fixed_clock)
        snapshot = adapter.fetch_direct_limit(FundIdentity("嘉实", "016533", "嘉实纳斯达克100 C"))
        self.assertEqual(snapshot.limit, "暂停")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.channel, "嘉实官网申购上限表（直销）")
        self.assertEqual(snapshot.raw["code"], "016533")

    def test_product_api_snapshot_normalizes_structured_fields(self):
        payload = {"error_no": "0", "results": [{
            "product_code": "000043", "product_abbr": "嘉实美国成长股票（QDII）人民币",
            "product_name": "嘉实美国成长股票型证券投资基金", "fund_class_text": "股票指数类",
            "risk_level_text": "中高风险", "found_date": "20130614", "newest_asset": "123.45",
            "nav_date": "2026-08-25", "fund_status": "0", "product_id": "410",
        }]}
        product = JiashiAdapter.product_from_api(payload, "000043", JiashiAdapter.product_url_template.format(code="000043"), fixed_clock().isoformat())
        self.assertEqual(product.name, "嘉实美国成长股票（QDII）人民币")
        self.assertEqual(product.full_name, "嘉实美国成长股票型证券投资基金")
        self.assertEqual(product.inception_date, "2013-06-14")
        self.assertEqual(product.net_value_date, "2026-08-25")
        self.assertEqual(product.fields["newest_asset"], "123.45")

    def test_fetch_product_and_trade_status_use_public_741010_741011_741044(self):
        catalogue = {"error_no": "0", "results": [{
            "product_code": "016533", "product_abbr": "嘉实纳指C", "product_id": "1951",
        }]}
        detail = {"error_no": "0", "results": [{
            "product_code": "016533", "product_abbr": "嘉实纳指C", "product_name": "全称",
            "product_id": "1951", "fund_status": "5", "risk_level_text": "中高风险",
            "found_date": "20220916", "nav_date": "2026-08-25",
        }]}
        status = {"error_no": "0", "results": [{"canbuy": "1", "redeemstatus": "1", "fixstatus": "0", "state": "5"}]}
        session = FakeSession({
            JiashiAdapter.api_url: FakeResponse(payload=catalogue),
        })
        # FakeSession returns sequential payloads for this endpoint.
        original = session.responses[JiashiAdapter.api_url]
        sequence = [FakeResponse(payload=catalogue), FakeResponse(payload=detail), FakeResponse(payload=detail), FakeResponse(payload=status)]
        def get(url, **kwargs):
            session.calls.append((url, kwargs))
            if url != JiashiAdapter.api_url:
                raise AssertionError(url)
            return sequence.pop(0)
        session.get = get
        adapter = JiashiAdapter(session=session, clock=fixed_clock)
        fund = FundIdentity("嘉实", "016533", "嘉实纳指C")
        product = adapter.fetch_product(fund)
        self.assertEqual(product.fields["product_id"], "1951")
        trade = adapter.fetch_trade_status(fund)
        self.assertTrue(trade.channels[0].subscription)
        self.assertFalse(trade.channels[0].sip)
        self.assertEqual(len(session.calls), 4)

    def test_auth_boundary_is_public_but_does_not_claim_trade_submission(self):
        boundary = JiashiAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)


if __name__ == "__main__":
    unittest.main()
