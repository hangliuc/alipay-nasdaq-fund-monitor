from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.guangfa import GuangfaAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class GuangfaAdapterTests(unittest.TestCase):
    def test_catalogue_parser_reads_ssr_rows_and_type_code(self):
        html = """
        <table><thead><tr><th>基金代码</th><th>基金简称</th><th>风险等级</th></tr></thead>
        <tbody id="all-funds">
          <tr id="fund-006479" class="fund-typeid-9" data-fundcode="006479">
            <td><span class="js-select-fund" data-fundcode="006479" data-fundname="广发纳指100ETF联接（QDII）人民币C"></span></td>
            <td>006479</td><td><a href="?fundcode=006479">广发纳指100ETF联接（QDII）人民币C</a></td><td>中风险</td>
          </tr>
          <tr data-fundcode="bad"><td>bad</td></tr>
        </tbody></table>
        """.encode()
        funds = GuangfaAdapter.parse_catalogue_html(html, GuangfaAdapter.catalogue_url)
        self.assertEqual([x.code for x in funds], ["006479"])
        self.assertEqual(funds[0].share_class, "C")
        self.assertEqual(funds[0].fund_type, "官网类型码:9")

    def test_product_parser_reads_table_and_buttons(self):
        html = """
        <script>var fundCode = '021277', fundName = '广发全球精选股票（QDII）人民币C', fundType = '海外型基金', fCrateDate = "2024-04-18";</script>
        <table class="table04"><tr><td class="td-tit">基金全称 :</td><td>广发全球精选股票型证券投资基金</td><td class="td-tit">基金代码 :</td><td>021277</td></tr>
        <tr><td class="td-tit">基金简称 :</td><td>广发全球精选股票（QDII）人民币C</td><td class="td-tit">风险等级 :</td><td>中风险</td></tr></table>
        <a class="btn buy" href="https://trade.gffunds.com.cn/fund/all-fund/buy?fundCode=021277" style="display: block">购买</a>
        <a class="btn buy" href="https://trade.gffunds.com.cn/fund/all-fund/regular-buy?fundCode=021277" style="display: none">定投</a>
        """.encode()
        product = GuangfaAdapter.parse_product_html(html, "021277", "https://example.test/p", fixed_clock().isoformat())
        self.assertEqual(product.name, "广发全球精选股票（QDII）人民币C")
        self.assertEqual(product.full_name, "广发全球精选股票型证券投资基金")
        self.assertEqual(product.inception_date, "2024-04-18")
        self.assertEqual(product.fields["购买按钮状态"], "open")
        self.assertEqual(product.fields["定投按钮状态"], "closed")

    def test_json_service_enriches_current_nav_status_and_fields(self):
        base = GuangfaAdapter.parse_product_html(b"<table class='table04'></table>", "006479", "https://example.test/p", fixed_clock().isoformat())
        payload = {"data": [{"FUNDCODE": "006479", "FUNDNAME": "测试C", "FUNDFULLNAME": "测试全称", "CATEGORYNAME": "海外型", "FUNDLEVELSHOW": "中风险", "CREATEDATE": "20181025", "NAVUNIT": "7.9007", "NAVDATE": "20260824", "FUNDSTATUS": "正常开放", "WEBISOPEN": "Y", "WEBFIXOPEN": "N"}]}
        product = GuangfaAdapter.parse_fund_info_payload(payload, "006479", base)
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.fields["购买按钮状态"], "open")
        self.assertEqual(product.fields["定投按钮状态"], "closed")
        self.assertEqual(product.fields["API_FUNDSTATUS"], "正常开放")

    def test_direct_limit_uses_personal_realtime_api(self):
        session = FakeSession({
            GuangfaAdapter.person_limit_url: FakeResponse(url=GuangfaAdapter.person_limit_url, payload={"FUNDCODE": "021277", "MAX_ALLOT_BALA": "2,000", "MIN_ALLOT_BALA": "1"}),
            GuangfaAdapter.org_limit_url: FakeResponse(url=GuangfaAdapter.org_limit_url, payload={"FUNDCODE": "021277", "MAX_ALLOT_BALA": "100"}),
        })
        adapter = GuangfaAdapter(session=session, clock=fixed_clock)
        snapshot = adapter.fetch_direct_limit(FundIdentity("广发", "021277", "测试"))
        self.assertEqual(snapshot.limit, "2000元")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.channel, "广发基金官网个人限额 API")
        self.assertEqual(len(session.calls), 2)

    def test_limit_parser_does_not_treat_empty_max_as_unlimited(self):
        self.assertIsNone(GuangfaAdapter.parse_limit_payload({"MAX_ALLOT_BALA": None, "MIN_ALLOT_BALA": "1"}))
        self.assertEqual(GuangfaAdapter.parse_limit_payload({"MAX_ALLOT_BALA": "1万元"}), "1万元")

    def test_announcement_parser_reads_amount_and_pause(self):
        parsed = GuangfaAdapter.parse_announcement_text("基金代码 006479 021277 业务限额为 5 2,000 元", "021277", "调整大额申购", "2026-08-10", "https://example.test/a.pdf")
        self.assertEqual(parsed["limit"], "2000元")
        paused = GuangfaAdapter.parse_announcement_text("", "021277", "关于暂停大额申购的公告", "2026-08-10", "https://example.test/b.pdf")
        self.assertEqual(paused["limit"], "暂停")

    def test_no_api_limit_falls_back_to_notice_and_auth_boundary_is_public(self):
        session = FakeSession({
            GuangfaAdapter.person_limit_url: FakeResponse(url=GuangfaAdapter.person_limit_url, payload={"MAX_ALLOT_BALA": None}),
            GuangfaAdapter.org_limit_url: FakeResponse(url=GuangfaAdapter.org_limit_url, payload={"MAX_ALLOT_BALA": None}),
        })
        adapter = GuangfaAdapter(session=session, clock=fixed_clock)
        adapter._fetch_notice = lambda fund: {"limit": "暂停", "status": "ok", "quota_type": "公告", "quota_remark": "暂停", "source_url": "https://example.test/a.pdf"}
        snapshot = adapter.fetch_direct_limit(FundIdentity("广发", "021277", "测试"))
        self.assertEqual(snapshot.limit, "暂停")
        self.assertEqual(adapter.auth_boundary().status, "public")


if __name__ == "__main__":
    unittest.main()
