import json, os, sqlite3, secrets
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

STATUSES = ("Draft", "Active", "Sold", "Delisted")
TRANSITIONS = {
    "Draft": {"Active", "Sold", "Delisted"}, "Active": {"Sold", "Delisted"},
    "Delisted": {"Active", "Sold"}, "Sold": set(),
}
def now(): return datetime.now(timezone.utc).isoformat()

class Disposition(str):
    """Explicit claim result, while retaining compatibility with old bool callers."""
    def __bool__(self): return self == "claimed"
class WebhookClaim(dict):
    def __bool__(self): return self.get("disposition")=="claimed"
    def __eq__(self,other):
        if isinstance(other,str): return self.get("disposition")==other
        return dict.__eq__(self,other)
    def __ne__(self,other): return not self.__eq__(other)

class Database:
    def __init__(self, path): self.path = path
    def connect(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        con = sqlite3.connect(self.path, timeout=30); con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON"); con.execute("PRAGMA busy_timeout=30000"); return con
    def migrate(self):
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            root=os.path.join(os.path.dirname(__file__), "migrations")
            for name in sorted(n for n in os.listdir(root) if n.endswith(".sql")):
                if con.execute("SELECT 1 FROM schema_migrations WHERE version=?",(name,)).fetchone(): continue
                con.executescript(open(os.path.join(root,name), encoding="utf8").read())
                con.execute("INSERT INTO schema_migrations VALUES (?,?)",(name,now()))
    def get_seller_setup(self):
        with self.connect() as con:
            row=con.execute("SELECT * FROM ebay_seller_setup WHERE id=1").fetchone()
            return dict(row) if row else None
    def save_seller_setup(self, seller_id, fulfillment_policy_id, payment_policy_id,
                          return_policy_id, merchant_location_key, marketplace):
        stamp=now()
        with self.transaction(immediate=True) as con:
            con.execute("""INSERT INTO ebay_seller_setup
                (id,seller_id,fulfillment_policy_id,payment_policy_id,return_policy_id,merchant_location_key,marketplace,updated_at)
                VALUES (1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                seller_id=excluded.seller_id, fulfillment_policy_id=excluded.fulfillment_policy_id,
                payment_policy_id=excluded.payment_policy_id, return_policy_id=excluded.return_policy_id,
                merchant_location_key=excluded.merchant_location_key, marketplace=excluded.marketplace,
                updated_at=excluded.updated_at""", (seller_id,fulfillment_policy_id,payment_policy_id,
                    return_policy_id,merchant_location_key,marketplace,stamp))
        return self.get_seller_setup()
    @contextmanager
    def transaction(self, immediate=False):
        con=self.connect()
        try:
            if immediate: con.execute("BEGIN IMMEDIATE")
            yield con; con.commit()
        except Exception: con.rollback(); raise
        finally: con.close()
    def create_item(self, photos=None, **v):
        """Atomically allocate the next human-readable SKU and insert photo metadata."""
        stamp=now(); fields=("title","description","price","currency","quantity","condition","brand","size","color","category","category_name","photo_urls","item_specifics","ebay_integration_mode","ebay_merchant_location_key","ebay_fulfillment_policy_id","ebay_payment_policy_id","ebay_return_policy_id","ebay_marketplace_id")
        vals=[json.dumps(v.get(k, [] if k=="photo_urls" else {})) if k in ("photo_urls","item_specifics") else v.get(k) for k in fields]
        with self.transaction(immediate=True) as c:
            # Explicit SKUs remain accepted for the isolated legacy Sandbox test
            # harness; the seller workflow never accepts or sends one.
            sku=v.get("sku")
            if not sku:
                highest=c.execute("SELECT COALESCE(MAX(CAST(SUBSTR(sku,4) AS INTEGER)),0) FROM items WHERE sku GLOB 'VO-[0-9]*'").fetchone()[0]
                sku=f"VO-{highest+1:06d}"
            cur=c.execute(f"INSERT INTO items (sku,{','.join(fields)},created_at,updated_at) VALUES (?,{','.join('?' for _ in fields)},?,?)", [sku]+vals+[stamp,stamp])
            for order,p in enumerate(photos or []):
                c.execute("INSERT INTO item_photos(item_id,filename,original_filename,display_order,created_at,updated_at) VALUES(?,?,?,?,?,?)",(cur.lastrowid,p["filename"],p["original_filename"],order,stamp,stamp))
            return self.get_item(cur.lastrowid,c)
    def get_item(self, item_id, con=None):
        own=con is None; con=con or self.connect(); row=con.execute("SELECT * FROM items WHERE id=?",(item_id,)).fetchone()
        result=dict(row) if row else None
        if result:
            result["photos"]=[dict(x) for x in con.execute("SELECT * FROM item_photos WHERE item_id=? ORDER BY display_order,id",(item_id,))]
            result["item_specifics"]=json.loads(result.get("item_specifics") or "{}")
        if own: con.close()
        return result
    def get_item_by_sku(self, sku):
        with self.connect() as con:
            row=con.execute("SELECT id FROM items WHERE sku=?",(sku,)).fetchone()
            return self.get_item(row["id"],con) if row else None
    @staticmethod
    def _public_item(row, con):
        """Return the deliberately small, ID-free representation used by the shop."""
        item={key: row[key] for key in ("sku","title","description","price","currency","quantity","condition","brand","size","color","category","category_name","status","created_at")}
        item["photos"]=[{"filename": p["filename"], "original_filename": p["original_filename"]} for p in con.execute("SELECT filename,original_filename FROM item_photos WHERE item_id=? ORDER BY display_order,id",(row["id"],))]
        return item
    def get_storefront_item_by_sku(self, sku):
        """Look up a visible product by its public SKU without returning internal IDs."""
        with self.connect() as con:
            row=con.execute("SELECT * FROM items WHERE sku=? AND storefront_visible=1",(sku,)).fetchone()
            if not row: return None
            return self._public_storefront_item(row,con)
    def _public_storefront_item(self, row, con):
        """Project an item and calculate its availability consistently."""
        item=self._public_item(row,con)
        reserved=con.execute("SELECT 1 FROM item_reservations WHERE item_id=? AND state='open' AND expires_at>?",(row["id"],now())).fetchone()
        item["availability"]="reserved" if reserved else ("available" if row["storefront_visible"] and row["status"]=="Active" and row["quantity"]>0 else "unavailable")
        return item
    def storefront_catalog(self, search=None, brand=None, size=None, min_price=None, max_price=None, sort="newest"):
        """Query visible, active inventory and return only storefront-safe fields."""
        clauses=["storefront_visible=1","status!='Sold'","quantity>0"]; args=[]
        if search:
            clauses.append("(LOWER(title) LIKE ? OR LOWER(description) LIKE ? OR LOWER(COALESCE(brand,'')) LIKE ?)")
            term=f"%{search.lower()}%"; args.extend((term,term,term))
        if brand: clauses.append("LOWER(COALESCE(brand,''))=LOWER(?)"); args.append(brand)
        if size: clauses.append("LOWER(COALESCE(size,''))=LOWER(?)"); args.append(size)
        if min_price is not None: clauses.append("CAST(price AS NUMERIC)>=?"); args.append(str(min_price))
        if max_price is not None: clauses.append("CAST(price AS NUMERIC)<=?"); args.append(str(max_price))
        orders={"newest":"created_at DESC,id DESC","price_asc":"CAST(price AS NUMERIC) ASC,id DESC","price_desc":"CAST(price AS NUMERIC) DESC,id DESC"}
        if sort not in orders: raise ValueError("Invalid storefront sort")
        with self.connect() as con:
            rows=con.execute("SELECT * FROM items WHERE "+" AND ".join(clauses)+" ORDER BY "+orders[sort],args).fetchall()
            return [self._public_storefront_item(row,con) for row in rows]
    def inventory(self, status=None):
        with self.connect() as c:
            sql="SELECT * FROM items"; args=[]
            if status in STATUSES: sql+=" WHERE status=?"; args=[status]
            rows=c.execute(sql+" ORDER BY created_at DESC,id DESC",args).fetchall()
            return [self.get_item(r["id"],c) for r in rows]
    def get_photos(self,item_id): return (self.get_item(item_id) or {}).get("photos",[])
    def update_item(self,item_id,**values):
        allowed={"title","description","price","currency","quantity","condition","brand","size","color","category","category_name","photo_urls","item_specifics","ebay_sku","ebay_offer_id","ebay_listing_id","ebay_publication_state","ebay_sync_state","local_revision","synced_revision","publication_lock_token","publication_lock_expires_at","last_error","status","storefront_visible","ebay_integration_mode","ebay_merchant_location_key","ebay_fulfillment_policy_id","ebay_payment_policy_id","ebay_return_policy_id","ebay_marketplace_id"}
        if not values: return self.get_item(item_id)
        if set(values)-allowed: raise ValueError("Unsupported item field")
        for key in ("photo_urls","item_specifics"):
            if key in values and not isinstance(values[key],str): values[key]=json.dumps(values[key])
        values["updated_at"]=now()
        with self.transaction() as c: c.execute("UPDATE items SET "+",".join(f"{k}=?" for k in values)+" WHERE id=?",[*values.values(),item_id])
        return self.get_item(item_id)
    def transition_status(self,item_id,status,con=None):
        if status not in STATUSES: raise ValueError("Invalid item status")
        item=self.get_item(item_id,con)
        if not item: raise ValueError("Item not found")
        if status != item["status"] and status not in TRANSITIONS[item["status"]]: raise ValueError(f"Cannot transition {item['status']} to {status}")
        if con:
            con.execute("UPDATE items SET status=?,updated_at=? WHERE id=?",(status,now(),item_id)); return self.get_item(item_id,con)
        return self.update_item(item_id,status=status)
    def update_marketplace(self,item_id,**values): return self.update_item(item_id,**values)
    def update_photo_eps(self, photo_id, image_id, url, fingerprint, error=None):
        """Persist one successful EPS upload independently of its siblings."""
        with self.transaction(immediate=True) as c:
            c.execute("""UPDATE item_photos SET ebay_image_id=?,ebay_eps_url=?,
                ebay_source_fingerprint=?,ebay_upload_error=?,updated_at=? WHERE id=?""",
                (image_id,url,fingerprint,error,now(),photo_id))
    def set_photo_eps_error(self, photo_id, error):
        # Do not erase a prior successful identity: a changed local file may be
        # retried while its older EPS object remains part of the promoted set.
        with self.transaction() as c:
            c.execute("UPDATE item_photos SET ebay_upload_error=?,updated_at=? WHERE id=?",(error,now(),photo_id))
    def promote_eps_photo_set(self, item_id, urls):
        """Atomically expose a complete ordered candidate as last-known-good."""
        with self.transaction(immediate=True) as c:
            row=c.execute("SELECT photo_set_revision FROM items WHERE id=?",(item_id,)).fetchone()
            if not row: raise ValueError("Item not found")
            c.execute("UPDATE items SET photo_urls=?,ebay_synced_photo_revision=?,updated_at=? WHERE id=?",
                      (json.dumps(urls),row["photo_set_revision"],now(),item_id))
            c.execute("UPDATE item_photos SET ebay_upload_error=NULL WHERE item_id=?",(item_id,))
    def update_item_and_photos(self,item_id,values,photos=None):
        """Atomically accept canonical edits and optional replacement photo rows."""
        allowed={"title","description","price","quantity","condition","category","category_name","brand","size","color","item_specifics"}
        if set(values)-allowed: raise ValueError("Unsupported editable item field")
        stamp=now(); values=dict(values); values["item_specifics"]=json.dumps(values.get("item_specifics",{}))
        with self.transaction(immediate=True) as c:
            c.execute("UPDATE items SET "+",".join(f"{k}=?" for k in values)+",local_revision=local_revision+1,ebay_sync_state=?,updated_at=? WHERE id=?",[*values.values(),"pending",stamp,item_id])
            if photos is not None:
                c.execute("DELETE FROM item_photos WHERE item_id=?",(item_id,))
                for order,p in enumerate(photos): c.execute("INSERT INTO item_photos(item_id,filename,original_filename,display_order,created_at,updated_at) VALUES(?,?,?,?,?,?)",(item_id,p["filename"],p["original_filename"],order,stamp,stamp))
            return self.get_item(item_id,c)
    def mutate_item_photos(self,item_id,values,ordered_photos):
        """Apply validated retains/replacements/additions without losing EPS state.

        Existing entries are ``{'id': ...}``; new entries carry filename and
        original_filename.  Returns the hydrated item and filenames made
        unreferenced by the committed mutation.
        """
        allowed={"title","description","price","quantity","condition","category","category_name","brand","size","color","item_specifics"}
        if set(values)-allowed: raise ValueError("Unsupported editable item field")
        stamp=now(); values=dict(values); values["item_specifics"]=json.dumps(values.get("item_specifics",{}))
        with self.transaction(immediate=True) as c:
            current={r["id"]:dict(r) for r in c.execute("SELECT * FROM item_photos WHERE item_id=?",(item_id,))}
            retained=[p["id"] for p in ordered_photos if "id" in p]
            if len(retained)!=len(set(retained)) or any(pid not in current for pid in retained): raise ValueError("Invalid photo selection")
            removed=[row["filename"] for pid,row in current.items() if pid not in retained]
            # Avoid transient UNIQUE(item_id, display_order) collisions.
            c.execute("UPDATE item_photos SET display_order=display_order+1000000 WHERE item_id=?",(item_id,))
            for order,p in enumerate(ordered_photos):
                if "id" in p:
                    c.execute("UPDATE item_photos SET display_order=?,updated_at=? WHERE id=? AND item_id=?",(order,stamp,p["id"],item_id))
                else:
                    c.execute("INSERT INTO item_photos(item_id,filename,original_filename,display_order,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                              (item_id,p["filename"],p["original_filename"],order,stamp,stamp))
            if current:
                placeholders=",".join("?" for _ in retained)
                if retained: c.execute(f"DELETE FROM item_photos WHERE item_id=? AND id NOT IN ({placeholders})",[item_id,*retained])
                else: c.execute("DELETE FROM item_photos WHERE item_id=?",(item_id,))
            c.execute("UPDATE items SET "+",".join(f"{k}=?" for k in values)+",local_revision=local_revision+1,photo_set_revision=photo_set_revision+1,ebay_sync_state='pending',updated_at=? WHERE id=?",
                      [*values.values(),stamp,item_id])
            return self.get_item(item_id,c),removed
    def acquire_publication_lock(self,item_id,seconds=120):
        from datetime import timedelta
        token=secrets.token_urlsafe(24); expiry=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()
        with self.transaction(immediate=True) as c:
            row=c.execute("SELECT publication_lock_token,publication_lock_expires_at FROM items WHERE id=?",(item_id,)).fetchone()
            if row["publication_lock_token"] and row["publication_lock_expires_at"] and row["publication_lock_expires_at"]>now(): return None
            c.execute("UPDATE items SET publication_lock_token=?,publication_lock_expires_at=? WHERE id=?",(token,expiry,item_id))
        return token
    def release_publication_lock(self,item_id,token):
        with self.transaction() as c: c.execute("UPDATE items SET publication_lock_token=NULL,publication_lock_expires_at=NULL WHERE id=? AND publication_lock_token=?",(item_id,token))
    ORDER_TRANSITIONS={
        "checkout_pending":{"authorization_pending","cancelled","expired","unavailable","manual_resolution"},
        "authorization_pending":{"authorized","cancelled","expired","manual_resolution"},
        "authorized":{"ebay_withdrawal_pending","capture_pending","authorization_cancel_pending","manual_resolution","unavailable"},
        "ebay_withdrawal_pending":{"capture_pending","authorization_cancel_pending","manual_resolution"},
        "capture_pending":{"paid","authorization_cancel_pending","manual_resolution"},
        "paid":{"sold","manual_resolution"}, "authorization_cancel_pending":{"cancelled","unavailable","manual_resolution"},
        "sold":{"manual_resolution"}, "cancelled":set(), "expired":set(), "unavailable":{"manual_resolution"}, "manual_resolution":set()
    }
    def create_storefront_order(self, sku, duration_seconds=1800, provider_identity="legacy_unknown"):
        stamp=now(); expiry=(datetime.now(timezone.utc)+timedelta(seconds=duration_seconds)).isoformat()
        with self.transaction(immediate=True) as c:
            item=c.execute("SELECT * FROM items WHERE sku=?",(sku,)).fetchone()
            if not item or item["status"]!="Active" or not item["storefront_visible"] or item["quantity"]<1: raise ValueError("Item unavailable")
            reservation=c.execute("SELECT r.order_id FROM item_reservations r JOIN storefront_orders o ON o.id=r.order_id WHERE r.item_id=? AND r.state='open' AND (r.expires_at>? OR "+self._protected_reservation_sql("o")+")",(item["id"],stamp)).fetchone()
            if reservation: raise ValueError("Item reserved")
            c.execute("UPDATE item_reservations SET state='expired',close_reason='expired',updated_at=? WHERE item_id=? AND state='open' AND expires_at<=?",(stamp,item["id"],stamp))
            public_id=secrets.token_urlsafe(24)
            cur=c.execute("INSERT INTO storefront_orders(public_id,item_id,price_snapshot,currency_snapshot,state,stripe_provider_identity,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(public_id,item["id"],item["price"],item["currency"],"checkout_pending",provider_identity,stamp,stamp))
            rid=c.execute("INSERT INTO item_reservations(item_id,order_id,state,expires_at,created_at,updated_at) VALUES(?,?,'open',?,?,?)",(item["id"],cur.lastrowid,expiry,stamp,stamp)).lastrowid
            c.execute("UPDATE storefront_orders SET reservation_id=? WHERE id=?",(rid,cur.lastrowid))
            self._event(c,cur.lastrowid,None,"checkout_pending","shopper","order_created")
            return self.get_order(public_id,c)
    def get_order(self, public_id, con=None):
        own=con is None; con=con or self.connect(); row=con.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone(); result=dict(row) if row else None
        if own: con.close()
        return result
    def public_order_status(self, public_id):
        """Allowlisted shopper projection; never expose provider, customer or internal data."""
        with self.connect() as con:
            row=con.execute("""SELECT o.public_id,o.state,o.payment_state,o.fulfillment_state,
                o.refund_state,o.created_at,o.tracking_carrier,o.tracking_number,o.tracking_url,
                i.sku,i.title,i.condition,i.brand,i.size,i.color
                FROM storefront_orders o JOIN items i ON i.id=o.item_id WHERE o.public_id=?""",(public_id,)).fetchone()
            if not row: return None
            result={key:row[key] for key in ("public_id","state","payment_state","fulfillment_state",
                "refund_state","created_at","sku","title","condition","brand","size","color")}
            result["payment_outcome"]=("paid" if row["payment_state"]=="captured" else
                "not_completed" if row["payment_state"] in ("cancelled","unpaid") else "processing")
            if row["fulfillment_state"] in ("shipped","delivered"):
                result.update(tracking_carrier=row["tracking_carrier"],tracking_number=row["tracking_number"],tracking_url=row["tracking_url"])
            return result
    def find_order_by_stripe(self, session_id=None, payment_intent_id=None):
        with self.connect() as con:
            row=con.execute("SELECT * FROM storefront_orders WHERE stripe_checkout_session_id=? OR stripe_payment_intent_id=?",(session_id or "",payment_intent_id or "")).fetchone()
            return dict(row) if row else None
    def _event(self,c,order_id,old,new,source,category):
        if old!=new or category:
            c.execute("INSERT INTO storefront_order_events(order_id,from_state,to_state,source,category,created_at) VALUES(?,?,?,?,?,?)",(order_id,old,new,source,category,now()))

    @staticmethod
    def _clean_metadata(metadata):
        """Serialize caller-sanitized audit data, rejecting non-JSON values."""
        if metadata is None: return None
        return json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def append_order_event(self, public_id, category, source="seller", actor=None,
                           lifecycle=None, old_value=None, new_value=None,
                           metadata=None, dedupe_key=None, con=None):
        """Append an audit event; a dedupe key makes retries harmless."""
        own=con is None
        if own: con=self.connect(); con.execute("BEGIN IMMEDIATE")
        try:
            order=con.execute("SELECT id,state FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order: raise ValueError("Order not found")
            con.execute("""INSERT OR IGNORE INTO storefront_order_events
                (order_id,from_state,to_state,source,category,actor,lifecycle,old_value,new_value,metadata_json,dedupe_key,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (order["id"],order["state"],order["state"],source,category,actor,lifecycle,
                 old_value,new_value,self._clean_metadata(metadata),dedupe_key,now()))
            row=con.execute("SELECT * FROM storefront_order_events WHERE order_id=? AND (? IS NOT NULL AND dedupe_key=?) ORDER BY id DESC LIMIT 1",
                            (order["id"],dedupe_key,dedupe_key)).fetchone() if dedupe_key else con.execute("SELECT * FROM storefront_order_events WHERE id=last_insert_rowid()").fetchone()
            if own: con.commit()
            return dict(row) if row else None
        except Exception:
            if own: con.rollback()
            raise
        finally:
            if own: con.close()

    def _cas_order_field(self, public_id, field, expected, value, *, actor=None,
                         metadata=None, dedupe_key=None, timestamps=None):
        allowed={"fulfillment_state","refund_state"}
        if field not in allowed: raise ValueError("Unsupported order lifecycle")
        valid={"fulfillment_state":{"not_applicable","unfulfilled","packing","shipped","delivered"},
               "refund_state":{"none","pending","refunded","manual_resolution"}}[field]
        if expected not in valid or value not in valid: raise ValueError("Invalid lifecycle state")
        with self.transaction(immediate=True) as c:
            order=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order: raise ValueError("Order not found")
            if order[field]!=expected: return None
            stamp=now(); assignments=[f"{field}=?","updated_at=?"]; values=[value,stamp]
            for column,when in (timestamps or {}).items():
                if column not in {"shipped_at","delivered_at","refunded_at"}: raise ValueError("Unsupported timestamp")
                assignments.append(f"{column}=COALESCE({column},?)"); values.append(when or stamp)
            changed=c.execute(f"UPDATE storefront_orders SET {','.join(assignments)} WHERE id=? AND {field}=?",
                              (*values,order["id"],expected)).rowcount
            if not changed: return None
            self.append_order_event(public_id,field.removesuffix("_state")+"_changed",actor=actor,
                lifecycle=field,old_value=expected,new_value=value,metadata=metadata,dedupe_key=dedupe_key,con=c)
            return self.get_order(public_id,c)

    def compare_and_set_fulfillment(self, public_id, expected, value, actor=None, metadata=None, dedupe_key=None):
        timestamps={"shipped_at":None} if value=="shipped" else ({"delivered_at":None} if value=="delivered" else {})
        return self._cas_order_field(public_id,"fulfillment_state",expected,value,actor=actor,metadata=metadata,dedupe_key=dedupe_key,timestamps=timestamps)

    def compare_and_set_refund(self, public_id, expected, value, actor=None, metadata=None,
                               dedupe_key=None, stripe_refund_id=None,
                               captured_amount=None, captured_currency=None):
        with self.transaction(immediate=True) as c:
            order=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order: raise ValueError("Order not found")
            valid={"none","pending","refunded","manual_resolution"}
            if expected not in valid or value not in valid: raise ValueError("Invalid lifecycle state")
            if order["refund_state"]!=expected: return None
            stamp=now(); refunded=stamp if value=="refunded" else None
            changed=c.execute("""UPDATE storefront_orders SET refund_state=?,stripe_refund_id=COALESCE(?,stripe_refund_id),
                captured_amount=COALESCE(?,captured_amount),captured_currency=COALESCE(?,captured_currency),
                refunded_at=CASE WHEN ?='refunded' THEN COALESCE(refunded_at,?) ELSE refunded_at END,updated_at=?
                WHERE id=? AND refund_state=?""",(value,stripe_refund_id,captured_amount,captured_currency,value,refunded,stamp,order["id"],expected)).rowcount
            if not changed: return None
            self.append_order_event(public_id,"refund_changed",actor=actor,lifecycle="refund_state",old_value=expected,
                new_value=value,metadata=metadata,dedupe_key=dedupe_key,con=c)
            return self.get_order(public_id,c)

    def record_provider_capture(self, public_id, amount, currency):
        """Store the amount confirmed by the payment provider without inference."""
        if not isinstance(amount,int) or isinstance(amount,bool) or amount < 0 or not currency: raise ValueError("Captured amount and currency are required")
        with self.transaction(immediate=True) as c:
            changed=c.execute("""UPDATE storefront_orders SET
                captured_amount=COALESCE(captured_amount,?),
                captured_currency=COALESCE(captured_currency,?),provider_revision=provider_revision+1,updated_at=?
                WHERE public_id=? AND (captured_amount IS NULL OR (captured_amount=? AND captured_currency=?))""",
                (amount,currency.upper(),now(),public_id,amount,currency.upper())).rowcount
            if not changed:
                if not c.execute("SELECT 1 FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone(): raise ValueError("Order not found")
                raise ValueError("Conflicting provider capture")
            return self.get_order(public_id,c)

    def add_tracking_revision(self, public_id, request_key, carrier=None, tracking_number=None,
                              tracking_url=None, actor=None):
        if not request_key or not any((carrier,tracking_number,tracking_url)): raise ValueError("Tracking and request key are required")
        with self.transaction(immediate=True) as c:
            order=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order: raise ValueError("Order not found")
            existing=c.execute("SELECT * FROM storefront_tracking_revisions WHERE request_key=?",(request_key,)).fetchone()
            if existing:
                if existing["order_id"]!=order["id"]: raise ValueError("Tracking request key already used")
                return dict(existing)
            stamp=now(); cur=c.execute("""INSERT INTO storefront_tracking_revisions
                (order_id,request_key,carrier,tracking_number,tracking_url,previous_carrier,previous_tracking_number,previous_tracking_url,actor,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",(order["id"],request_key,carrier,tracking_number,tracking_url,
                order["tracking_carrier"],order["tracking_number"],order["tracking_url"],actor,stamp))
            c.execute("UPDATE storefront_orders SET tracking_carrier=?,tracking_number=?,tracking_url=?,updated_at=? WHERE id=?",
                      (carrier,tracking_number,tracking_url,stamp,order["id"]))
            self.append_order_event(public_id,"tracking_revised",actor=actor,lifecycle="tracking",metadata={"revision_id":cur.lastrowid},dedupe_key="tracking:"+request_key,con=c)
            return dict(c.execute("SELECT * FROM storefront_tracking_revisions WHERE id=?",(cur.lastrowid,)).fetchone())

    def append_order_note(self, public_id, note, actor=None):
        if not note or not note.strip(): raise ValueError("Note is required")
        with self.transaction(immediate=True) as c:
            order=c.execute("SELECT id FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order: raise ValueError("Order not found")
            cur=c.execute("INSERT INTO storefront_order_notes(order_id,note,actor,created_at) VALUES(?,?,?,?)",(order["id"],note.strip(),actor,now()))
            return dict(c.execute("SELECT * FROM storefront_order_notes WHERE id=?",(cur.lastrowid,)).fetchone())

    def create_attention_case(self, public_id, kind, summary, dedupe_key=None, actor=None):
        with self.transaction(immediate=True) as c:
            order=c.execute("SELECT id FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order: raise ValueError("Order not found")
            stamp=now(); c.execute("INSERT OR IGNORE INTO storefront_attention_cases(order_id,kind,summary,dedupe_key,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (order["id"],kind,summary,dedupe_key,actor,stamp,stamp))
            row=c.execute("SELECT * FROM storefront_attention_cases WHERE order_id=? AND ((? IS NOT NULL AND dedupe_key=?) OR (? IS NULL AND id=last_insert_rowid())) ORDER BY id DESC LIMIT 1",
                          (order["id"],dedupe_key,dedupe_key,dedupe_key)).fetchone()
            return dict(row)

    def set_attention_case_state(self, case_id, expected, value, actor=None):
        if expected not in {"open","acknowledged","resolved"} or value not in {"open","acknowledged","resolved"}: raise ValueError("Invalid attention state")
        with self.transaction(immediate=True) as c:
            stamp=now(); column="acknowledged_at" if value=="acknowledged" else "resolved_at" if value=="resolved" else None
            extra=f",{column}=COALESCE({column},?)" if column else ""; args=[value,actor,stamp]
            if column: args.append(stamp)
            args.extend((case_id,expected))
            changed=c.execute(f"UPDATE storefront_attention_cases SET state=?,actor=?,updated_at=?{extra} WHERE id=? AND state=?",args).rowcount
            row=c.execute("SELECT * FROM storefront_attention_cases WHERE id=?",(case_id,)).fetchone()
            return dict(row) if changed and row else None

    def attention_cases(self, state=None, limit=100):
        if state is not None and state not in {"open","acknowledged","resolved"}: raise ValueError("Invalid attention state")
        with self.connect() as c:
            where=" WHERE a.state=?" if state else " WHERE a.state!='resolved'"
            args=[state] if state else []
            return [dict(row) for row in c.execute("""SELECT a.*,o.public_id FROM storefront_attention_cases a
                JOIN storefront_orders o ON o.id=a.order_id"""+where+" ORDER BY a.updated_at,a.id LIMIT ?",(*args,limit))]
    def attach_checkout(self, public_id, session_id, payment_intent_id=None, source="shopper"):
        with self.transaction(immediate=True) as c:
            before=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not before: raise ValueError("Order not found")
            if ((before["stripe_checkout_session_id"] and session_id and before["stripe_checkout_session_id"]!=session_id) or
                (before["stripe_payment_intent_id"] and payment_intent_id and before["stripe_payment_intent_id"]!=payment_intent_id)):
                c.execute("UPDATE storefront_orders SET state='manual_resolution',conflict_details='Conflicting Stripe identifiers',updated_at=? WHERE id=?",(now(),before["id"]))
                self._event(c,before["id"],before["state"],"manual_resolution",source,"provider_identifier_conflict")
                return self.get_order(public_id,c)
            changed=(not before["stripe_checkout_session_id"] and session_id) or (not before["stripe_payment_intent_id"] and payment_intent_id)
            c.execute("UPDATE storefront_orders SET stripe_checkout_session_id=COALESCE(stripe_checkout_session_id,?),stripe_payment_intent_id=COALESCE(stripe_payment_intent_id,?),provider_revision=provider_revision+?,state=CASE WHEN state='checkout_pending' THEN 'authorization_pending' ELSE state END,updated_at=? WHERE public_id=?",(session_id,payment_intent_id,1 if changed else 0,now(),public_id))
            after=self.get_order(public_id,c); self._event(c,before["id"],before["state"],after["state"],source,"checkout_attached")
            return after
    def attach_payment_intent(self,public_id,payment_intent_id,fulfillment=None):
        fulfillment={k:v for k,v in (fulfillment or {}).items() if k in {"customer_email","shipping_name","shipping_line1","shipping_line2","shipping_city","shipping_state","shipping_postal_code","shipping_country","shipping_phone"}}
        with self.transaction(immediate=True) as c:
            row=c.execute("SELECT id,stripe_payment_intent_id,state FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not row: raise ValueError("Order not found")
            if row["stripe_payment_intent_id"] and row["stripe_payment_intent_id"]!=payment_intent_id: raise ValueError("Conflicting PaymentIntent")
            # Webhook delivery order is unspecified. Fill blanks, but never erase
            # already-known customer/fulfilment data with absent values.
            values={"stripe_payment_intent_id":payment_intent_id,**{k:v for k,v in fulfillment.items() if v is not None},"provider_revision":c.execute("SELECT provider_revision FROM storefront_orders WHERE id=?",(row["id"],)).fetchone()[0]+(1 if not row["stripe_payment_intent_id"] else 0),"updated_at":now()}
            c.execute("UPDATE storefront_orders SET "+",".join(f"{k}=?" for k in values)+" WHERE id=?",[*values.values(),row["id"]])
            self._event(c,row["id"],row["state"],row["state"],"stripe_webhook","payment_intent_attached")
            return self.get_order(public_id,c)
    def transition_order(self, public_id, state, payment_state=None, error=None, conflict=None, source="maintenance", category="state_transition"):
        with self.transaction(immediate=True) as c:
            row=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not row: raise ValueError("Order not found")
            if state!=row["state"] and state not in self.ORDER_TRANSITIONS.get(row["state"],set()): raise ValueError(f"Invalid order transition {row['state']} to {state}")
            c.execute("UPDATE storefront_orders SET state=?,payment_state=COALESCE(?,payment_state),operation_error=?,conflict_details=COALESCE(?,conflict_details),updated_at=? WHERE id=?",(state,payment_state,error,conflict,now(),row["id"]))
            self._event(c,row["id"],row["state"],state,source,category)
            return self.get_order(public_id,c)
    def release_order(self, public_id, state, reason, source="maintenance"):
        with self.transaction(immediate=True) as c:
            row=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not row: return None
            if state!=row["state"] and state not in self.ORDER_TRANSITIONS.get(row["state"],set()): raise ValueError("Invalid order transition")
            c.execute("UPDATE storefront_orders SET state=?,updated_at=? WHERE id=?",(state,now(),row["id"]))
            c.execute("UPDATE item_reservations SET state=?,close_reason=?,updated_at=? WHERE order_id=? AND state='open'",("expired" if state=="expired" else "released",reason,now(),row["id"]))
            self._event(c,row["id"],row["state"],state,source,"reservation_released")
            return self.get_order(public_id,c)
    def expire_reservations(self, limit=100):
        with self.transaction(immediate=True) as c:
            stamp=now(); rows=c.execute("SELECT r.order_id FROM item_reservations r JOIN storefront_orders o ON o.id=r.order_id WHERE r.state='open' AND r.expires_at<=? AND NOT ("+self._protected_reservation_sql("o")+") LIMIT ?",(stamp,limit)).fetchall()
            for row in rows:
                before=c.execute("SELECT state FROM storefront_orders WHERE id=?",(row["order_id"],)).fetchone()[0]
                c.execute("UPDATE item_reservations SET state='expired',close_reason='expired',updated_at=? WHERE order_id=?",(now(),row["order_id"]))
                c.execute("UPDATE storefront_orders SET state='expired',updated_at=? WHERE id=? AND state IN ('checkout_pending','authorization_pending')",(now(),row["order_id"]))
                order=c.execute("SELECT state FROM storefront_orders WHERE id=?",(row["order_id"],)).fetchone()
                self._event(c,row["order_id"],before,order["state"],"maintenance","reservation_expired")
            return len(rows)

    @staticmethod
    def _protected_reservation_sql(alias="o"):
        return f"""({alias}.state IN ('authorized','ebay_withdrawal_pending','capture_pending','paid','authorization_cancel_pending','manual_resolution')
          OR {alias}.payment_state IN ('authorized','captured')
          OR EXISTS(SELECT 1 FROM storefront_operations po WHERE po.order_id={alias}.id AND po.state IN ('running','manual_resolution'))
          OR EXISTS(SELECT 1 FROM storefront_resolution_actions ra WHERE ra.order_id={alias}.id AND ra.state IN ('pending','running','manual_resolution'))
          OR EXISTS(SELECT 1 FROM storefront_resolution_reviews rr WHERE rr.order_id={alias}.id AND rr.id=(SELECT id FROM storefront_resolution_reviews WHERE order_id={alias}.id ORDER BY created_at DESC,id DESC LIMIT 1) AND (rr.state='ambiguous' OR rr.stripe_classification='ambiguous' OR rr.ebay_classification IN ('ambiguous','missing'))))"""
    def claim_webhook(self,event_id,event_type,lease_seconds=120):
        token=secrets.token_urlsafe(24); stamp=now(); expiry=(datetime.now(timezone.utc)+timedelta(seconds=lease_seconds)).isoformat()
        with self.transaction(immediate=True) as c:
            try:
                c.execute("INSERT INTO stripe_webhook_events(event_id,event_type,state,claim_token,lease_expires_at,created_at,updated_at) VALUES(?,?,'processing',?,?,?,?)",(event_id,event_type,token,expiry,stamp,stamp))
                return WebhookClaim(disposition="claimed",claim_token=token)
            except sqlite3.IntegrityError:
                row=c.execute("SELECT * FROM stripe_webhook_events WHERE event_id=?",(event_id,)).fetchone()
                if row["state"]=="processed": return WebhookClaim(disposition="already_complete",claim_token=None)
                if row["state"]=="processing" and row["lease_expires_at"] and row["lease_expires_at"]>stamp: return WebhookClaim(disposition="busy",claim_token=None)
                c.execute("UPDATE stripe_webhook_events SET state='processing',attempts=attempts+1,claim_token=?,lease_expires_at=?,updated_at=? WHERE event_id=?",(token,expiry,stamp,event_id))
                return WebhookClaim(disposition="claimed",claim_token=token)
    def correlate_webhook(self,event_id,correlation_state,session_id=None,payment_intent_id=None,reference=None,matched_order_id=None):
        if correlation_state not in ("irrelevant","matched","unmatched_retryable","unmatched_attention"): raise ValueError("Invalid correlation state")
        with self.transaction(immediate=True) as c:
            c.execute("UPDATE stripe_webhook_events SET checkout_session_id=?,payment_intent_id=?,order_reference=?,correlation_state=?,matched_order_id=?,last_attempt_at=?,next_attempt_at=CASE WHEN ?='unmatched_retryable' THEN datetime('now','+5 minutes') ELSE NULL END,updated_at=? WHERE event_id=?",(session_id,payment_intent_id,reference,correlation_state,matched_order_id,now(),correlation_state,now(),event_id))
    def finish_webhook(self,event_id,error=None,claim_token=None):
        with self.transaction(immediate=True) as c:
            where="event_id=?"; args=[event_id]
            if claim_token: where+=" AND claim_token=?"; args.append(claim_token)
            c.execute(f"UPDATE stripe_webhook_events SET state=?,error=?,claim_token=NULL,lease_expires_at=NULL,updated_at=? WHERE {where}",( "failed" if error else "processed",error,now(),*args))

    def claim_operation(self, public_id, operation_type, lease_seconds=120):
        """Atomically recover/create and claim one durable per-order operation."""
        token=secrets.token_urlsafe(24); stamp=now(); expiry=(datetime.now(timezone.utc)+timedelta(seconds=lease_seconds)).isoformat()
        with self.transaction(immediate=True) as c:
            order=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order: return {"disposition":"superseded","token":None,"order":None}
            if order["state"]=="manual_resolution": return {"disposition":"manual_resolution","token":None,"order":dict(order)}
            row=c.execute("SELECT * FROM storefront_operations WHERE order_id=? AND operation_type=?",(order["id"],operation_type)).fetchone()
            if row and row["state"]=="complete": return {"disposition":"already_complete","token":None,"order":dict(order),"operation":dict(row)}
            if row and row["state"]=="manual_resolution": return {"disposition":"manual_resolution","token":None,"order":dict(order),"operation":dict(row)}
            if row and row["state"]=="running" and row["lease_expires_at"] and row["lease_expires_at"]>stamp:
                return {"disposition":"busy","token":None,"order":dict(order),"operation":dict(row)}
            key={"ebay_withdrawal":"withdraw","stripe_capture":"capture","stripe_cancel":"cancel",
                 "stripe_full_refund":"refund"}[operation_type]+":"+public_id
            if operation_type=="stripe_full_refund": key=f"refund:{public_id}:full:v1"
            if row:
                c.execute("UPDATE storefront_operations SET state='running',attempts=attempts+1,claim_token=?,lease_expires_at=?,error=NULL,updated_at=? WHERE id=?",(token,expiry,stamp,row["id"]))
            else:
                c.execute("INSERT INTO storefront_operations(order_id,operation_type,state,attempts,idempotency_key,claim_token,lease_expires_at,created_at,updated_at) VALUES(?,?,'running',1,?,?,?,?,?)",(order["id"],operation_type,key,token,expiry,stamp,stamp))
            op=c.execute("SELECT * FROM storefront_operations WHERE order_id=? AND operation_type=?",(order["id"],operation_type)).fetchone()
            return {"disposition":"claimed","token":token,"order":dict(order),"operation":dict(op)}

    def renew_operation(self, public_id, operation_type, token, lease_seconds=120):
        expiry=(datetime.now(timezone.utc)+timedelta(seconds=lease_seconds)).isoformat()
        with self.transaction(immediate=True) as c:
            changed=c.execute("UPDATE storefront_operations SET lease_expires_at=?,updated_at=? WHERE order_id=(SELECT id FROM storefront_orders WHERE public_id=?) AND operation_type=? AND state='running' AND claim_token=?",(expiry,now(),public_id,operation_type,token)).rowcount
            return Disposition("claimed" if changed else "superseded")

    def complete_operation(self, public_id, operation_type, token, result=None, diagnostic=None, manual=False):
        with self.transaction(immediate=True) as c:
            changed=c.execute("UPDATE storefront_operations SET state=?,result=?,diagnostic=?,error=?,claim_token=NULL,lease_expires_at=NULL,updated_at=? WHERE order_id=(SELECT id FROM storefront_orders WHERE public_id=?) AND operation_type=? AND state='running' AND claim_token=?",("manual_resolution" if manual else "complete",result,diagnostic,diagnostic,now(),public_id,operation_type,token)).rowcount
            return Disposition("manual_resolution" if manual and changed else "already_complete" if changed else "superseded")
    def fail_operation(self, public_id, operation_type, token, diagnostic):
        """Release a confirmed failure for retry while retaining its audit row."""
        with self.transaction(immediate=True) as c:
            changed=c.execute("UPDATE storefront_operations SET state='failed',diagnostic=?,error=?,claim_token=NULL,lease_expires_at=NULL,updated_at=? WHERE order_id=(SELECT id FROM storefront_orders WHERE public_id=?) AND operation_type=? AND state='running' AND claim_token=?",(diagnostic,diagnostic,now(),public_id,operation_type,token)).rowcount
            return Disposition("failed" if changed else "superseded")
    def reconcile_operation_failure(self, public_id, operation_type, diagnostic):
        """Record a provider-confirmed reconciliation failure without a lease."""
        with self.transaction(immediate=True) as c:
            changed=c.execute("UPDATE storefront_operations SET state='failed',diagnostic=?,error=?,claim_token=NULL,lease_expires_at=NULL,updated_at=? WHERE order_id=(SELECT id FROM storefront_orders WHERE public_id=?) AND operation_type=?",(diagnostic,diagnostic,now(),public_id,operation_type)).rowcount
            return Disposition("failed" if changed else "superseded")
    def finalize_storefront_sale(self,public_id,source="stripe_webhook"):
        with self.transaction(immediate=True) as c:
            order=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order: raise ValueError("Order not found")
            if order["state"]=="sold": return self.get_order(public_id,c)
            if order["state"] not in ("paid","capture_pending"): raise ValueError("Order is not captured")
            item=c.execute("SELECT quantity,status FROM items WHERE id=?",(order["item_id"],)).fetchone()
            if item["quantity"]<1 or item["status"]=="Sold":
                c.execute("UPDATE storefront_orders SET state='manual_resolution',conflict_details='Inventory sold by another channel',updated_at=? WHERE id=?",(now(),order["id"])); return self.get_order(public_id,c)
            c.execute("UPDATE items SET quantity=0,status='Sold',storefront_visible=0,updated_at=? WHERE id=?",(now(),order["item_id"]))
            c.execute("""UPDATE storefront_orders SET state='sold',payment_state='captured',
                fulfillment_state=CASE WHEN fulfillment_state='not_applicable' THEN 'unfulfilled' ELSE fulfillment_state END,
                updated_at=? WHERE id=?""",(now(),order["id"]))
            c.execute("UPDATE item_reservations SET state='closed',close_reason='sold',updated_at=? WHERE order_id=?",(now(),order["id"]))
            self._event(c,order["id"],order["state"],"sold",source,"inventory_sold")
            return self.get_order(public_id,c)

    def seller_orders(self,filters,page=1,per_page=25):
        clauses=[]; args=[]
        mapping={"state":"o.state","payment_state":"o.payment_state","fulfillment_state":"o.fulfillment_state",
                 "refund_state":"o.refund_state","sku":"i.sku","order_id":"o.public_id"}
        for key,column in mapping.items():
            if filters.get(key): clauses.append(f"{column}=?"); args.append(filters[key])
        if filters.get("date"):
            clauses.append("substr(o.created_at,1,10)=?"); args.append(filters["date"])
        if filters.get("attention") == "open":
            clauses.append("(o.state='manual_resolution' OR o.refund_state='manual_resolution' OR EXISTS(SELECT 1 FROM storefront_attention_cases ac WHERE ac.order_id=o.id AND ac.state!='resolved') OR EXISTS(SELECT 1 FROM storefront_operations ao WHERE ao.order_id=o.id AND ao.state IN ('failed','manual_resolution')))")
        elif filters.get("attention") == "none":
            clauses.append("NOT (o.state='manual_resolution' OR o.refund_state='manual_resolution' OR EXISTS(SELECT 1 FROM storefront_attention_cases ac WHERE ac.order_id=o.id AND ac.state!='resolved') OR EXISTS(SELECT 1 FROM storefront_operations ao WHERE ao.order_id=o.id AND ao.state IN ('failed','manual_resolution')))")
        where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
        with self.connect() as c:
            total=c.execute("SELECT COUNT(*) FROM storefront_orders o JOIN items i ON i.id=o.item_id"+where,args).fetchone()[0]
            rows=c.execute("""SELECT o.*,i.sku,i.title,r.state reservation_state,r.expires_at,
                EXISTS(SELECT 1 FROM storefront_operations op WHERE op.order_id=o.id AND op.state IN ('failed','manual_resolution')) operation_attention,
                EXISTS(SELECT 1 FROM storefront_attention_cases ac WHERE ac.order_id=o.id AND ac.state!='resolved') open_attention
                FROM storefront_orders o JOIN items i ON i.id=o.item_id LEFT JOIN item_reservations r ON r.order_id=o.id"""+where+" ORDER BY o.created_at DESC,o.id DESC LIMIT ? OFFSET ?",[*args,per_page,(page-1)*per_page]).fetchall()
            return [dict(x) for x in rows],total
    def seller_order_summary(self):
        """Return dashboard counts in one indexed aggregate query."""
        with self.connect() as c:
            row=c.execute("""SELECT COUNT(*) total,
                SUM(fulfillment_state='unfulfilled') unfulfilled,
                SUM(fulfillment_state='packing') packing,
                SUM(fulfillment_state='shipped') shipped,
                SUM(fulfillment_state='delivered') delivered,
                SUM(refund_state='pending') refunds_pending,
                SUM(refund_state='manual_resolution') refunds_manual_resolution,
                (SELECT COUNT(*) FROM storefront_orders oa WHERE oa.state='manual_resolution' OR oa.refund_state='manual_resolution' OR EXISTS(SELECT 1 FROM storefront_attention_cases ac WHERE ac.order_id=oa.id AND ac.state!='resolved')) open_attention
                FROM storefront_orders""").fetchone()
            return {key:(row[key] or 0) for key in row.keys()}
    def seller_order(self,public_id):
        with self.connect() as c:
            row=c.execute("""SELECT o.*,i.sku,i.title,i.description,i.condition,i.brand,i.size,i.color,
                i.ebay_listing_id,i.ebay_offer_id,i.ebay_publication_state,r.state reservation_state,r.expires_at,r.close_reason
                FROM storefront_orders o JOIN items i ON i.id=o.item_id LEFT JOIN item_reservations r ON r.order_id=o.id WHERE o.public_id=?""",(public_id,)).fetchone()
            if not row:return None
            result=dict(row); result["operations"]=[{k:x[k] for k in ("operation_type","state","attempts","created_at","updated_at")} for x in c.execute("SELECT * FROM storefront_operations WHERE order_id=? ORDER BY created_at,id",(row["id"],))]
            result["reviews"]=[dict(x) for x in c.execute("SELECT * FROM storefront_resolution_reviews WHERE order_id=? ORDER BY created_at DESC,id DESC",(row["id"],))]
            result["resolution_actions"]=[dict(x) for x in c.execute("SELECT * FROM storefront_resolution_actions WHERE order_id=? ORDER BY created_at,id",(row["id"],))]
            protected=row["state"] in ("authorized","ebay_withdrawal_pending","capture_pending","paid","authorization_cancel_pending","manual_resolution") or row["payment_state"] in ("authorized","captured")
            result["reservation_hold_reason"]="cancellation_verification" if row["state"]=="authorization_cancel_pending" else "provider_operation_review" if row["state"]=="manual_resolution" else "authorization_review" if protected else "normal"
            result["events"]=[dict(x) for x in c.execute("SELECT * FROM storefront_order_events WHERE order_id=? ORDER BY created_at,id",(row["id"],))]
            result["notes"]=[dict(x) for x in c.execute("SELECT * FROM storefront_order_notes WHERE order_id=? ORDER BY created_at,id",(row["id"],))]
            result["tracking_revisions"]=[dict(x) for x in c.execute("SELECT * FROM storefront_tracking_revisions WHERE order_id=? ORDER BY created_at,id",(row["id"],))]
            result["attention_cases"]=[dict(x) for x in c.execute("SELECT * FROM storefront_attention_cases WHERE order_id=? ORDER BY created_at,id",(row["id"],))]
            for key,prefix in (("stripe_checkout_session_id","cs_"),("stripe_payment_intent_id","pi_"),("stripe_refund_id","re_")):
                value=result.get(key) or ""
                result["safe_"+key]=value if value.startswith(prefix) and all(ch.isalnum() or ch=="_" for ch in value) else None
            return result
    def process_order_line(self,order_id,line_id,sku,listing_id,quantity,payment,fulfillment,raw):
        """Deduplicate an order line and atomically reduce immediately-visible stock."""
        with self.transaction(immediate=True) as c:
            if c.execute("SELECT 1 FROM ebay_order_lines WHERE order_id=? AND line_item_id=?",(order_id,line_id)).fetchone(): return False
            c.execute("INSERT INTO ebay_order_lines(order_id,line_item_id,sku,listing_id,quantity,payment_state,fulfillment_state,raw_payload,processed_at) VALUES(?,?,?,?,?,?,?,?,?)",(order_id,line_id,sku,listing_id,quantity,payment,fulfillment,json.dumps(raw),now()))
            if payment not in ("PAID","FULLY_REFUNDED"):
                return True
            row=c.execute("SELECT * FROM items WHERE ebay_sku=? OR ebay_listing_id=?",(sku,listing_id)).fetchone()
            if row and payment=="PAID":
                remaining=max(0,row["quantity"]-quantity); status="Sold" if remaining==0 else row["status"]
                c.execute("UPDATE items SET quantity=?,status=?,storefront_visible=?,ebay_sync_state='sold',updated_at=? WHERE id=?",(remaining,status,0 if remaining==0 else row["storefront_visible"],now(),row["id"]))
                # eBay cannot share this SQLite transaction with Stripe. Preserve
                # the reservation and durably flag any possibly-authorized sale;
                # a worker must cancel it outside this transaction.
                orders=c.execute("SELECT o.* FROM storefront_orders o JOIN item_reservations r ON r.order_id=o.id WHERE o.item_id=? AND r.state='open' AND o.state IN ('authorization_pending','authorized','ebay_withdrawal_pending','capture_pending','paid')",(row["id"],)).fetchall()
                for order in orders:
                    target="manual_resolution" if order["state"] in ("capture_pending","paid") else "authorization_cancel_pending"
                    c.execute("UPDATE storefront_orders SET state=?,conflict_details=?,updated_at=? WHERE id=?",(target,f"eBay order {order_id} line {line_id} owns inventory",now(),order["id"]))
                    c.execute("INSERT OR IGNORE INTO storefront_operations(order_id,operation_type,state,idempotency_key,created_at,updated_at) VALUES(?, 'stripe_cancel','pending',?,?,?)",(order["id"],f"cancel:{order['public_id']}",now(),now()))
            return True
