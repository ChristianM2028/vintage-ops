"""Atomic, retry-safe seller fulfillment commands."""
import re

from db import now


class FulfillmentError(ValueError):
    pass


class FulfillmentService:
    _rank = {"unfulfilled": 0, "packing": 1, "shipped": 2, "delivered": 3}

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _text(value, label, maximum):
        value = str(value or "").strip()
        if not value or len(value) > maximum or not value.isprintable():
            raise FulfillmentError(f"Invalid {label}")
        if re.search(r"(?:https?://|www\.|[a-z][a-z0-9+.-]*://)", value, re.I):
            raise FulfillmentError(f"Invalid {label}")
        return value

    def _reject(self, public_id, action, token, reason):
        # The classification is intentionally finite; submitted values and PII
        # never enter either application logs or audit metadata.
        try:
            with self.db.transaction(immediate=True) as con:
                order = con.execute("SELECT id FROM storefront_orders WHERE public_id=?", (public_id,)).fetchone()
                if not order:
                    return
                con.execute("INSERT OR IGNORE INTO storefront_request_tokens(order_id,action,token,outcome,created_at) VALUES(?,?,?,?,?)",
                            (order["id"], action, token, "rejected", now()))
                self.db.append_order_event(public_id, "fulfillment_action_rejected", actor="seller",
                    lifecycle="fulfillment", metadata={"action": action, "reason": reason},
                    dedupe_key=f"request:{action}:{token}:rejected", con=con)
        except Exception:
            pass

    def _run(self, public_id, action, token, operation):
        token = self._text(token, "request token", 200)
        try:
            with self.db.transaction(immediate=True) as con:
                order = con.execute("SELECT * FROM storefront_orders WHERE public_id=?", (public_id,)).fetchone()
                if not order:
                    raise FulfillmentError("Order not found")
                prior = con.execute("SELECT outcome FROM storefront_request_tokens WHERE order_id=? AND action=? AND token=?",
                                    (order["id"], action, token)).fetchone()
                if prior:
                    if prior["outcome"] == "succeeded": return dict(order)
                    raise FulfillmentError("Request was rejected")
                result = operation(con, order, token)
                con.execute("INSERT INTO storefront_request_tokens(order_id,action,token,outcome,created_at) VALUES(?,?,?,?,?)",
                            (order["id"], action, token, "succeeded", now()))
                return result
        except FulfillmentError as exc:
            self._reject(public_id, action, token, "invalid_request")
            raise

    @staticmethod
    def _eligible(order):
        if order["state"] != "sold" or order["payment_state"] != "captured":
            raise FulfillmentError("Order is not captured and sold")
        if order["fulfillment_state"] == "not_applicable":
            raise FulfillmentError("Fulfillment is not applicable")

    def _transition(self, public_id, target, token, carrier=None, tracking=None):
        action = target
        def op(con, order, request_token):
            self._eligible(order); current = order["fulfillment_state"]
            if current == target: return dict(order)
            allowed = ((current, target) in {("unfulfilled","packing"), ("unfulfilled","shipped"),
                                             ("packing","shipped"), ("shipped","delivered")})
            if not allowed: raise FulfillmentError("Invalid fulfillment transition")
            stamp = now(); extra = ""; args = []
            if target == "shipped":
                extra = ",tracking_carrier=?,tracking_number=?,tracking_url=NULL,shipped_at=COALESCE(shipped_at,?)"
                args = [carrier, tracking, stamp]
            if target == "delivered": extra = ",delivered_at=COALESCE(delivered_at,?)"; args = [stamp]
            changed=con.execute(f"UPDATE storefront_orders SET fulfillment_state=?,updated_at=?{extra} WHERE id=? AND fulfillment_state=?",
                                (target, stamp, *args, order["id"], current)).rowcount
            if not changed: raise FulfillmentError("Fulfillment changed concurrently")
            self.db.append_order_event(public_id,"fulfillment_changed",actor="seller",lifecycle="fulfillment_state",
                old_value=current,new_value=target,metadata={"action":action},dedupe_key=f"request:{action}:{request_token}",con=con)
            return self.db.get_order(public_id, con)
        return self._run(public_id, action, token, op)

    def mark_packing(self, public_id, request_token):
        return self._transition(public_id, "packing", request_token)

    def mark_shipped(self, public_id, carrier, tracking_number, request_token):
        try:
            carrier=self._text(carrier,"carrier",80); tracking_number=self._text(tracking_number,"tracking number",120)
        except FulfillmentError:
            self._reject(public_id,"shipped",str(request_token or "")[:200],"invalid_input"); raise
        return self._transition(public_id,"shipped",request_token,carrier,tracking_number)

    def mark_delivered(self, public_id, request_token):
        return self._transition(public_id,"delivered",request_token)

    def correct_tracking(self, public_id, carrier, tracking_number, reason, request_token):
        try:
            carrier=self._text(carrier,"carrier",80); tracking_number=self._text(tracking_number,"tracking number",120)
            reason=self._text(reason,"correction reason",500)
        except FulfillmentError:
            self._reject(public_id,"tracking",str(request_token or "")[:200],"invalid_input"); raise
        def op(con, order, token):
            self._eligible(order)
            if self._rank.get(order["fulfillment_state"], -1) < self._rank["shipped"]: raise FulfillmentError("Order has not shipped")
            stamp=now()
            con.execute("""INSERT INTO storefront_tracking_revisions(order_id,request_key,carrier,tracking_number,
                previous_carrier,previous_tracking_number,correction_reason,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (order["id"],token,carrier,tracking_number,order["tracking_carrier"],order["tracking_number"],reason,"seller",stamp))
            changed=con.execute("UPDATE storefront_orders SET tracking_carrier=?,tracking_number=?,tracking_url=NULL,updated_at=? WHERE id=? AND tracking_carrier IS ? AND tracking_number IS ?",
                (carrier,tracking_number,stamp,order["id"],order["tracking_carrier"],order["tracking_number"])).rowcount
            if not changed: raise FulfillmentError("Tracking changed concurrently")
            self.db.append_order_event(public_id,"tracking_corrected",actor="seller",lifecycle="tracking",
                metadata={"reason_recorded":True},dedupe_key=f"request:tracking:{token}",con=con)
            return self.db.get_order(public_id,con)
        return self._run(public_id,"tracking",request_token,op)

    def add_note(self, public_id, note, request_token):
        try: note=self._text(note,"note",2000)
        except FulfillmentError:
            self._reject(public_id,"note",str(request_token or "")[:200],"invalid_input"); raise
        def op(con, order, token):
            stamp=now(); con.execute("INSERT INTO storefront_order_notes(order_id,note,actor,created_at) VALUES(?,?,?,?)",(order["id"],note,"seller",stamp))
            self.db.append_order_event(public_id,"note_added",actor="seller",metadata={"content_logged":False},dedupe_key=f"request:note:{token}",con=con)
            return self.db.get_order(public_id,con)
        return self._run(public_id,"note",request_token,op)

    def acknowledge_attention(self, public_id, case_id, request_token):
        def op(con, order, token):
            case=con.execute("SELECT * FROM storefront_attention_cases WHERE id=? AND order_id=?",(case_id,order["id"])).fetchone()
            if not case: raise FulfillmentError("Attention case not found")
            if case["state"] == "open": con.execute("UPDATE storefront_attention_cases SET state='acknowledged',actor='seller',acknowledged_at=?,updated_at=? WHERE id=? AND state='open'",(now(),now(),case_id))
            self.db.append_order_event(public_id,"attention_acknowledged",actor="seller",metadata={"case_id":case_id},dedupe_key=f"request:attention:{token}",con=con)
            return self.db.get_order(public_id,con)
        return self._run(public_id,f"attention:{case_id}",request_token,op)
