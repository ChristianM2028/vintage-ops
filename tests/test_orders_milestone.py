import hashlib, hmac, json, sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from config import Config
from integrations.stripe.client import StripeAdapter


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
    cfg = replace(Config(), stripe_fake_mode=False, stripe_secret_key="sk_test_example",
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
    cfg = replace(Config(), database_path=str(tmp_path/"db.sqlite"), stripe_fake_mode=True,
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


def test_failed_webhook_can_be_claimed_again(tmp_path):
    app=create_app(replace(Config(),database_path=str(tmp_path/"retry.sqlite")))
    db=app.extensions["vintage_db"]
    assert db.claim_webhook("evt_retry","test")
    db.finish_webhook("evt_retry","safe failure")
    assert db.claim_webhook("evt_retry","test")
    assert not db.claim_webhook("evt_retry","test")
