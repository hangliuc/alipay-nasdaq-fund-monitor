from datetime import datetime, timezone
import json
import unittest

from fund_monitor.fetch.direct_sales.adapters.base import FundIdentity, ProductSnapshot
from fund_monitor.fetch.direct_sales.adapters.wanjia import WanjiaAdapter
from tests.helpers import FakeResponse, FakeSession


def fixed_clock():
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


CATALOGUE = '''/* public official catalogue */
var FundArr = [
  {"fundcode":"019441","fundname":"万家纳斯达克100指数发起式(QDII)A","fundFullName":"万家纳斯达克100指数型发起式证券投资基金(QDII)A","fundtype":"QDII","levelofriskStr":"中高风险","setupdate":"2023年08月25日","valuedate":"2026-08-24","todaynetvalue":"1.2345","statusStr":"正常开放","buypoint":"1元","lastasset":"1000000元","url":"products/qdii/019441/index.html"},
  {"fundcode":"019442","fundname":"万家纳斯达克100指数发起式(QDII)C","fundFullName":"万家纳斯达克100指数型发起式证券投资基金(QDII)C","fundtype":"QDII","url":"/products/qdii/019442/index.html"},
  {"fundcode":"bad","fundname":"ignore"}
];
'''.encode("utf-8")


def catalogue_response(url, kwargs):
    return FakeResponse(content=CATALOGUE, url=url)


class WanjiaAdapterTests(unittest.TestCase):
    def test_catalogue_parser_keeps_raw_fields_and_resolves_root_relative_url(self):
        funds = WanjiaAdapter.parse_catalogue_script(CATALOGUE, WanjiaAdapter.catalogue_url)

        self.assertEqual([fund.code for fund in funds], ["019441", "019442"])
        self.assertEqual(funds[0].share_class, "A")
        self.assertEqual(funds[1].source_url, "https://www.wjasset.com/products/qdii/019442/index.html")
        self.assertEqual(funds[0].source_type, "wanjia_official_fund_catalogue_js")

    def test_discover_and_product_keep_observation_and_original_fields(self):
        product_url = "https://www.wjasset.com/products/qdii/019442/index.html"
        html = '''<html><head><title>万家纳斯达克100指数发起式(QDII)C - 万家基金</title></head>
        <body><input id="categaryId2" value="cat-019442">
        <table><tr><th>基金简称</th><td>网页简称</td><th>基金经理</th><td>测试经理</td></tr></table>
        </body></html>'''.encode("utf-8")
        session = FakeSession({
            WanjiaAdapter.catalogue_url: catalogue_response,
            product_url: FakeResponse(content=html, url=product_url),
        })
        adapter = WanjiaAdapter(session=session, clock=fixed_clock)
        funds = adapter.discover_funds()
        product = adapter.fetch_product(funds[1])

        self.assertEqual(product.code, "019442")
        self.assertEqual(product.name, "万家纳斯达克100指数发起式(QDII)C")
        self.assertEqual(product.inception_date, "")  # this share's catalogue row has no date
        self.assertEqual(product.fields["基金经理"], "测试经理")
        self.assertEqual(product.fields["公告分类ID"], "cat-019442")
        self.assertEqual(product.source_url, product_url)
        self.assertEqual(product.observed_at, fixed_clock().isoformat())

    def test_trade_status_accepts_jsonp_and_keeps_trade_raw_fields(self):
        product_url = "https://www.wjasset.com/products/qdii/019441/index.html"
        trade_url = WanjiaAdapter.trade_state_url
        trade = (
            'wjAdapterCallback({"resultCode":"ETS-5BP0000",'
            '"fundState":{"declarestate":"1","withdrawstate":"0",'
            '"valuagrstate":"1","rawTradeFlag":"X"}});'
        ).encode()
        session = FakeSession({
            WanjiaAdapter.catalogue_url: catalogue_response,
            product_url: FakeResponse(content="<title>产品页</title>".encode("utf-8"), url=product_url),
            trade_url: FakeResponse(content=trade, url=trade_url),
        })
        adapter = WanjiaAdapter(session=session, clock=fixed_clock)
        snapshot = adapter.fetch_trade_status(FundIdentity("万家", "019441", "测试"))

        channel = snapshot.channels[0]
        self.assertEqual(snapshot.api_status, 0)
        self.assertTrue(channel.subscription)
        self.assertFalse(channel.redemption)
        self.assertTrue(channel.sip)
        self.assertEqual(snapshot.raw["trade_rawTradeFlag"], "X")
        self.assertIn("trade!fundState", channel.channel)

    def test_trade_status_falls_back_to_product_when_public_api_has_no_state(self):
        product = ProductSnapshot(
            manager_id="万家", code="019442", name="测试", trade_status="正常开放",
            source_url="https://www.wjasset.com/products/qdii/019442/index.html",
            observed_at=fixed_clock().isoformat(), fields={"statusStr": "正常开放"},
        )
        session = FakeSession({WanjiaAdapter.trade_state_url: FakeResponse(content=b"bad response")})
        adapter = WanjiaAdapter(session=session, clock=fixed_clock)
        adapter.fetch_product = lambda fund: product
        snapshot = adapter.fetch_trade_status(FundIdentity("万家", "019442", "测试"))

        self.assertIsNone(snapshot.api_status)
        self.assertTrue(snapshot.channels[0].subscription)
        self.assertIn("trade_state_error", snapshot.raw)

    def test_notice_payload_and_announcement_parser_support_official_shapes(self):
        payload = {"data": {"contents": [
            {"contentTitle": "关于调整大额申购（含定期定额投资）业务金额限制的公告",
             "downloadUrl": "/upload/pdf/limit.pdf", "publishDate": 1753488000000, "contentId": 7},
            {"title": "季度报告", "url": "/upload/pdf/report.pdf", "publishDate": 1753488000000},
        ]}}
        notices = WanjiaAdapter.parse_notice_payload(payload, WanjiaAdapter.home_url)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["url"], "https://www.wjasset.com/upload/pdf/limit.pdf")
        self.assertEqual(notices[0]["date"], "2025-07-26")

        text = """下属基金份额的交易代码 019441 019442
        该分级基金是否暂停大额申购（含定期定额投资） 是 是
        下属基金份额的限制申购（含定期定额投资）金额（单位：人民币元） 50 万 50 万"""
        parsed = WanjiaAdapter.parse_announcement_text(
            text, "019442", notices[0]["title"], notices[0]["date"], notices[0]["url"]
        )
        self.assertEqual(parsed["limit"], "50万元")
        self.assertEqual(parsed["status"], "paused")
        self.assertEqual(parsed["announcement_date"], "2025-07-26")

        resumed = WanjiaAdapter.parse_announcement_text("恢复大额申购（含定期定额投资）", "019442")
        self.assertEqual(resumed["limit"], "不限")

    def test_direct_limit_and_record_preserve_official_notice_status(self):
        fund = FundIdentity("万家", "019442", "测试")
        product = ProductSnapshot(
            manager_id="万家", code=fund.code, name=fund.name,
            source_url="https://www.wjasset.com/products/qdii/019442/index.html",
            observed_at=fixed_clock().isoformat(), fields={"公告分类ID": "cat-019442"},
        )
        adapter = WanjiaAdapter(clock=fixed_clock)
        adapter.fetch_product = lambda _fund: product
        adapter._fetch_notice = lambda _fund, _category, _url: {
            "limit": "50万元", "status": "paused", "quota_type": "单日累计申购",
            "quota_remark": "官网公告", "source_url": "https://www.wjasset.com/upload/pdf/limit.pdf",
        }
        snapshot = adapter.fetch_direct_limit(fund)
        record = adapter.fetch_direct_sales_record(fund)

        self.assertEqual(snapshot.limit, "50万元")
        self.assertEqual(snapshot.status, "paused")
        self.assertEqual(snapshot.raw["公告分类ID"], "cat-019442")
        self.assertEqual(record["direct_sales_status"], "paused")
        self.assertEqual(record["direct_sales_url"], snapshot.source_url)

    def test_no_data_and_auth_boundaries_are_explicit(self):
        fund = FundIdentity("万家", "019442", "测试")
        product = ProductSnapshot(
            manager_id="万家", code=fund.code, name=fund.name,
            source_url="https://www.wjasset.com/products/qdii/019442/index.html",
            observed_at=fixed_clock().isoformat(), fields={},
        )
        adapter = WanjiaAdapter(clock=fixed_clock)
        adapter.fetch_product = lambda _fund: product
        adapter._fetch_notice = lambda _fund, _category, _url: None
        snapshot = adapter.fetch_direct_limit(fund)
        self.assertIsNone(snapshot.limit)
        self.assertEqual(snapshot.status, "official_api_no_data")
        self.assertIsNone(adapter.fetch_direct_sales_record(fund))

        adapter.fetch_product = lambda _fund: (_ for _ in ()).throw(PermissionError("需要认证"))
        auth = adapter.fetch_direct_limit(fund)
        self.assertEqual(auth.status, "official_interface_requires_auth")
        boundary = adapter.auth_boundary()
        self.assertEqual(boundary.status, "public_with_trade_boundary")
        self.assertTrue(boundary.requires_login)
        self.assertFalse(boundary.requires_captcha)
        self.assertFalse(boundary.requires_device_signature)


if __name__ == "__main__":
    unittest.main()
