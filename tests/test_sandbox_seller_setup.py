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


def test_initial_setup_fails_closed_instead_of_creating_commerce_policy(tmp_path):
    cfg = Config(database_path=str(tmp_path / "setup.sqlite"), marketplace_id="EBAY_US",
                 ebay_provider_mode="sandbox", environment="sandbox")
    db = Database(cfg.database_path); db.migrate()
    client = FakeClient([{"programs": [{"programType": "SELLING_POLICY_MANAGEMENT"}]},
                         {"fulfillmentPolicies": []}])
    with pytest.raises(EbayError, match="must be supplied explicitly"):
        SandboxSellerSetup(cfg, db, client).run()
    assert db.get_seller_setup() is None
    assert all(method == "GET" for method, _, _ in client.calls)

def test_reuses_existing_resources_without_writes(tmp_path):
    cfg = Config(database_path=str(tmp_path / "reuse.sqlite"), marketplace_id="EBAY_US", ebay_provider_mode="sandbox", environment="sandbox")
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


def test_setup_reuses_preconfigured_resources_without_writes(tmp_path):
    cfg = Config(database_path=str(tmp_path / "reuse-all.sqlite"), marketplace_id="EBAY_US",
                 ebay_provider_mode="sandbox", environment="sandbox")
    db = Database(cfg.database_path); db.migrate()
    resources = [("fulfillment", "F"), ("payment", "P"), ("return", "R")]
    responses = [{"programs": [{"programType": "SELLING_POLICY_MANAGEMENT"}]}]
    responses += [{kind + "Policies": [policy(kind, identifier)]} for kind, identifier in resources]
    responses += [{"merchantLocationKey": "vintage-ops-main", "merchantLocationStatus": "ENABLED"}]
    client = FakeClient(responses)
    saved = SandboxSellerSetup(cfg, db, client).run()
    assert (saved["fulfillment_policy_id"], saved["payment_policy_id"],
            saved["return_policy_id"]) == ("F", "P", "R")
    assert all(method == "GET" for method, _, _ in client.calls)

def test_environment_and_marketplace_guards(tmp_path):
    db = Database(str(tmp_path / "guard.sqlite")); db.migrate()
    with pytest.raises(EbayError):
        SandboxSellerSetup(Config(ebay_provider_mode="mock"), db, FakeClient([])).run()
    with pytest.raises(EbayError):
        SandboxSellerSetup(Config(ebay_provider_mode="sandbox", environment="sandbox", marketplace_id="EBAY_GB"), db, FakeClient([])).run()


def location_setup(tmp_path, responses):
    cfg = Config(database_path=str(tmp_path / "location.sqlite"), marketplace_id="EBAY_US", ebay_provider_mode="sandbox", environment="sandbox")
    db = Database(cfg.database_path); db.migrate()
    client = FakeClient(responses)
    return SandboxSellerSetup(cfg, db, client), client


def test_missing_location_fails_closed_without_create(tmp_path):
    setup, client = location_setup(tmp_path, [EbayError("remote", "not found", status=404)])
    with pytest.raises(EbayError, match="Configure the Sandbox merchant location explicitly"):
        setup._location()
    assert client.calls == [("GET", "/sell/inventory/v1/location/vintage-ops-main", {})]

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


def test_location_404_is_not_created(tmp_path):
    setup, client = location_setup(tmp_path, [EbayError("remote", "not found", status=404)])
    with pytest.raises(EbayError, match="Configure the Sandbox merchant location explicitly"):
        setup._location()
    assert all(method == "GET" for method, _, _ in client.calls)

def test_location_non_404_lookup_error_is_propagated_unchanged(tmp_path):
    error = EbayError("remote", "service unavailable", status=503, request_id="request-1")
    setup, client = location_setup(tmp_path, [error])

    with pytest.raises(EbayError) as raised:
        setup._location()

    assert raised.value is error
    assert client.calls == [("GET", "/sell/inventory/v1/location/vintage-ops-main", {})]
    assert all(path != "/sell/inventory/v1/location?limit=200" for _, path, _ in client.calls)


def test_missing_location_error_is_configuration_failure(tmp_path):
    setup, _ = location_setup(tmp_path, [EbayError("remote", "not found", status=404)])
    with pytest.raises(EbayError) as raised:
        setup._location()
    assert raised.value.kind == "configuration"
