from dataclasses import replace

import pytest

from config import Config
from db import Database
from integrations.ebay.errors import EbayError
from integrations.ebay.seller_setup import SandboxSellerSetup


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def policy(kind, identifier):
    names = {"fulfillment": "Vintage Ops Sandbox Fulfillment",
             "payment": "Vintage Ops Sandbox Payment", "return": "Vintage Ops Sandbox Returns"}
    return {"name": names[kind], "marketplaceId": "EBAY_US", kind + "PolicyId": identifier}


def test_initial_setup_exact_contract_payloads_and_persistence(tmp_path):
    cfg = Config(database_path=str(tmp_path / "setup.sqlite"), marketplace_id="EBAY_US", environment="sandbox")
    db = Database(cfg.database_path); db.migrate()
    responses = [{"programs": [{"programType": "OUT_OF_STOCK_CONTROL"}]}, {}]
    for kind, identifier in (("fulfillment", "F1"), ("payment", "P1"), ("return", "R1")):
        responses += [{kind + "Policies": []}, {}, {kind + "Policies": [policy(kind, identifier)]}]
    responses += [EbayError("remote", "not found", status=404), {},
                  {"merchantLocationKey": "vintage-ops-main", "merchantLocationStatus": "DISABLED"}, {}]
    client = FakeClient(responses)

    saved = SandboxSellerSetup(cfg, db, client).run()

    assert (saved["seller_id"], saved["fulfillment_policy_id"], saved["payment_policy_id"],
            saved["return_policy_id"], saved["merchant_location_key"], saved["marketplace"]) == (
                None, "F1", "P1", "R1", "vintage-ops-main", "EBAY_US")
    assert client.calls[1] == ("POST", "/sell/account/v1/program/opt_in",
                               {"json": {"programType": "SELLING_POLICY_MANAGEMENT"}, "expected": (200,)})
    creates = {(method, path): kwargs["json"] for method, path, kwargs in client.calls if "json" in kwargs and "_policy" in path}
    assert creates[("POST", "/sell/account/v1/payment_policy")] == {
        "name": "Vintage Ops Sandbox Payment", "marketplaceId": "EBAY_US",
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES", "default": True}]}
    location = next(c for c in client.calls
                    if c[:2] == ("POST", "/sell/inventory/v1/location/vintage-ops-main"))
    assert location == ("POST", "/sell/inventory/v1/location/vintage-ops-main", {"json": {
        "location": {"address": {"addressLine1": "2025 Hamilton Avenue", "city": "San Jose",
            "stateOrProvince": "CA", "postalCode": "95125", "country": "US"}},
        "locationTypes": ["WAREHOUSE"], "name": "Vintage Ops Sandbox Main"}, "expected": (204,)})
    assert client.calls[-1][:2] == ("POST", "/sell/inventory/v1/location/vintage-ops-main/enable")
    assert all(path != "/sell/inventory/v1/location?limit=200" for _, path, _ in client.calls)


def test_reuses_existing_resources_without_writes(tmp_path):
    cfg = Config(database_path=str(tmp_path / "reuse.sqlite"), marketplace_id="EBAY_US", environment="sandbox")
    db = Database(cfg.database_path); db.migrate()
    responses = [{"programs": [{"programType": "SELLING_POLICY_MANAGEMENT"}]}]
    responses += [{kind + "Policies": [policy(kind, identifier)]} for kind, identifier in
                  (("fulfillment", "F"), ("payment", "P"), ("return", "R"))]
    responses += [{"merchantLocationKey": "vintage-ops-main", "merchantLocationStatus": "ENABLED"}]
    client = FakeClient(responses)
    SandboxSellerSetup(cfg, db, client).run()
    assert all(method == "GET" for method, _, _ in client.calls)
    assert [path for _, path, _ in client.calls] == ["/sell/account/v1/program/get_opted_in_programs",
        "/sell/account/v1/fulfillment_policy?marketplace_id=EBAY_US",
        "/sell/account/v1/payment_policy?marketplace_id=EBAY_US",
        "/sell/account/v1/return_policy?marketplace_id=EBAY_US",
        "/sell/inventory/v1/location/vintage-ops-main"]
    assert all(path != "/sell/inventory/v1/location?limit=200" for _, path, _ in client.calls)


def test_second_setup_reuses_resources_without_opt_in_again(tmp_path):
    cfg = Config(database_path=str(tmp_path / "twice.sqlite"), marketplace_id="EBAY_US", environment="sandbox")
    db = Database(cfg.database_path); db.migrate()
    resources = [("fulfillment", "F"), ("payment", "P"), ("return", "R")]
    responses = [{"programs": []}, {}]
    for kind, identifier in resources:
        responses += [{kind + "Policies": []}, {}, {kind + "Policies": [policy(kind, identifier)]}]
    responses += [EbayError("remote", "not found", status=404), {}, {
        "merchantLocationKey": "vintage-ops-main", "merchantLocationStatus": "ENABLED"}]
    responses += [{"programs": [{"programType": "SELLING_POLICY_MANAGEMENT"}]}]
    responses += [{kind + "Policies": [policy(kind, identifier)]} for kind, identifier in resources]
    responses += [{"merchantLocationKey": "vintage-ops-main", "merchantLocationStatus": "ENABLED"}]
    client = FakeClient(responses)
    setup = SandboxSellerSetup(cfg, db, client)

    setup.run()
    setup.run()

    opt_ins = [call for call in client.calls
               if call[:2] == ("POST", "/sell/account/v1/program/opt_in")]
    assert opt_ins == [("POST", "/sell/account/v1/program/opt_in", {
        "json": {"programType": "SELLING_POLICY_MANAGEMENT"}, "expected": (200,)})]
    assert len([call for call in client.calls if call[0] == "POST" and "_policy" in call[1]]) == 3
    assert len([call for call in client.calls
                if call[:2] == ("POST", "/sell/inventory/v1/location/vintage-ops-main")]) == 1
    assert all(path != "/sell/inventory/v1/location?limit=200" for _, path, _ in client.calls)


def test_environment_and_marketplace_guards(tmp_path):
    db = Database(str(tmp_path / "guard.sqlite")); db.migrate()
    with pytest.raises(EbayError):
        SandboxSellerSetup(Config(environment="production"), db, FakeClient([])).run()
    with pytest.raises(EbayError):
        SandboxSellerSetup(Config(environment="sandbox", marketplace_id="EBAY_GB"), db, FakeClient([])).run()


def location_setup(tmp_path, responses):
    cfg = Config(database_path=str(tmp_path / "location.sqlite"), marketplace_id="EBAY_US", environment="sandbox")
    db = Database(cfg.database_path); db.migrate()
    client = FakeClient(responses)
    return SandboxSellerSetup(cfg, db, client), client


@pytest.mark.parametrize("create_error", [
    EbayError("remote", "HTTP 500", status=500),
    EbayError("conflict", "already exists", status=409),
])
def test_uncertain_location_create_is_confirmed_without_duplicate_create(tmp_path, create_error):
    confirmed = {"merchantLocationKey": "vintage-ops-main", "merchantLocationStatus": "ENABLED"}
    setup, client = location_setup(tmp_path, [
        EbayError("remote", "not found", status=404), create_error, confirmed, confirmed])

    assert setup._location() == "vintage-ops-main"
    assert setup._location() == "vintage-ops-main"
    assert [(method, path) for method, path, _ in client.calls] == [
        ("GET", "/sell/inventory/v1/location/vintage-ops-main"),
        ("POST", "/sell/inventory/v1/location/vintage-ops-main"),
        ("GET", "/sell/inventory/v1/location/vintage-ops-main"),
        ("GET", "/sell/inventory/v1/location/vintage-ops-main")]
    assert all(path != "/sell/inventory/v1/location?limit=200" for _, path, _ in client.calls)


def test_enabled_location_does_not_issue_enable_request(tmp_path):
    setup, client = location_setup(tmp_path, [{
        "merchantLocationKey": "vintage-ops-main", "merchantLocationStatus": "ENABLED"}])

    setup._location()

    assert client.calls == [("GET", "/sell/inventory/v1/location/vintage-ops-main", {})]


def test_disabled_location_is_enabled_only_after_observed_disabled(tmp_path):
    setup, client = location_setup(tmp_path, [{
        "merchantLocationKey": "vintage-ops-main", "merchantLocationStatus": "DISABLED"}, {}])

    setup._location()

    assert [(method, path) for method, path, _ in client.calls] == [
        ("GET", "/sell/inventory/v1/location/vintage-ops-main"),
        ("POST", "/sell/inventory/v1/location/vintage-ops-main/enable")]


def test_location_404_is_created_and_confirmed(tmp_path):
    confirmed = {"merchantLocationKey": "vintage-ops-main", "merchantLocationStatus": "ENABLED"}
    setup, client = location_setup(tmp_path, [
        EbayError("remote", "not found", status=404), {}, confirmed])

    assert setup._location() == "vintage-ops-main"
    assert [(method, path) for method, path, _ in client.calls] == [
        ("GET", "/sell/inventory/v1/location/vintage-ops-main"),
        ("POST", "/sell/inventory/v1/location/vintage-ops-main"),
        ("GET", "/sell/inventory/v1/location/vintage-ops-main")]
    assert all(path != "/sell/inventory/v1/location?limit=200" for _, path, _ in client.calls)


def test_location_non_404_lookup_error_is_propagated_unchanged(tmp_path):
    error = EbayError("remote", "service unavailable", status=503, request_id="request-1")
    setup, client = location_setup(tmp_path, [error])

    with pytest.raises(EbayError) as raised:
        setup._location()

    assert raised.value is error
    assert client.calls == [("GET", "/sell/inventory/v1/location/vintage-ops-main", {})]
    assert all(path != "/sell/inventory/v1/location?limit=200" for _, path, _ in client.calls)


def test_unexpected_location_create_error_is_propagated_unchanged(tmp_path):
    error = EbayError("invalid_item", "invalid address", status=400, request_id="request-2")
    setup, client = location_setup(tmp_path, [
        EbayError("remote", "not found", status=404), error])

    with pytest.raises(EbayError) as raised:
        setup._location()

    assert raised.value is error
    assert [(method, path) for method, path, _ in client.calls] == [
        ("GET", "/sell/inventory/v1/location/vintage-ops-main"),
        ("POST", "/sell/inventory/v1/location/vintage-ops-main")]
    assert all(path != "/sell/inventory/v1/location?limit=200" for _, path, _ in client.calls)
