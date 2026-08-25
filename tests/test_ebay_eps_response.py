import hashlib
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from config import Config
from db import Database
from integrations.ebay.client import EbayClient, parse_upload_site_hosted_pictures
from integrations.ebay.errors import EbayError
from integrations.ebay.photos import EbayImagePublisher

FIXTURES = Path(__file__).parent / "fixtures" / "ebay"


def fixture(name):
    return (FIXTURES / name).read_bytes()


def test_eps_parser_is_namespace_independent_and_accepts_warning():
    default = parse_upload_site_hosted_pictures(fixture("upload_success_default.xml"))
    prefixed = parse_upload_site_hosted_pictures(fixture("upload_success_prefixed.xml"))
    warning = parse_upload_site_hosted_pictures(fixture("upload_warning.xml"))
    assert default["url"].endswith("default/s-l1600.jpg")
    assert prefixed["url"].endswith("prefixed/s-l1600.jpg")
    assert warning["ack"] == "Warning" and warning["url"].endswith("warning/s-l1600.jpg")
    assert warning["errors"][0] == {"ShortMessage": "Picture was resized.", "LongMessage": "The picture exceeded the preferred dimensions and was resized.", "ErrorCode": "21916790", "SeverityCode": "Warning"}


def test_eps_failure_exposes_only_sanitized_diagnostics():
    with pytest.raises(EbayError) as caught:
        parse_upload_site_hosted_pictures(fixture("upload_failure.xml"))
    assert str(caught.value) == "Picture format is invalid."
    assert caught.value.details[0]["ErrorCode"] == "21916604"
    assert set(caught.value.details[0]) <= {"ErrorCode", "SeverityCode", "ShortMessage", "LongMessage", "ErrorParameters"}


class TradingClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_item(tmp_path):
    cfg = replace(Config(), database_path=str(tmp_path / "db.sqlite"), upload_directory=str(tmp_path / "uploads"),
                  marketplace_mock_mode=False, ebay_image_upload_api="trading")
    db = Database(cfg.database_path)
    db.migrate()
    Path(cfg.upload_directory).mkdir()
    (Path(cfg.upload_directory) / "safe.jpg").write_bytes(b"representative-image-bytes")
    item = db.create_item(title="x", description="x", price="1", currency="USD", quantity=1,
                          condition="NEW", category="1", photo_urls=["https://i.ebayimg.com/old.jpg"],
                          photos=[{"filename": "safe.jpg", "original_filename": "private-name.jpg"}])
    return cfg, db, db.update_item(item["id"], ebay_integration_mode="ebay_sandbox")


def test_trading_url_and_fingerprint_persist_without_image_id_and_are_reused(tmp_path):
    cfg, db, item = make_item(tmp_path)
    result = parse_upload_site_hosted_pictures(fixture("upload_success_default.xml"))
    client = TradingClient([result])
    assert EbayImagePublisher(cfg, db, client).publish(item) == [result["url"]]
    saved = db.get_item(item["id"])
    photo = saved["photos"][0]
    assert photo["ebay_image_id"] is None
    assert photo["ebay_eps_url"] == result["url"]
    assert photo["ebay_source_fingerprint"] == hashlib.sha256(b"representative-image-bytes").hexdigest()
    EbayImagePublisher(cfg, db, client).publish(saved)
    assert len(client.calls) == 1


def test_incomplete_diagnostic_is_used_and_last_good_urls_survive(tmp_path):
    cfg, db, item = make_item(tmp_path)
    incomplete = parse_upload_site_hosted_pictures(fixture("upload_incomplete.xml"))
    diagnosed = {**incomplete, "errors": [{"ShortMessage": "Safe picture diagnostic"}]}
    with pytest.raises(EbayError) as caught:
        EbayImagePublisher(cfg, db, TradingClient([diagnosed])).publish(item)
    assert caught.value.kind == "photo_partial_failure"
    assert caught.value.details[0]["message"] == "Safe picture diagnostic"
    assert db.get_item(item["id"])["photo_urls"] == '["https://i.ebayimg.com/old.jpg"]'


class Response:
    status_code = 200
    headers = {}
    content = fixture("upload_warning.xml")
    def json(self):
        raise ValueError

class Session:
    def request(self, *args, **kwargs):
        return Response()

class OAuth:
    def access_token(self):
        return "secret-oauth-token"


def test_client_logs_never_include_xml_token_or_image_bytes(caplog):
    cfg = replace(Config(), ebay_image_upload_api="trading")
    with caplog.at_level(logging.INFO):
        result = EbayClient(cfg, OAuth(), Session()).request(
            "POST", "/ws/api.dll", data=b"representative-image-bytes",
            request_headers={"X-EBAY-API-CALL-NAME": "UploadSiteHostedPictures"})
    output = caplog.text
    assert result["ack"] == "Warning"
    assert "secret-oauth-token" not in output
    assert "representative-image-bytes" not in output
    assert "UploadSiteHostedPicturesResponse" not in output
