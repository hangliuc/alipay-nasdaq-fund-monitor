import unittest
from datetime import datetime, timezone

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.huabao import HuabaoAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class HuabaoAdapterTests(unittest.TestCase):
    def test_catalogue_html_parses_embedded_current_fund_market_list(self):
        html = '''<input id="fundMarketList" value="[{NAV=1.2, SHORTNAME=纳斯达克精选C, FUNDSTATUS=203001, FUNDCODE=017437, FUNDSTYLE=201008}, {SHORTNAME=华宝致远混合C, FUNDCODE=008254, FUNDSTYLE=201003}]">'''.encode()
        funds = HuabaoAdapter.parse_catalogue_html(html, HuabaoAdapter.catalogue_url)
        self.assertEqual([fund.code for fund in funds], ["008254", "017437"])
        self.assertEqual(funds[1].name, "纳斯达克精选C")
        self.assertEqual(funds[1].share_class, "C")
        self.assertEqual(funds[1].fund_type, "201008")
        self.assertEqual(funds[1].source_type, HuabaoAdapter.source_type_catalogue)

    def test_product_html_extracts_structured_fields_and_public_buy_button(self):
        html = '''
        <input id="shortName" value="纳斯达克精选C">
        <table><tr><th>基金全称</th><td>华宝纳斯达克精选股票型发起式证券投资基金（QDII）</td>
        <th>基金代码</th><td>017437</td></tr>
        <tr><th>基金类型</th><td>海外基金</td><th>基金合同生效日</th><td>2023-03-02</td></tr>
        <tr><th>最新规模</th><td>56.2亿</td><th>风险等级</th><td>R4</td></tr></table>
        <a class="btn2" href="https://e.fsfund.com/etrading/trade/buyFund/017437/0;">立即购买</a>
        '''.encode()
        product = HuabaoAdapter.parse_product_html(
            html, "017437", HuabaoAdapter.product_url_template.format(code="017437"), fixed_clock().isoformat()
        )
        self.assertEqual(product.name, "纳斯达克精选C")
        self.assertEqual(product.full_name, "华宝纳斯达克精选股票型发起式证券投资基金（QDII）")
        self.assertEqual(product.inception_date, "2023-03-02")
        self.assertEqual(product.asset_scale, "56.2亿")
        self.assertEqual(product.fields["购买按钮状态"], "open")
        self.assertEqual(product.fields["定投按钮状态"], "unknown")

    def test_parse_announcement_text_selects_direct_limit_not_distribution_limit(self):
        text = """限制申购金额（单位：人民币元） 3000.00。下属基金的交易代码 017436 017437。
        本基金在各代销机构的单日单个基金账户累计申购金额上限调整为 3000 元，
        在直销柜台及网上直销平台的单日单个基金账户累计申购（含定投）金额上限仍为 10 万元（含）。"""
        parsed = HuabaoAdapter.parse_announcement_text(
            text, "017437", "调整大额申购（含定投）金额上限的公告", "2026年2月9日", "https://www.fsfund.com/a.pdf"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["limit"], "10万元")
        self.assertEqual(parsed["announcement_date"], "2026-02-09")
        self.assertIn("直销", parsed["quota_remark"])

    def test_parse_announcement_text_supports_direct_limit_with_wan_units(self):
        text = "公告基本信息 基金代码 008253 008254。直销柜台及网上直销平台的单日单个基金账户累计申购（含定投）金额上限由 8 万元调整为 50 万元。"
        parsed = HuabaoAdapter.parse_announcement_text(text, "008254")
        self.assertEqual(parsed["limit"], "50万元")

    def test_fetch_direct_limit_downloads_official_pdf_and_preserves_raw_text(self):
        # No PDF dependency in the test: inject the parser result through a subclass.
        class TestAdapter(HuabaoAdapter):
            def _fetch_notice(self, fund):
                return {
                    "limit": "10万元", "status": "ok", "quota_type": "直销上限",
                    "quota_remark": "官网公告正文明确直销。", "source_url": "https://www.fsfund.com/x.pdf",
                    "announcement_title": "调整大额申购", "announcement_date": "2026-02-09",
                    "announcement_text": "直销柜台及网上直销平台上限 10 万元",
                }
        snapshot = TestAdapter(clock=fixed_clock).fetch_direct_limit(FundIdentity("华宝", "017437", "纳斯达克精选C"))
        self.assertEqual(snapshot.limit, "10万元")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.channel, "华宝基金官网直销限额公告 PDF")
        self.assertIn("announcement_text", snapshot.raw)

    def test_missing_notice_is_explicit_auth_boundary(self):
        session = FakeSession({})
        snapshot = HuabaoAdapter(session=session, clock=fixed_clock).fetch_direct_limit(
            FundIdentity("华宝", "000001", "测试基金")
        )
        self.assertEqual(snapshot.status, "official_interface_requires_auth")
        self.assertIn("网点代码", snapshot.quota_remark)

    def test_discover_funds_uses_official_catalogue_page(self):
        html = '<input id="fundMarketList" value="[{SHORTNAME=测试基金C, FUNDCODE=000001, FUNDSTYLE=201003}]">'.encode()
        session = FakeSession({HuabaoAdapter.catalogue_url: FakeResponse(content=html, url=HuabaoAdapter.catalogue_url)})
        funds = HuabaoAdapter(session=session, clock=fixed_clock).discover_funds()
        self.assertEqual(funds[0].code, "000001")
        self.assertEqual(session.calls[0][0], HuabaoAdapter.catalogue_url)

    def test_auth_boundary_records_announcement_and_trade_authentication(self):
        boundary = HuabaoAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public_with_auth_announcement_boundary")
        self.assertTrue(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)


if __name__ == "__main__":
    unittest.main()
