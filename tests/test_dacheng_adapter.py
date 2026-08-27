from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.dacheng import DachengAdapter


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class DachengAdapterTests(unittest.TestCase):
    def test_catalogue_and_product_payload_mapping(self):
        payload = {"results": [{"product_code": "008971", "product_abbr": "大成纳斯达克100ETF联接（QDII）C", "product_type": "QDII"}]}
        funds = DachengAdapter.parse_catalogue_payload(payload, DachengAdapter.api_url)
        self.assertEqual(funds[0].code, "008971")
        self.assertEqual(funds[0].share_class, "C")
        detail = {"results": [{"product_code": "008971", "product_name": "大成纳斯达克100交易型开放式指数证券投资基金联接基金（QDII）C", "product_abbr": "大成纳斯达克100ETF联接（QDII）C", "product_type": "QDII", "p_risk_level_text": "中高风险", "found_date": "20230628", "nav_date": "20260824", "product_status_text": "正常", "unit_nav": "6.1549"}]}
        product = DachengAdapter.parse_product_payload(detail, "008971", DachengAdapter.product_url_template.format(code="008971"), fixed_clock().isoformat())
        self.assertEqual(product.trade_status, "正常")
        self.assertEqual(product.inception_date, "2023-06-28")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.risk_level, "中高风险")

    def test_announcement_list_filters_and_sorts_limit_notices(self):
        payload = {"results": [{"data": [
            {"pub_date": "2026-06-03 09:00:00", "title": "关于调整大额申购（含定期定额申购）的公告", "attachment_url": "/a.pdf"},
            {"pub_date": "2026-07-21 09:00:00", "title": "2026年第2季度报告", "attachment_url": "/q.pdf"},
        ]}]}
        notices = DachengAdapter.parse_announcement_list_payload(payload)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["date"], "2026-06-03")
        self.assertEqual(notices[0]["url"], "https://www.dcfund.com.cn/a.pdf")

    def test_announcement_parser_uses_direct_sales_amount(self):
        text = """限制申购金额（单位：人民币元） 100.00  下属基金份额的交易代码 000834 008971  下属基金份额的限制金额（单位：人民币元） 100.00 100.00  2026年6月4日起，投资人通过本公司直销渠道（包含大成基金 APP、官网、微信公众号和直销柜台等）申购单日单个基金账户累计金额应不超过100元。"""
        parsed = DachengAdapter.parse_announcement_text(text, "008971", "调整大额申购公告", "2026年06月03日", "https://example.test/a.pdf")
        self.assertEqual(parsed["limit"], "100元")
        self.assertEqual(parsed["announcement_date"], "2026-06-03")
        self.assertIn("直销", parsed["quota_remark"])

    def test_pause_and_resume_are_explicit_states(self):
        paused = DachengAdapter.parse_announcement_text("008971 暂停大额申购（含定期定额申购）业务", "008971", "暂停大额申购公告")
        self.assertEqual(paused["limit"], "暂停")
        resumed = DachengAdapter.parse_announcement_text("008971 恢复正常办理大额申购及定投业务", "008971", "恢复公告")
        self.assertEqual(resumed["limit"], "不限")

    def test_direct_limit_uses_current_notice_not_old_value(self):
        adapter = DachengAdapter(clock=fixed_clock)
        adapter.fetch_product = lambda fund: adapter.parse_product_payload({"results": [{"product_status_text": "正常", "product_code": fund.code}]}, fund.code, "https://example.test/product", adapter._observed_at())
        adapter._fetch_notice = lambda code: {"limit": "100元", "status": "ok", "quota_type": "单日累计", "quota_remark": "最新公告", "source_url": "https://example.test/20260603.pdf"}
        snapshot = adapter.fetch_direct_limit(FundIdentity("大成", "008971", "测试"))
        self.assertEqual(snapshot.limit, "100元")
        self.assertEqual(snapshot.status, "ok")

    def test_auth_boundary_is_public(self):
        self.assertEqual(DachengAdapter(clock=fixed_clock).auth_boundary().status, "public")


if __name__ == "__main__":
    unittest.main()
