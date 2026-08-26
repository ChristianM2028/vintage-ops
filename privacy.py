"""Bounded and idempotent expiry of customer-identifying operational data."""
from datetime import datetime, timedelta, timezone

def anonymize_expired(db, *, retention_days=730, limit=100, now=None):
    if not 1 <= limit <= 500 or not 30 <= retention_days <= 3650: raise ValueError("invalid anonymization bounds")
    cutoff=((now or datetime.now(timezone.utc))-timedelta(days=retention_days)).isoformat()
    with db.transaction(immediate=True) as con:
        ids=[r[0] for r in con.execute("SELECT id FROM storefront_orders WHERE created_at<? AND anonymized_at IS NULL ORDER BY id LIMIT ?",(cutoff,limit))]
        for oid in ids:
            con.execute("""UPDATE storefront_orders SET customer_email=NULL,shipping_name=NULL,shipping_line1=NULL,shipping_line2=NULL,
             shipping_city=NULL,shipping_state=NULL,shipping_postal_code=NULL,shipping_country=NULL,shipping_phone=NULL,
             tracking_carrier=NULL,tracking_number=NULL,tracking_url=NULL,anonymized_at=? WHERE id=?""",((now or datetime.now(timezone.utc)).isoformat(),oid))
            con.execute("DELETE FROM storefront_order_notes WHERE order_id=?",(oid,))
            con.execute("UPDATE storefront_tracking_revisions SET carrier=NULL,tracking_number=NULL,tracking_url=NULL,previous_carrier=NULL,previous_tracking_number=NULL,previous_tracking_url=NULL,correction_reason=NULL WHERE order_id=?",(oid,))
        return len(ids)
