import sqlite3
from dataclasses import replace

from config import Config
from db import Database
from integrations.stripe.fake import FakeStripeAdapter


def test_recovery_schema_and_fake_intent_are_sanitized_and_idempotent(tmp_path):
    db=Database(str(tmp_path/'recovery.sqlite')); db.migrate()
    with db.connect() as con:
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'storefront_resolution_reviews','storefront_resolution_actions','storefront_operation_attempts'} <= tables
        assert 'provider_revision' in {r[1] for r in con.execute('PRAGMA table_info(storefront_orders)')}
    stripe=FakeStripeAdapter(replace(Config(),database_path=str(tmp_path/'unused.sqlite')))
    order={'public_id':'order-safe','price_snapshot':'12.00','currency_snapshot':'USD'}
    item={'title':'Coat'}
    intent_id=stripe.create_checkout(order,item,'ok','cancel')['payment_intent']
    observed=stripe.retrieve_payment_intent(intent_id)
    assert set(observed)=={'id','status','capture_method','amount','amount_capturable','amount_received','currency'}
    stripe.capture(intent_id,'capture:order-safe'); stripe.capture(intent_id,'capture:order-safe')
    assert stripe.retrieve_payment_intent(intent_id)['status']=='succeeded'
    assert len(stripe.captures)==1
