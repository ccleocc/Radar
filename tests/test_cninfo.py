from datetime import date

from radar.cninfo import CNInfoClient


class FakeResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = "{}"

    def json(self):
        return {
            "totalRecordNum": 1,
            "announcements": [{
                "announcementId": "1234567890",
                "secCode": "603801",
                "secName": "<em>志邦</em>家居",
                "announcementTitle": "关于向全资子公司、孙公司增资并对<em>泰国</em>外投资项目的公告",
                "announcementTime": 1791417600000,
                "adjunctUrl": "finalpage/2026-10-08/1234567890.PDF",
            }],
        }


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.called = None

    def post(self, url, data, timeout):
        self.called = (url, data, timeout)
        return FakeResponse()


def test_cninfo_search_parse(monkeypatch):
    client = CNInfoClient(delay=0)
    default_headers = dict(client.session.headers)
    client.session = FakeSession()
    result, total = client.search("泰国", date(2026, 10, 1), date(2026, 10, 10), "sse", 1, 30)
    assert total == 1
    assert len(result) == 1
    item = result[0]
    assert item.stock_code == "603801"
    assert item.stock_name == "志邦家居"
    assert "<em>" not in item.stock_name
    assert item.pub_date == "2026-10-08"
    assert "<em>" not in item.raw_json
    assert item.pdf_url.endswith("finalpage/2026-10-08/1234567890.PDF")
    assert client.session.called[1]["seDate"] == "2026-10-01~2026-10-10"
    # The query must use CNINFO's full-text interface, not title-only search.
    assert client.session.called[1]["tabName"] == "fulltext"
    assert client.session.called[1]["searchkey"] == "泰国"
    assert client.session.called[1]["isHLtitle"] == "true"
    # A body-only term can return an announcement whose title lacks the term.
    assert "<em>" not in item.title
    assert "泰国" in item.title
    assert default_headers["X-Requested-With"] == "XMLHttpRequest"
    assert "disclosure/list/search" in default_headers["Referer"]
    assert "Chrome/120" in default_headers["User-Agent"]


def test_market_parameters_for_hk_neeq_and_bse(monkeypatch):
    client = CNInfoClient(delay=0)
    client.session = FakeSession()
    for market, expected_column, expected_plate in [
        ("hke", "hke", ""),
        ("third", "third", ""),
        ("bj", "szse", "bj"),
    ]:
        client.search("投资", date(2026, 1, 1), date(2026, 1, 2), market, 1, 30)
        params = client.session.called[1]
        assert params["column"] == expected_column
        assert params["plate"] == expected_plate


def test_json_response_prefers_utf8_bytes_over_wrong_charset_header():
    import json

    raw_content = json.dumps(
        {"announcements": [{"secName": "志邦家居", "announcementTitle": "关于泰国投资的公告"}]},
        ensure_ascii=False,
    ).encode("utf-8")

    class BadCharsetResponse:
        # Simulate a response whose HTTP charset metadata is wrong while payload is UTF-8.
        content = raw_content
        encoding = "iso-8859-1"

        def json(self):
            # Simulate a parser that trusts the wrong charset and produces mojibake.
            return json.loads(self.content.decode("iso-8859-1"))

    payload = CNInfoClient._decode_response_json(BadCharsetResponse())
    assert payload["announcements"][0]["secName"] == "志邦家居"
    assert payload["announcements"][0]["announcementTitle"] == "关于泰国投资的公告"
