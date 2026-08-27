from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.morgan import MorganAdapter


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class MorganAdapterTests(unittest.TestCase):
    def test_catalogue_parser_reads_and_deduplicates_fund_links(self):
        html = """
        <div class="line_top"><a href="/fund/019173/"><div class="item_title">摩根纳斯达克100人民币C</div></a></div>
        <div class="line_btm_item"><a href="/fund/019173/"><strong>摩根纳斯达克100人民币C</strong></a></div>
        <div class="line_btm_item"><a href="/fund/370010B/"><strong>摩根货币B</strong></a></div>
        <a href="/fund/not-a-fund/">错误链接</a>
        """.encode("utf-8")
        funds = MorganAdapter.parse_catalogue_html(html, MorganAdapter.catalogue_url)
        self.assertEqual([fund.code for fund in funds], ["019173", "370010B"])
        self.assertEqual(funds[0].share_class, "C")
        self.assertEqual(funds[1].source_url, "https://www.cifm.com/fund/370010B/")

    def test_product_parser_reads_official_fields_and_nav(self):
        html = """
        <script>var currentFundCode = '019173'; var currentFundName = '摩根纳斯达克100人民币C'; var clrq = "2023-08-25";</script>
        <h3 class="fundName">摩根纳斯达克100人民币C</h3>
        <table class="table_box"><tr><td>法定名称</td><td>摩根纳斯达克100指数型发起式证券投资基金(QDII)</td></tr>
        <tr><td>基金代码</td><td>019173</td></tr><tr><td>成立日期</td><td>2023年08月25日</td></tr></table>
        <table class="table_box"><tr><td>基金简称</td><td>摩根纳斯达克100人民币C</td></tr>
        <tr><td>运作方式</td><td>契约型开放式</td></tr><tr><td>风险等级</td><td>中风险</td></tr></table>
        <div id="goumai"><div class="buy_btn fl">立即购买</div></div>
        """.encode("utf-8")
        nav = {"FundDate": "2026-08-24", "NetValue": "1.2345", "TotalNetValue": "1.5678", "FundType": "指数型", "FundState": "正常开放"}
        product = MorganAdapter.parse_product_html(html, "019173", "https://www.cifm.com/fund/019173/", fixed_clock().isoformat(), nav=nav)
        self.assertEqual(product.name, "摩根纳斯达克100人民币C")
        self.assertEqual(product.full_name, "摩根纳斯达克100指数型发起式证券投资基金(QDII)")
        self.assertEqual(product.inception_date, "2023-08-25")
        self.assertEqual(product.net_value_date, "2026-08-24")
        self.assertEqual(product.fields["最新净值"], "1.2345")
        self.assertEqual(product.fields["购买按钮状态"], "open")

    def test_nav_xml_parser_selects_latest_matching_row(self):
        xml = b"""
        <Root>
          <Fund FundCode="019173" FundDate="2026-08-22" NetValue="1.2" />
          <Fund FundCode="019173" FundDate="2026-08-24" NetValue="1.3" />
          <Fund FundCode="019172" FundDate="2026-08-25" NetValue="9.9" />
        </Root>
        """
        row = MorganAdapter.parse_nav_xml(xml, "019173")
        self.assertEqual(row["FundDate"], "2026-08-24")
        self.assertEqual(row["NetValue"], "1.3")

    def test_search_result_parser_reads_title_date_and_url(self):
        html = """
        <div class="result">
          <div><a href="/fund/019172/announce/202607/a.pdf"><p class="title">1. 摩根纳斯达克100调整直销渠道大额申购公告</p></a><p class="category">类别: 基金公告：2026-07-09</p></div>
          <div><a href="/fund/019172/announce/202608/a.pdf"><p class="title">2. 经理变更公告</p></a><p class="category">类别: 基金公告：2026-08-22</p></div>
        </div>
        """.encode("utf-8")
        rows = MorganAdapter.parse_search_results_html(html, MorganAdapter.search_url)
        self.assertEqual(rows[0]["title"], "摩根纳斯达克100调整直销渠道大额申购公告")
        self.assertEqual(rows[0]["date"], "2026-07-09")
        self.assertTrue(rows[0]["url"].endswith("/a.pdf"))

    def test_direct_notice_parser_maps_amount_by_share_code(self):
        text = """
        摩根纳斯达克100调整直销渠道大额申购、定期定额投资及转换转入业务限制金额的公告
        下属分级基金的交易代码 019172 019173 019174 019175
        该分级基金是否暂停大额申购、大额转换转入、定期定额投资 是 是 是 是
        下属分级基金的限制申购金额（单位：人民币元） 300.00 300.00 30.00 30.00
        """
        parsed = MorganAdapter.parse_announcement_text(text, "019173", "直销渠道公告", "2026-07-09", "https://www.cifm.com/a.pdf", "摩根纳斯达克100人民币C")
        self.assertEqual(parsed["limit"], "300元")
        self.assertEqual(parsed["status"], "ok")
        self.assertIn("直销渠道", parsed["quota_type"])

        usd = MorganAdapter.parse_announcement_text(text, "019174", "直销渠道公告", fund_name="摩根纳斯达克100美元A")
        self.assertEqual(usd["limit"], "30美元")

    def test_direct_limit_prefers_direct_notice_and_returns_record(self):
        fund = FundIdentity("摩根", "019173", "摩根纳斯达克100人民币C")
        adapter = MorganAdapter(clock=fixed_clock)
        adapter._search_notices = lambda _fund: [
            {"title": "通用调整大额申购公告", "date": "2026-07-24", "url": "https://www.cifm.com/general.pdf"},
            {"title": "调整直销渠道大额申购公告", "date": "2026-07-09", "url": "https://www.cifm.com/direct.pdf"},
        ]
        adapter._fetch_document = lambda url: ("交易代码 019172 019173 019174 019175 下属分级基金的限制申购金额 300 300 30 30", url)
        snapshot = adapter.fetch_direct_limit(fund)
        self.assertEqual(snapshot.limit, "300元")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.source_url, "https://www.cifm.com/direct.pdf")
        record = adapter.fetch_direct_sales_record(fund)
        self.assertEqual(record["direct_sales_limit"], "300元")

    def test_direct_notice_uses_latest_notice_within_direct_channel(self):
        fund = FundIdentity("摩根", "019173", "摩根纳斯达克100人民币C")
        adapter = MorganAdapter(clock=fixed_clock)
        adapter._search_notices = lambda _fund: [
            {"title": "调整直销渠道大额申购公告", "date": "2026-07-09", "url": "https://www.cifm.com/old.pdf"},
            {"title": "调整直销渠道大额申购公告", "date": "2026-07-10", "url": "https://www.cifm.com/new.pdf"},
        ]
        adapter._fetch_document = lambda url: (
            "交易代码 019172 019173 019174 019175 限制申购金额 300 300 30 30",
            url,
        )
        snapshot = adapter.fetch_direct_limit(fund)
        self.assertEqual(snapshot.source_url, "https://www.cifm.com/new.pdf")

    def test_pause_status_is_mapped_per_share_code(self):
        text = (
            "下属分级基金的交易代码 019172 019173 "
            "该分级基金是否暂停大额申购 否 是"
        )
        self.assertIsNone(MorganAdapter.parse_announcement_text(text, "019172"))
        parsed = MorganAdapter.parse_announcement_text(text, "019173")
        self.assertEqual(parsed["limit"], "暂停")
        self.assertEqual(parsed["status"], "paused")

    def test_html_notice_fetches_official_pdf_attachment(self):
        adapter = MorganAdapter()
        html_url = "https://www.cifm.com/notice.html"
        pdf_url = "https://www.cifm.com/notice.pdf"

        class Response:
            def __init__(self, content, url):
                self.content = content
                self.url = url
                self.status_code = 200

            def raise_for_status(self):
                return None

        class Session:
            def __init__(self):
                self.urls = []

            def get(self, url, **kwargs):
                self.urls.append(url)
                return Response(b"html" if url == html_url else b"pdf", url)

        session = Session()
        adapter.session = session
        with patch.object(
            MorganAdapter,
            "_notice_text",
            side_effect=[("HTML title", pdf_url), ("PDF body", pdf_url)],
        ):
            text, actual_url = adapter._fetch_document(html_url)
        self.assertEqual(text, "PDF body")
        self.assertEqual(actual_url, pdf_url)
        self.assertEqual(session.urls, [html_url, pdf_url])

    def test_auth_boundary_is_explicit(self):
        boundary = MorganAdapter().auth_boundary()
        self.assertEqual(boundary.status, "public_with_trade_auth_boundary")
        self.assertTrue(boundary.requires_login)
        self.assertTrue(boundary.requires_device_signature)


if __name__ == "__main__":
    unittest.main()
