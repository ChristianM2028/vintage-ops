import hashlib, hmac, json
from dataclasses import replace

from app import create_app
from config import Config


def test_irrelevant_webhook_is_durably_classified_and_duplicate_is_safe(tmp_path):
    cfg=replace(Config(),database_path=str(tmp_path/'webhooks.sqlite'),stripe_provider_mode="fake")
    app=create_app(cfg); client=app.test_client()
    body=json.dumps({'id':'evt_irrelevant','type':'customer.updated','data':{'object':{'email':'never-store@example.test'}}}).encode()
    signature=hmac.new(cfg.stripe_webhook_secret.encode(),body,hashlib.sha256).hexdigest()
    headers={'Stripe-Signature':signature,'Content-Type':'application/json'}
    assert client.post('/stripe/webhook',data=body,headers=headers).status_code==200
    assert client.post('/stripe/webhook',data=body,headers=headers).status_code==200
    with app.extensions['vintage_db'].connect() as con:
        row=con.execute("SELECT * FROM stripe_webhook_events WHERE event_id='evt_irrelevant'").fetchone()
        assert row['state']=='processed' and row['correlation_state']=='irrelevant'
        assert 'email' not in row.keys()
