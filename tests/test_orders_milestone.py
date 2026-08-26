import hashlib, hmac, json, sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from config import Config
from integrations.stripe.client import StripeAdapter
from integrations.stripe import StripeIntegrationError
from refund_service import RefundService


def test_live_stripe_keys_are_always_rejected():
    with pytest.raises(ValueError, match="Live Stripe"):
        replace(Config(), stripe_secret_key="sk_live_do_not_use")


def test_real_checkout_is_card_only_manual_capture_and_collects_shipping(monkeypatch):
    captured = {}
    class Sessions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="cs_test_one", url="https://checkout.stripe.test/one", payment_intent=None)
    monkeypatch.setitem(sys.modules, "stripe", SimpleNamespace(checkout=SimpleNamespace(Session=Sessions)))
    cfg = replace(Config(), stripe_provider_mode="test", stripe_secret_key="sk_test_example",
                  stripe_shipping_countries=("US", "CA"))
    result = StripeAdapter(cfg).create_checkout(
        {"public_id":"public","currency_snapshot":"USD","price_snapshot":"12.34"},
        {"title":"Coat"}, "https://example.test/success", "https://example.test/cancel")
    assert result["payment_intent"] is None
    assert captured["payment_method_types"] == ["card"]
    assert captured["payment_intent_data"]["capture_method"] == "manual"
    assert captured["shipping_address_collection"] == {"allowed_countries":["US","CA"]}
    assert captured["line_items"][0]["price_data"]["unit_amount"] == 1234


def test_webhook_attaches_intent_sells_and_audits_without_public_pii(tmp_path):
    cfg = replace(Config(), database_path=str(tmp_path/"db.sqlite"), stripe_provider_mode="fake",
                  seller_username="seller", seller_password_hash=generate_password_hash("password"))
    app = create_app(cfg); db=app.extensions["vintage_db"]
    item=db.create_item(sku="AUDIT-1",title="Coat",description="",price="25.00",currency="USD",quantity=1,condition="Used",brand="",size="",color="",category="1",category_name="Coats")
    db.update_item(item["id"],status="Active",storefront_visible=1)
    shopper=app.test_client()
    with shopper.session_transaction() as sess: sess["csrf_token"]="token"
    response=shopper.post("/shop/item/AUDIT-1/checkout",data={"csrf_token":"token"})
    public_id=response.headers["Location"].rsplit("/",1)[-1]
    order=db.get_order(public_id)
    event={"id":"evt_one","type":"payment_intent.amount_capturable_updated","data":{"object":{"id":order["stripe_payment_intent_id"],"status":"requires_capture","capture_method":"manual","metadata":{"order":public_id}}}}
    body=json.dumps(event).encode(); signature=hmac.new(cfg.stripe_webhook_secret.encode(),body,hashlib.sha256).hexdigest()
    assert shopper.post("/stripe/webhook",data=body,headers={"Stripe-Signature":signature,"Content-Type":"application/json"}).status_code==200
    assert db.get_order(public_id)["state"]=="sold"
    assert len(db.seller_order(public_id)["events"]) >= 6
    public=shopper.get(f"/shop/order/{public_id}")
    assert b"shipping_line1" not in public.data and b"customer_email" not in public.data
    assert shopper.get("/orders").status_code==302


def test_refund_webhooks_reconcile_exact_full_refunds_without_creating_them(tmp_path):
    cfg = replace(Config(), database_path=str(tmp_path/"refund-webhooks.sqlite"), stripe_provider_mode="fake")
    app=create_app(cfg); db=app.extensions["vintage_db"]; stripe=app.extensions["stripe_adapter"]
    item=db.create_item(sku="REFUND-1",title="Coat",description="",price="25.00",currency="USD",quantity=1,condition="Used",brand="",size="",color="",category="1",category_name="Coats")
    db.update_item(item["id"],status="Active",storefront_visible=1)
    client=app.test_client()
    with client.session_transaction() as session: session["csrf_token"]="token"
    response=client.post("/shop/item/REFUND-1/checkout",data={"csrf_token":"token"})
    public_id=response.headers["Location"].rsplit("/",1)[-1]
    assert client.post(f"/shop/order/{public_id}/complete",data={"csrf_token":"token"}).status_code==303
    order=db.get_order(public_id); pi=order["stripe_payment_intent_id"]

    def deliver(event_id,event_type,refund):
        event={"id":event_id,"type":event_type,"data":{"object":refund}}
        body=json.dumps(event).encode(); signature=hmac.new(cfg.stripe_webhook_secret.encode(),body,hashlib.sha256).hexdigest()
        return client.post("/stripe/webhook",data=body,headers={"Stripe-Signature":signature})

    exact={"id":"re_external","payment_intent":pi,"amount":2500,"currency":"usd","status":"succeeded","metadata":{"order":public_id}}
    assert deliver("evt_refund_1","refund.created",exact).status_code==200
    assert db.get_order(public_id)["refund_state"]=="refunded"
    assert stripe.refund_calls==[]

    late=dict(exact,status="pending")
    assert deliver("evt_refund_2","refund.updated",late).status_code==200
    assert db.get_order(public_id)["refund_state"]=="refunded"
    failed=dict(exact,status="failed")
    assert deliver("evt_refund_3","refund.failed",failed).status_code==200
    assert db.get_order(public_id)["refund_state"]=="refunded"
    assert any(case["kind"]=="refund_contradiction" for case in db.seller_order(public_id)["attention_cases"])


def test_identifiable_mismatched_refund_is_manual_review_and_charge_needs_refund_id(tmp_path):
    assert RefundService.normalize_webhook("charge.refunded",{
        "id":"ch_one","payment_intent":"pi_one","currency":"usd","amount_refunded":1000,
        "refunds":{"data":[]}})==[]
    assert RefundService.normalize_webhook("charge.refunded",{
        "id":"ch_one","payment_intent":"pi_one","currency":"usd",
        "refunds":{"data":[{"id":"re_one","amount":1000,"status":"succeeded"}]}})==[
            {"id":"re_one","payment_intent":"pi_one","amount":1000,"currency":"usd",
             "status":"succeeded","reference":None}]
    cfg=replace(Config(),database_path=str(tmp_path/"refund-review.sqlite"),stripe_provider_mode="fake")
    app=create_app(cfg); db=app.extensions["vintage_db"]; client=app.test_client()
    item=db.create_item(sku="REFUND-2",title="Coat",description="",price="10",currency="USD",quantity=1,condition="Used",brand="",size="",color="",category="1",category_name="Coats")
    db.update_item(item["id"],status="Active",storefront_visible=1)
    order=db.create_storefront_order("REFUND-2",provider_identity="fake")
    order=db.attach_checkout(order["public_id"],"cs_refund_2","pi_refund_2")
    db.record_provider_capture(order["public_id"],1000,"USD")
    event={"id":"evt_bad_refund","type":"refund.updated","data":{"object":{"id":"re_bad","payment_intent":"pi_refund_2","amount":999,"currency":"usd","status":"succeeded"}}}
    body=json.dumps(event).encode(); signature=hmac.new(cfg.stripe_webhook_secret.encode(),body,hashlib.sha256).hexdigest()
    assert client.post("/stripe/webhook",data=body,headers={"Stripe-Signature":signature}).status_code==200
    assert db.get_order(order["public_id"])["refund_state"]=="manual_resolution"
    assert db.seller_order(order["public_id"])["attention_cases"][0]["summary"]=="Stripe refund data conflicts with the recorded full refund; review required."


def test_failed_webhook_can_be_claimed_again(tmp_path):
    app=create_app(replace(Config(),database_path=str(tmp_path/"retry.sqlite")))
    db=app.extensions["vintage_db"]
    assert db.claim_webhook("evt_retry","test")
    db.finish_webhook("evt_retry","safe failure")
    assert db.claim_webhook("evt_retry","test")
    assert not db.claim_webhook("evt_retry","test")


def test_webhook_and_operation_claim_leases_are_durable(tmp_path):
    app=create_app(replace(Config(),database_path=str(tmp_path/"claims.sqlite")))
    db=app.extensions["vintage_db"]
    assert db.claim_webhook("evt","test")=="claimed"
    assert db.claim_webhook("evt","test")=="busy"
    with db.transaction(immediate=True) as con:
        con.execute("UPDATE stripe_webhook_events SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE event_id='evt'")
    assert db.claim_webhook("evt","test")=="claimed"
    db.finish_webhook("evt","temporary")
    assert db.claim_webhook("evt","test")=="claimed"
    db.finish_webhook("evt")
    with db.connect() as con:
        row=con.execute("SELECT * FROM stripe_webhook_events WHERE event_id='evt'").fetchone()
        assert (row["state"],row["attempts"],row["error"])==("processed",3,None)

    item=db.create_item(sku="CLAIM-1",title="Coat",description="",price="25",currency="USD",quantity=1,condition="Used",brand="",size="",color="",category="1",category_name="Coats")
    db.update_item(item["id"],status="Active",storefront_visible=1)
    order=db.create_storefront_order("CLAIM-1",provider_identity="fake")
    first=db.claim_operation(order["public_id"],"stripe_capture",lease_seconds=60)
    assert first["disposition"]=="claimed"
    assert db.claim_operation(order["public_id"],"stripe_capture")["disposition"]=="busy"
    assert db.complete_operation(order["public_id"],"stripe_capture",first["token"],result="captured")=="already_complete"
    assert db.claim_operation(order["public_id"],"stripe_capture")["disposition"]=="already_complete"


def test_provider_identity_merge_and_malformed_hash_are_safe(tmp_path):
    cfg=replace(Config(),database_path=str(tmp_path/"identity.sqlite"),seller_username="seller",seller_password_hash="not-a-werkzeug-hash")
    app=create_app(cfg); db=app.extensions["vintage_db"]
    item=db.create_item(sku="IDENT-1",title="Coat",description="",price="25",currency="USD",quantity=1,condition="Used",brand="",size="",color="",category="1",category_name="Coats")
    db.update_item(item["id"],status="Active",storefront_visible=1)
    order=db.create_storefront_order("IDENT-1",provider_identity="stripe_test")
    order=db.attach_checkout(order["public_id"],"session-one","intent-one")
    db.attach_payment_intent(order["public_id"],"intent-one",{"customer_email":"buyer@example.test","shipping_name":None})
    merged=db.attach_payment_intent(order["public_id"],"intent-one",{"customer_email":None,"shipping_name":"Buyer"})
    assert merged["stripe_provider_identity"]=="stripe_test"
    assert (merged["customer_email"],merged["shipping_name"])==("buyer@example.test","Buyer")
    conflict=db.attach_checkout(order["public_id"],"session-two","intent-one")
    assert conflict["state"]=="manual_resolution"
    client=app.test_client(); client.get("/login")
    with client.session_transaction() as session: token=session["csrf_token"]
    response=client.post("/login",data={"username":"seller","password":"secret hash content","csrf_token":token})
    assert response.status_code==200
    assert b"secret hash content" not in response.data and b"not-a-werkzeug-hash" not in response.data


def test_retrieve_session_wraps_sdk_failures_without_raw_details(monkeypatch):
    class InvalidRequestError(Exception):
        http_status=404
    class Sessions:
        @staticmethod
        def retrieve(*args,**kwargs): raise InvalidRequestError("sk_test_secret raw customer")
    monkeypatch.setitem(sys.modules,"stripe",SimpleNamespace(checkout=SimpleNamespace(Session=Sessions)))
    with pytest.raises(StripeIntegrationError) as caught:
        StripeAdapter(replace(Config(),stripe_provider_mode="test",stripe_secret_key="sk_test_example")).retrieve_session("session")
    assert caught.value.kind=="session_not_found" and not caught.value.ambiguous
    assert "secret" not in str(caught.value)
