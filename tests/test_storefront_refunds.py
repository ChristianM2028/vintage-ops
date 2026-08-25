"""Exact full-refund orchestration, reconciliation, and side-effect tests."""
import threading

import pytest

from integrations.stripe import StripeIntegrationError


def snapshot(db, public_id, item):
    order = db.get_order(public_id)
    with db.connect() as con:
        reservation = dict(con.execute("SELECT * FROM item_reservations WHERE order_id=?", (order["id"],)).fetchone())
    return db.get_item(item["id"]), reservation


@pytest.mark.parametrize("fulfillment", ["unfulfilled", "shipped", "delivered"])
def test_full_refund_success_and_repeat_have_no_inventory_or_marketplace_side_effects(
        order_factory, fulfillment):
    app, db, _, public_id, item = order_factory()
    if fulfillment != "unfulfilled":
        with db.transaction() as con:
            con.execute("UPDATE storefront_orders SET fulfillment_state=? WHERE public_id=?", (fulfillment, public_id))
    stripe = app.extensions["stripe_adapter"]
    before = snapshot(db, public_id, item)
    result = app.extensions["refund_service"].create_full(public_id)
    again = app.extensions["refund_service"].create_full(public_id)
    assert result["refund_state"] == again["refund_state"] == "refunded"
    assert len(stripe.refund_calls) == 1 and stripe.cancellations == []
    assert snapshot(db, public_id, item) == before


@pytest.mark.parametrize("mode,first_state,recheck", [
    ("pending", "pending", "succeeded"),
    ("ambiguous", "manual_resolution", "succeeded"),
])
def test_pending_and_ambiguous_results_reconcile_by_retrieval_only(
        order_factory, mode, first_state, recheck):
    app, db, _, public_id, _ = order_factory()
    stripe = app.extensions["stripe_adapter"]
    stripe.refund_mode = mode
    assert app.extensions["refund_service"].create_full(public_id)["refund_state"] == first_state
    calls = list(stripe.refund_calls)
    stripe.refund_mode = recheck
    assert app.extensions["refund_service"].recheck(public_id)["refund_state"] == "refunded"
    assert stripe.refund_calls == calls


def test_confirmed_failure_requires_explicit_retry(order_factory):
    app, db, _, public_id, _ = order_factory()
    stripe = app.extensions["stripe_adapter"]
    stripe.refund_mode = "failed"
    assert app.extensions["refund_service"].create_full(public_id)["refund_state"] == "none"
    assert len(stripe.refund_calls) == 1
    stripe.refund_mode = "succeeded"
    # A subsequent explicit request claims the failed operation again.
    assert app.extensions["refund_service"].create_full(public_id)["refund_state"] == "refunded"
    assert len(stripe.refund_calls) == 2


@pytest.mark.parametrize("change", [
    {"amount": 1}, {"amount": 999999}, {"currency": "eur"},
    {"payment_intent": "pi_wrong"},
])
def test_partial_excessive_currency_and_payment_intent_mismatches_require_review(
        order_factory, change):
    app, db, _, public_id, _ = order_factory()
    order = db.get_order(public_id)
    refund = {"id": "re_external", "payment_intent": order["stripe_payment_intent_id"],
              "amount": order["captured_amount"], "currency": order["captured_currency"],
              "status": "succeeded", **change}
    result = app.extensions["refund_service"].reconcile(order, refund)
    assert result["refund_state"] == "manual_resolution"
    assert db.seller_order(public_id)["attention_cases"]


def test_concurrent_refund_claims_create_exactly_one_provider_refund(order_factory):
    app, db, _, public_id, _ = order_factory()
    service = app.extensions["refund_service"]
    barrier = threading.Barrier(3)
    outcomes = []
    def run():
        barrier.wait(); outcomes.append(service.create_full(public_id)["refund_state"])
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join()
    assert len(app.extensions["stripe_adapter"].refund_calls) == 1
    assert db.get_order(public_id)["refund_state"] == "refunded"
    assert set(outcomes) <= {"pending", "refunded"}


def test_webhook_logical_dedup_out_of_order_and_contradiction_after_success(order_factory):
    app, db, _, public_id, _ = order_factory()
    service = app.extensions["refund_service"]
    order = db.get_order(public_id)
    base = {"id": "re_external", "payment_intent": order["stripe_payment_intent_id"],
            "amount": order["captured_amount"], "currency": "usd"}
    for status in ("pending", "pending", "succeeded", "pending", "failed"):
        service.reconcile(order, dict(base, status=status))
    assert db.get_order(public_id)["refund_state"] == "refunded"
    events = db.seller_order(public_id)["events"]
    assert len([e for e in events if e.get("dedupe_key") == "refund:re_external:pending"]) == 1
    cases = db.seller_order(public_id)["attention_cases"]
    assert len([case for case in cases if case["kind"] == "refund_contradiction"]) == 1
    assert app.extensions["stripe_adapter"].refund_calls == []
