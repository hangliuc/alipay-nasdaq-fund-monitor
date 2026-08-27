from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.yinhu import YinhuAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


CATALOGUE = """
<html><body><table>
 <tr><td><a href="/main/fund/funddetail/index.shtml?product_code=016702">银华海外数字经济量化选股混合发起式(QDII)C</a></td><td>QDII</td></tr>
 <tr><td><a href="https://www.yhfund.com.cn/main/fund/funddetail/index.shtml?product_code=016702">银华海外数字经济量化选股混合发起式(QDII)C</a></td><td>QDII</td></tr>
 <tr><td><a href="/main/fund/funddetail/index.shtml?product_code=016701">银华海外数字经济量化选股混合发起式(QDII)A</a></td><td>QDII</td></tr>
</table></body></html>
""".encode("utf-8")

PRODUCT = """
<html><head><title>银华海外数字经济量化选股混合发起式(QDII)C（基金代码：016702）</title></head>
<body><table>
 <tr><th>基金全称</th><td>银华海外数字经济量化选股混合型发起式证券投资基金(QDII)</td><th>基金类型</th><td>QDII</td></tr>
 <tr><th>风险等级</th><td>中高风险</td><th>成立日期</th><td>2023年3月15日</td></tr>
 <tr><th>申购状态</th><td>正常开放</td><th>赎回状态</th><td>正常开放</td></tr>
 </table><div>最新净值 2.0375 净值日期：2026-08-24</div>
 <div>银华网上直销单日累计申购上限：5,000元。起购金额：1元。</div>
</body></html>
""".encode("utf-8")

PRODUCT_NO_LIMIT = """
<html><body><h1>银华测试基金（基金代码：016702）</h1>
<div>申购状态：正常开放；赎回状态：正常开放；起购金额：1元起。</div>
</body></html>
""".encode("utf-8")


class YinhuAdapterTests(unittest.TestCase):
    def test_identity_from_config_uses_official_product_url(self):
        identity = YinhuAdapter.identity_from_config({"code": "016702", "name": "银华海外数字经济(QDII)C"})
        self.assertEqual(identity.manager_id, "银华")
        self.assertEqual(identity.share_class, "C")
        self.assertIn("product_code=016702", identity.source_url)

    def test_catalogue_parser_deduplicates_official_links(self):
        funds = YinhuAdapter.parse_catalogue_html(CATALOGUE, YinhuAdapter.catalogue_url)
        self.assertEqual([fund.code for fund in funds], ["016701", "016702"])
        self.assertEqual(funds[1].share_class, "C")
        self.assertEqual(funds[1].source_type, YinhuAdapter.source_type_catalogue)

    def test_product_parser_preserves_fields_limit_and_status(self):
        product = YinhuAdapter.parse_product_html(
            PRODUCT, "016702", YinhuAdapter.product_url_template.format(code="016702"), fixed_clock().isoformat()
        )
        self.assertEqual(product.full_name, "银华海外数字经济量化选股混合型发起式证券投资基金(QDII)")
        self.assertEqual(product.inception_date, "2023-03-15")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.trade_status, "申购开放；赎回开放")
        self.assertEqual(product.fields["直销/申购限额"], "5000元")

    def test_notice_parser_requires_target_code_and_direct_wording(self):
        parsed = YinhuAdapter.parse_announcement_text(
            "公告基本信息 基金代码 016702。本公司网上直销渠道单日累计申购上限为5,000元。",
            "016702", "调整直销机构大额申购公告", "2026年7月17日", "https://www.yhfund.com.cn/upload/notice.pdf",
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["limit"], "5000元")
        self.assertEqual(parsed["announcement_date"], "2026-07-17")
        self.assertIsNone(YinhuAdapter.parse_announcement_text("基金代码 016701，直销上限5万元", "016702"))

    def test_fetch_no_limit_does_not_guess_from_minimum_purchase(self):
        url = YinhuAdapter.product_url_template.format(code="016702")
        adapter = YinhuAdapter(
            session=FakeSession({url: FakeResponse(PRODUCT_NO_LIMIT, url=url)}), clock=fixed_clock
        )
        fund = FundIdentity("银华", "016702", "银华海外数字经济(QDII)C")
        snapshot = adapter.fetch_direct_limit(fund)
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")
        self.assertIsNone(adapter.fetch_direct_sales_record(fund))

    def test_auth_boundary_is_explicit_for_public_trade_login(self):
        login_url = YinhuAdapter.trade_login_url
        product_url = YinhuAdapter.product_url_template.format(code="016702")
        adapter = YinhuAdapter(
            session=FakeSession({product_url: FakeResponse("需要登录 验证码".encode("utf-8"), status_code=403, url=product_url)}),
            clock=fixed_clock,
        )
        fund = FundIdentity("银华", "016702", "银华海外数字经济(QDII)C")
        snapshot = adapter.fetch_direct_limit(fund)
        self.assertEqual(snapshot.status, "official_interface_requires_auth")
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.source_url, login_url)
        record = adapter.fetch_direct_sales_record(fund)
        self.assertEqual(record["direct_sales_status"], "official_interface_requires_auth")
        boundary = adapter.auth_boundary()
        self.assertTrue(boundary.requires_login)
        self.assertTrue(boundary.requires_captcha)

    def test_trade_status_uses_product_page_without_guessing(self):
        url = YinhuAdapter.product_url_template.format(code="016702")
        adapter = YinhuAdapter(session=FakeSession({url: FakeResponse(PRODUCT, url=url)}), clock=fixed_clock)
        status = adapter.fetch_trade_status(FundIdentity("银华", "016702", "测试"))
        self.assertTrue(status.channels[0].subscription)
        self.assertTrue(status.channels[0].redemption)
        self.assertEqual(status.message, "申购开放；赎回开放")


if __name__ == "__main__":
    unittest.main()
