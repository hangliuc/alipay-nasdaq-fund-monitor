from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.tianhong import TianhongAdapter


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class TianhongAdapterTests(unittest.TestCase):
    def test_catalogue_payload_mapping(self):
        payload = {"retCode": 0, "fundDataInfo": [
            {"fundCode": "016665", "fundName": "天弘全球高端制造混合（QDII）C", "fundType": "QDII"},
            {"fundCode": "016665", "fundName": "天弘全球高端制造混合（QDII）C", "fundType": "QDII"},
        ]}
        funds = TianhongAdapter.parse_catalogue_payload(payload, TianhongAdapter.catalogue_api_url)
        self.assertEqual(len(funds), 1)
        self.assertEqual(funds[0].share_class, "C")

    def test_waf_challenge_is_detected_without_bypass(self):
        content = b'<textarea id="renderData">{"l1":"x"}</textarea><script>acw_sc__v2</script>'
        self.assertTrue(TianhongAdapter.is_waf_challenge(content))
        self.assertFalse(TianhongAdapter.is_waf_challenge(b'{"retCode":0,"fundDataInfo":[]}'))

    def test_announcement_parser_prefers_direct_sales_amount(self):
        text = """公告送出日期：2026年07月24日 天弘全球高端制造混合型证券投资基金 016665 本公司决定调整个人投资者在直销机构单日累计申购单个基金份额的金额不得超过1000元，代销机构仍为100元。"""
        parsed = TianhongAdapter.parse_announcement_text(text, "016665", "调整个人投资者大额申购公告", "2026年07月24日", "https://example.test/a.pdf")
        self.assertEqual(parsed["limit"], "1000元")
        self.assertIn("直销", parsed["quota_remark"])

    def test_pause_notice_is_explicit(self):
        text = "天弘纳斯达克100指数型发起式证券投资基金（QDII）018044 暂停申购及定期定额投资业务。"
        parsed = TianhongAdapter.parse_announcement_text(text, "018044", "暂停申购及定期定额投资业务的公告")
        self.assertEqual(parsed["limit"], "暂停")

    def test_direct_limit_uses_official_cdn_notice(self):
        adapter = TianhongAdapter(clock=fixed_clock, notice_urls={"016665": "https://cdn.example.test/a.pdf"})
        adapter._fetch_notice = lambda code: {"limit": "1000元", "status": "ok", "quota_type": "直销单日累计", "quota_remark": "官方 PDF", "source_url": "https://cdn.example.test/a.pdf"}
        snapshot = adapter.fetch_direct_limit(FundIdentity("天弘", "016665", "测试"))
        self.assertEqual(snapshot.limit, "1000元")
        self.assertEqual(snapshot.source_url, "https://cdn.example.test/a.pdf")

    def test_unknown_code_is_explicit_no_data(self):
        snapshot = TianhongAdapter(clock=fixed_clock, notice_urls={}).fetch_direct_limit(FundIdentity("天弘", "999999", "测试"))
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")

    def test_auth_boundary_records_waf(self):
        boundary = TianhongAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public_with_waf_boundary")
        self.assertIn("WAF", boundary.reason)


if __name__ == "__main__":
    unittest.main()
