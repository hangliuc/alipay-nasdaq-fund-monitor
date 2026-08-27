from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.changcheng import ChangchengAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


CATALOGUE = """
<html><body><table class="fund_tb"><tbody>
<tr><td jjlx="10"><a href="/main/jjcp/cache/018036.shtml"><p class="name">长城全球新能源车股票发起式（QDII）C</p><p class="desc">018036</p></a></td><td><p>R4<br>中高风险</p></td><td><p>曲少杰</p></td><td><p>2.5603</p><p class="desc">20260824</p></td><td>正常</td></tr>
<tr><td jjlx="2"><a href="/main/jjcp/cache/009002.shtml"><p class="name">长城泰利债券C</p><p class="desc">009002</p></a></td><td><p>R2<br>中低风险</p></td><td><p>测试</p></td><td><p>1.1000</p><p class="desc">20260824</p></td><td>暂停申购</td></tr>
</tbody></table></body></html>
""".encode("utf-8")

PRODUCT = """
<html><body>
<div class="fund_title"><p class="fund_name">长城全球新能源车股票发起式（QDII）C</p><p class="fund_code">018036</p></div>
<ul class="tag_list"><li>QDII</li><li>R4中高风险</li></ul>
<div class="fund_data"><p>2026-08-24净值(元)</p><p class="val"><span>2.5603</span></p></div>
<table class="abstract_tb"><tr><th>基金全称</th><td>长城全球新能源汽车股票型发起式证券投资基金（QDII-LOF）C</td><th>基金简称</th><td>长城全球新能源车股票发起式（QDII）C</td></tr>
<tr><th>基金代码</th><td>018036</td><th>成立日期</th><td>2023-04-21</td></tr>
<tr><th>基金经理</th><td>曲少杰</td><th>基金类型</th><td>QDII</td></tr></table>
</body></html>
""".encode("utf-8")


class ChangchengAdapterTests(unittest.TestCase):
    def test_catalogue_html_maps_all_public_fields(self):
        funds = ChangchengAdapter.parse_catalogue_html(CATALOGUE, ChangchengAdapter.catalogue_url)
        self.assertEqual([f.code for f in funds], ["009002", "018036"])
        self.assertEqual(funds[1].share_class, "C")
        self.assertEqual(funds[1].fund_type, "10")
        self.assertEqual(funds[1].source_type, ChangchengAdapter.source_type_catalogue)

    def test_product_html_maps_structured_fields_and_nav(self):
        adapter = ChangchengAdapter(clock=fixed_clock)
        product = adapter.parse_product_html(PRODUCT, "018036", ChangchengAdapter.product_url_template.format(code="018036"), adapter._observed_at())
        self.assertEqual(product.name, "长城全球新能源车股票发起式（QDII）C")
        self.assertEqual(product.full_name, "长城全球新能源汽车股票型发起式证券投资基金（QDII-LOF）C")
        self.assertEqual(product.fund_type, "QDII")
        self.assertEqual(product.inception_date, "2023-04-21")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.fields["最新净值"], "2.5603")

    def test_discover_uses_official_catalogue_page(self):
        session = FakeSession({ChangchengAdapter.catalogue_url: FakeResponse(CATALOGUE, url=ChangchengAdapter.catalogue_url)})
        adapter = ChangchengAdapter(session=session, clock=fixed_clock)
        funds = adapter.discover_funds()
        self.assertEqual(len(funds), 2)
        self.assertIn("trade_status", adapter._catalogue_rows["009002"])
        self.assertEqual(session.calls[0][0], ChangchengAdapter.catalogue_url)

    def test_notice_html_and_payload_filter_non_limit_notices(self):
        html = '''<ul class="notice_list"><li><a href="/a.pdf"><p class="title">关于调整大额申购、定投业务限额的公告</p><p class="date">2026-07-24</p></a></li><li><a href="/b.pdf"><p class="title">季度报告</p><p class="date">2026-07-20</p></a></li></ul>'''.encode("utf-8")
        notices = ChangchengAdapter.parse_notice_list_html(html, ChangchengAdapter.home_url)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["date"], "2026-07-24")
        payload = {"data": {"list": [{"TITLE": "恢复大额申购及定投", "PUBDATE": "2026年07月25日", "URL": "/c.pdf"}]}}
        self.assertEqual(ChangchengAdapter.parse_notice_list_payload(payload)[0]["url"], "https://www.ccfund.com.cn/c.pdf")

    def test_announcement_parser_prefers_direct_sales_amount(self):
        text = "2026年7月24日起，通过本公司直销渠道申购单日单个基金账户累计金额应不超过500元。下属基金份额的交易代码 501226 018036"
        parsed = ChangchengAdapter.parse_announcement_text(text, "018036", "调整大额申购、定投业务限额公告", "2026年07月24日", "https://www.ccfund.com.cn/a.pdf")
        self.assertEqual(parsed["limit"], "500元")
        self.assertEqual(parsed["announcement_date"], "2026-07-24")
        self.assertIn("直销", parsed["quota_remark"])

    def test_announcement_parser_explicit_pause_and_resume(self):
        self.assertEqual(ChangchengAdapter.parse_announcement_text("暂停大额申购、定投业务", "018036", "暂停大额申购公告")["limit"], "暂停")
        self.assertEqual(ChangchengAdapter.parse_announcement_text("恢复大额申购及定投业务", "018036", "恢复大额申购公告")["limit"], "不限")

    def test_direct_limit_no_data_does_not_reuse_old_value(self):
        product_url = ChangchengAdapter.product_url_template.format(code="018036")
        session = FakeSession({product_url: FakeResponse(PRODUCT, url=product_url), ChangchengAdapter.notice_api_url: FakeResponse(b"", url=ChangchengAdapter.notice_api_url, payload={"data": {"list": []}})})
        snapshot = ChangchengAdapter(session=session, clock=fixed_clock).fetch_direct_limit(FundIdentity("长城", "018036", "测试"))
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")

    def test_auth_boundary_is_public_with_frontend_boundary(self):
        boundary = ChangchengAdapter(clock=fixed_clock).auth_boundary()
        self.assertEqual(boundary.status, "public_with_frontend_request_boundary")
        self.assertFalse(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)


if __name__ == "__main__":
    unittest.main()
