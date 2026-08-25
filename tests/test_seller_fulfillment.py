"""Seller fulfillment state-machine, authorization, audit, and privacy tests."""
import itertools

import pytest

from fulfillment_service import FulfillmentError


STATES = ("not_applicable", "unfulfilled", "packing", "shipped", "delivered")
ALLOWED = {("unfulfilled", "packing"), ("unfulfilled", "shipped"),
           ("packing", "shipped"), ("shipped", "delivered")}


def set_fulfillment(db, public_id, state):
    with db.transaction() as con:
        con.execute("UPDATE storefront_orders SET fulfillment_state=? WHERE public_id=?",
                    (state, public_id))


@pytest.mark.parametrize("source,target", list(itertools.product(STATES, repeat=2)))
def test_every_fulfillment_transition_is_explicitly_allowed_or_forbidden(
        order_factory, source, target):
    app, db, _, public_id, _ = order_factory()
    service = app.extensions["fulfillment_service"]
    set_fulfillment(db, public_id, source)
    action = {
        "packing": lambda: service.mark_packing(public_id, "transition"),
        "shipped": lambda: service.mark_shipped(public_id, "UPS", "1Z999", "transition"),
        "delivered": lambda: service.mark_delivered(public_id, "transition"),
    }.get(target)
    if action is None:
        # There is deliberately no command capable of moving to these states.
        assert target in {"not_applicable", "unfulfilled"}
        return
    if source == target:
        action()  # exact retries are harmless
    elif (source, target) in ALLOWED:
        action()
    else:
        with pytest.raises(FulfillmentError):
            action()
    expected = target if source == target or (source, target) in ALLOWED else source
    assert db.get_order(public_id)["fulfillment_state"] == expected


def test_tracking_validation_history_reason_deduplication_and_atomic_snapshot(order_factory):
    app, db, _, public_id, _ = order_factory()
    service = app.extensions["fulfillment_service"]
    for carrier, number in (("", "TRACK"), ("UPS", ""), ("https://bad.test", "TRACK")):
        with pytest.raises(FulfillmentError):
            service.mark_shipped(public_id, carrier, number, f"bad-{number}-{carrier}")
    assert db.get_order(public_id)["fulfillment_state"] == "unfulfilled"

    service.mark_shipped(public_id, "UPS", "OLD-1", "ship-once")
    service.mark_shipped(public_id, "IGNORED", "IGNORED", "ship-once")
    with pytest.raises(FulfillmentError):
        service.correct_tracking(public_id, "USPS", "NEW-2", "", "correction-bad")
    service.correct_tracking(public_id, "USPS", "NEW-2", "Buyer supplied correction", "correction")
    service.correct_tracking(public_id, "FedEx", "IGNORED", "Ignored retry", "correction")

    detail = db.seller_order(public_id)
    assert (detail["tracking_carrier"], detail["tracking_number"]) == ("USPS", "NEW-2")
    assert len(detail["tracking_revisions"]) == 1
    revision = detail["tracking_revisions"][0]
    assert (revision["previous_carrier"], revision["previous_tracking_number"]) == ("UPS", "OLD-1")
    assert revision["correction_reason"] == "Buyer supplied correction"
    assert len([event for event in detail["events"]
                if event.get("dedupe_key") == "request:tracking:correction"]) == 1


def test_notes_are_append_only_attributed_timestamped_and_submit_safe(order_factory):
    app, db, _, public_id, _ = order_factory()
    service = app.extensions["fulfillment_service"]
    service.add_note(public_id, "First private note", "note-one")
    service.add_note(public_id, "Different retry body", "note-one")
    service.add_note(public_id, "Second private note", "note-two")
    notes = db.seller_order(public_id)["notes"]
    assert [note["note"] for note in notes] == ["First private note", "Second private note"]
    assert all(note["actor"] == "seller" and note["created_at"] for note in notes)
    with db.connect() as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(storefront_order_notes)")}
    assert not ({"updated_at", "deleted_at"} & columns)


def test_every_seller_order_route_requires_login_csrf_and_rejects_get_mutation(
        order_factory, seller_login):
    app, db, _, public_id, _ = order_factory()
    case = db.create_attention_case(public_id, "review", "Review it", "case")
    routes = [
        (f"/orders/{public_id}/packing", {"request_token": "p"}),
        (f"/orders/{public_id}/ship", {"request_token": "s", "carrier": "UPS", "tracking_number": "T"}),
        (f"/orders/{public_id}/tracking/correct", {"request_token": "t", "carrier": "UPS", "tracking_number": "T", "reason": "reason"}),
        (f"/orders/{public_id}/delivered", {"request_token": "d"}),
        (f"/orders/{public_id}/notes", {"request_token": "n", "note": "secret"}),
        (f"/orders/{public_id}/attention/{case['id']}/acknowledge", {"request_token": "a"}),
        (f"/orders/{public_id}/refund", {"confirmation": "CONFIRM"}),
        (f"/orders/{public_id}/refund/recheck", {"confirmation": "CONFIRM"}),
        (f"/orders/{public_id}/cancel", {"confirmation": "CONFIRM"}),
        (f"/orders/{public_id}/retry", {"confirmation": "CONFIRM"}),
    ]
    anonymous = app.test_client()
    seller, token = seller_login(app)
    baseline = dict(db.get_order(public_id))
    for url, form in routes:
        assert anonymous.post(url, data=form).status_code == 400  # CSRF runs before auth
        assert seller.post(url, data=form).status_code == 400
        assert seller.get(url).status_code == 405
    current = db.get_order(public_id)
    for field in ("state", "payment_state", "fulfillment_state", "refund_state"):
        assert current[field] == baseline[field]
    assert seller.get(f"/orders/{public_id}").status_code == 200
    assert seller.get(f"/orders/{public_id}/packing-slip").status_code == 200
    assert anonymous.get(f"/orders/{public_id}").status_code == 302
    assert anonymous.get(f"/orders/{public_id}/packing-slip").status_code == 302


@pytest.mark.parametrize("state,tracking_visible", [
    ("unfulfilled", False), ("packing", False), ("shipped", True), ("delivered", True),
])
def test_public_projection_headers_and_sensitive_value_denylist(
        order_factory, state, tracking_visible):
    app, db, shopper, public_id, _ = order_factory()
    secrets = ["address-secret", "email-secret@example.test", "+15555550123",
               "note-secret", "cs_secret", "pi_secret", "re_secret",
               "diagnostic-secret", "audit-secret", "manual-secret"]
    with db.transaction() as con:
        order = con.execute("SELECT id FROM storefront_orders WHERE public_id=?", (public_id,)).fetchone()
        con.execute("""UPDATE storefront_orders SET fulfillment_state=?,tracking_carrier='UPS',
          tracking_number='TRACK-SECRET',shipping_line1=?,customer_email=?,shipping_phone=?,
          stripe_checkout_session_id=?,stripe_payment_intent_id=?,stripe_refund_id=?,
          operation_error=?,conflict_details=? WHERE id=?""",
          (state, secrets[0], secrets[1], secrets[2], secrets[4], secrets[5], secrets[6],
           secrets[7], secrets[9], order["id"]))
        con.execute("INSERT INTO storefront_order_notes(order_id,note,actor,created_at) VALUES(?,?,?,?)",
                    (order["id"], secrets[3], "seller", "2026-01-01"))
        con.execute("INSERT INTO storefront_order_events(order_id,from_state,to_state,source,category,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (order["id"], "sold", "sold", "seller", "audit", '{"value":"audit-secret"}', "2026-01-01"))
    response = shopper.get(f"/shop/order/{public_id}")
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    body = response.get_data(as_text=True)
    assert all(secret not in body for secret in secrets)
    assert ("TRACK-SECRET" in body) is tracking_visible


def test_stale_fulfillment_compare_and_set_cannot_overwrite_active_snapshot(order_factory):
    _, db, _, public_id, _ = order_factory()
    assert db.compare_and_set_fulfillment(public_id, "unfulfilled", "packing")
    assert db.compare_and_set_fulfillment(public_id, "unfulfilled", "shipped") is None
    assert db.get_order(public_id)["fulfillment_state"] == "packing"
