from datetime import datetime, timezone
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity
from fund_monitor.fetch.direct_sales.adapters.cmfchina import CmfchinaAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class CmfchinaAdapterTests(unittest.TestCase):
    def test_catalogue_payload_reads_all_current_shares(self):
        payload = {
            "resultCode": "0000",
            "data": {
                "fundInfoList": [
                    {"fundId": "019548", "fundName": "招商纳斯达克100ETF联接C", "fundType": "5", "fundSt": "0"},
                    {"fundId": "012643", "fundName": "招商中证红利ETF联接A", "fundType": "0", "fundSt": "0"},
                    {"fundId": "bad", "fundName": "忽略", "fundType": "0"},
                ]
            },
        }
        funds = CmfchinaAdapter.parse_catalogue_payload(payload, CmfchinaAdapter.catalogue_api_url)
        self.assertEqual([fund.code for fund in funds], ["012643", "019548"])
        self.assertEqual(funds[1].fund_type, "QDII")
        self.assertEqual(funds[1].share_class, "C")

    def test_product_parser_reads_fields_and_public_trade_buttons(self):
        html = """
        <div class="pro_name"><h5>招商测试基金A</h5><div class="info"><span class="fund_code">000001</span></div></div>
        <div class="btn_wrapper"><a class="btn_buy" href="https://direct.cmfchina.com/ecwebh5/trade/fundBuyIndex?fundId=000001">购买</a>
        <a class="btn_invest disabled" href="https://direct.cmfchina.com/ecwebh5/trade/fundMipIndex?fundId=000001">定投</a></div>
        <div class="pro_intro_data_wrap"><div class="item"><strong>1.2345</strong><p>单位净值(2026-08-25)</p></div></div>
        <div class="pro_intro_table"><table><tr><th>基金全称</th><td>招商测试证券投资基金</td><th>基金代码</th><td>000001</td></tr>
        <tr><th>基金类型</th><td>股票型</td><th>成立日期</th><td>2020-01-02</td></tr></table></div>
        <ul><li><a href="/web/noticedetails/123/index.html"><p>普通公告</p><span class="date">2026-08-01</span></a></li></ul>
        """.encode()
        product = CmfchinaAdapter.parse_product_html(html, "000001", "https://www.cmfchina.com/web/fundDetail/000001/index.html", fixed_clock().isoformat())
        self.assertEqual(product.full_name, "招商测试证券投资基金")
        self.assertEqual(product.inception_date, "2020-01-02")
        self.assertEqual(product.net_value_date, "2026-08-25")
        self.assertEqual(product.fields["购买按钮状态"], "open")
        self.assertEqual(product.fields["定投按钮状态"], "closed")

    def test_detail_payload_enriches_live_trade_status_and_nav(self):
        base = CmfchinaAdapter.parse_product_html("<div class='pro_name'><h5>旧名称</h5></div>".encode(), "012643", "https://example.test/p", fixed_clock().isoformat())
        payload = {
            "resultCode": "0000",
            "data": {
                "fundId": "012643", "fundName": "招商中证红利ETF联接A", "fundTypeDesc": "股票型",
                "fundRiskLevelDesc": "中高风险", "establishDate": "2022-02-23成立", "fundSum": "5.93亿元",
                "fundNavInfoVoList": [{"nav": 1.1607, "navDisp": "1.1607", "navDate": "2026-08-25"}],
                "tradeStatusVo": {"canBuyFlag": "Y", "canRedeemFlag": "Y", "canMipFlag": "N"},
                "nested": {"flag": True},
            },
        }
        product = CmfchinaAdapter.parse_detail_payload(payload, FundIdentity("招商", "012643", "招商中证红利ETF联接A"), base, fixed_clock().isoformat())
        self.assertEqual(product.name, "招商中证红利ETF联接A")
        self.assertEqual(product.net_value_date, "2026-08-25")
        self.assertEqual(product.fields["购买按钮状态"], "open")
        self.assertEqual(product.fields["定投按钮状态"], "closed")
        self.assertIn('"flag":true', product.fields["API_nested"])

    def test_notice_parser_maps_subclass_code_to_amount(self):
        html = """
        <h2>关于调整招商基金在直销机构大额申购（含定期定额投资）业务的公告</h2>
        <table>
          <tr><td>基金主代码</td><td>019547</td></tr>
          <tr><td>暂停大额申购起始日</td><td>2026 年 7 月 27 日</td></tr>
          <tr><td>限制申购金额（单位：人民币元）</td><td>100.00</td></tr>
          <tr><td>下属分级基金的交易代码</td><td>019547</td><td>019548</td></tr>
          <tr><td>该分级基金是否暂停 / 恢复（大额）申购</td><td>是</td><td>是</td></tr>
          <tr><td>下属分级基金的限制申购金额（单位：人民币元）</td><td>100.00</td><td>100.00</td></tr>
        </table>
        <p>自2026年7月27日起调整在本公司直销机构大额申购业务。</p>
        """.encode()
        parsed = CmfchinaAdapter.parse_notice_html(html, "019548", "https://www.cmfchina.com/web/noticedetails/1/index.html", fixed_clock().isoformat(), "2026-07-24")
        self.assertEqual(parsed["limit"], "100元")
        self.assertEqual(parsed["status"], "ok")
        self.assertIn("直销机构", parsed["quota_remark"])

    def test_notice_parser_turns_effective_restore_into_unlimited(self):
        html = """
        <h2>关于暂停招商基金大额申购和定投业务的公告</h2>
        <table><tr><td>基金主代码</td><td>012643</td></tr>
        <tr><td>限制申购金额（单位：人民币元）</td><td>100,000.00</td></tr></table>
        <p>自2026年8月4日起暂停大额申购，2026年8月10日起恢复本基金的大额申购（含定期定额投资）业务。</p>
        """.encode()
        parsed = CmfchinaAdapter.parse_notice_html(html, "012643", "https://www.cmfchina.com/web/noticedetails/2/index.html", fixed_clock().isoformat(), "2026-08-04")
        self.assertEqual(parsed["limit"], "不限")
        self.assertEqual(parsed["quota_type"], "官网公告恢复状态")

    def test_fetch_direct_limit_uses_official_product_notice(self):
        product_url = CmfchinaAdapter.product_url_template.format(code="019548")
        detail_url = "https://www.cmfchina.com/web/noticedetails/225156/index.html"
        product_html = '<a href="/web/noticedetails/225156/index.html"><p>关于调整招商基金在直销机构大额申购业务的公告</p><span class="date">2026-07-24</span></a>'.encode()
        notice_html = '<h2>关于调整招商基金在直销机构大额申购业务的公告</h2><table><tr><td>基金主代码</td><td>019547</td></tr><tr><td>限制申购金额</td><td>100.00</td></tr><tr><td>下属分级基金的交易代码</td><td>019547</td><td>019548</td></tr><tr><td>下属分级基金的限制申购金额</td><td>100.00</td><td>100.00</td></tr></table>'.encode()
        session = FakeSession(gets={product_url: FakeResponse(product_html, product_url), detail_url: FakeResponse(notice_html, detail_url)})
        snapshot = CmfchinaAdapter(session=session, clock=fixed_clock).fetch_direct_limit(FundIdentity("招商", "019548", "招商纳斯达克100ETF联接C"))
        self.assertEqual(snapshot.limit, "100元")
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.channel, "招商基金官网直销机构公告")

    def test_auth_boundary_keeps_trade_submission_out_of_scope(self):
        boundary = CmfchinaAdapter().auth_boundary()
        self.assertEqual(boundary.status, "public_with_auth_trade_boundary")
        self.assertFalse(boundary.requires_login)


if __name__ == "__main__":
    unittest.main()
