import hashlib, hmac, json
from .errors import StripeIntegrationError

class FakeStripeAdapter:
    def __init__(self, config): self.cfg=config; self.sessions={}; self.captures=[]; self.cancellations=[]; self.calls=[]
    def create_checkout(self, order, item, success_url, cancel_url):
        self.calls.append({"order":order["public_id"],"amount":order["price_snapshot"],"currency":order["currency_snapshot"],"quantity":1,"name":item["title"]})
        sid="cs_test_"+order["public_id"][:16]; pi="pi_test_"+order["public_id"][:16]
        result={"id":sid,"payment_intent":pi,"url":success_url.replace("{ORDER_ID}",order["public_id"])}; self.sessions[sid]=result; return result
    def retrieve_session(self,session_id): return self.sessions[session_id]
    def capture(self,payment_intent_id,key):
        if key not in [x[1] for x in self.captures]: self.captures.append((payment_intent_id,key))
        return {"id":payment_intent_id,"status":"succeeded"}
    def cancel(self,payment_intent_id,key):
        if key not in [x[1] for x in self.cancellations]: self.cancellations.append((payment_intent_id,key))
        return {"id":payment_intent_id,"status":"canceled"}
    def verify_webhook(self,payload,signature):
        expected=hmac.new(self.cfg.stripe_webhook_secret.encode(),payload,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,signature or ""): raise StripeIntegrationError("invalid_signature","Invalid webhook signature")
        return json.loads(payload)
