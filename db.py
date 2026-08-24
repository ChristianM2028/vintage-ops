import json, os, sqlite3, secrets
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

STATUSES = ("Draft", "Active", "Sold", "Delisted")
TRANSITIONS = {
    "Draft": {"Active", "Sold", "Delisted"}, "Active": {"Sold", "Delisted"},
    "Delisted": {"Active", "Sold"}, "Sold": set(),
}
def now(): return datetime.now(timezone.utc).isoformat()

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
            item=self._public_item(row,con)
            reserved=con.execute("SELECT 1 FROM item_reservations WHERE item_id=? AND state='open' AND expires_at>?",(row["id"],now())).fetchone()
            item["availability"]="reserved" if reserved else ("available" if row["storefront_visible"] and row["status"]=="Active" and row["quantity"]>0 else "sold" if row["status"]=="Sold" or row["quantity"]<=0 else "unavailable")
            return item
    def storefront_catalog(self, search=None, brand=None, size=None, min_price=None, max_price=None, sort="newest"):
        """Query visible, active inventory and return only storefront-safe fields."""
        clauses=["storefront_visible=1","status='Active'","quantity>0","NOT EXISTS (SELECT 1 FROM item_reservations r WHERE r.item_id=items.id AND r.state='open' AND r.expires_at>?)"]; args=[now()]
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
            return [self._public_item(row,con) for row in rows]
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
    def create_storefront_order(self, sku, duration_seconds=1800):
        stamp=now(); expiry=(datetime.now(timezone.utc)+timedelta(seconds=duration_seconds)).isoformat()
        with self.transaction(immediate=True) as c:
            item=c.execute("SELECT * FROM items WHERE sku=?",(sku,)).fetchone()
            if not item or item["status"]!="Active" or not item["storefront_visible"] or item["quantity"]<1: raise ValueError("Item unavailable")
            reservation=c.execute("SELECT order_id FROM item_reservations WHERE item_id=? AND state='open' AND expires_at>?",(item["id"],stamp)).fetchone()
            if reservation: raise ValueError("Item reserved")
            c.execute("UPDATE item_reservations SET state='expired',close_reason='expired',updated_at=? WHERE item_id=? AND state='open' AND expires_at<=?",(stamp,item["id"],stamp))
            public_id=secrets.token_urlsafe(24)
            cur=c.execute("INSERT INTO storefront_orders(public_id,item_id,price_snapshot,currency_snapshot,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(public_id,item["id"],item["price"],item["currency"],"checkout_pending",stamp,stamp))
            rid=c.execute("INSERT INTO item_reservations(item_id,order_id,state,expires_at,created_at,updated_at) VALUES(?,?,'open',?,?,?)",(item["id"],cur.lastrowid,expiry,stamp,stamp)).lastrowid
            c.execute("UPDATE storefront_orders SET reservation_id=? WHERE id=?",(rid,cur.lastrowid))
            return self.get_order(public_id,c)
    def get_order(self, public_id, con=None):
        own=con is None; con=con or self.connect(); row=con.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone(); result=dict(row) if row else None
        if own: con.close()
        return result
    def find_order_by_stripe(self, session_id=None, payment_intent_id=None):
        with self.connect() as con:
            row=con.execute("SELECT * FROM storefront_orders WHERE stripe_checkout_session_id=? OR stripe_payment_intent_id=?",(session_id or "",payment_intent_id or "")).fetchone()
            return dict(row) if row else None
    def attach_checkout(self, public_id, session_id, payment_intent_id=None):
        with self.transaction(immediate=True) as c:
            c.execute("UPDATE storefront_orders SET stripe_checkout_session_id=COALESCE(stripe_checkout_session_id,?),stripe_payment_intent_id=COALESCE(stripe_payment_intent_id,?),state=CASE WHEN state='checkout_pending' THEN 'authorization_pending' ELSE state END,updated_at=? WHERE public_id=?",(session_id,payment_intent_id,now(),public_id))
            return self.get_order(public_id,c)
    def transition_order(self, public_id, state, payment_state=None, error=None, conflict=None):
        with self.transaction(immediate=True) as c:
            row=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not row: raise ValueError("Order not found")
            if state!=row["state"] and state not in self.ORDER_TRANSITIONS.get(row["state"],set()): raise ValueError(f"Invalid order transition {row['state']} to {state}")
            c.execute("UPDATE storefront_orders SET state=?,payment_state=COALESCE(?,payment_state),operation_error=?,conflict_details=COALESCE(?,conflict_details),updated_at=? WHERE id=?",(state,payment_state,error,conflict,now(),row["id"]))
            return self.get_order(public_id,c)
    def release_order(self, public_id, state, reason):
        with self.transaction(immediate=True) as c:
            row=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not row: return None
            if state!=row["state"] and state not in self.ORDER_TRANSITIONS.get(row["state"],set()): raise ValueError("Invalid order transition")
            c.execute("UPDATE storefront_orders SET state=?,updated_at=? WHERE id=?",(state,now(),row["id"]))
            c.execute("UPDATE item_reservations SET state=?,close_reason=?,updated_at=? WHERE order_id=? AND state='open'",("expired" if state=="expired" else "released",reason,now(),row["id"]))
            return self.get_order(public_id,c)
    def expire_reservations(self, limit=100):
        with self.transaction(immediate=True) as c:
            rows=c.execute("SELECT order_id FROM item_reservations WHERE state='open' AND expires_at<=? LIMIT ?",(now(),limit)).fetchall()
            for row in rows:
                c.execute("UPDATE item_reservations SET state='expired',close_reason='expired',updated_at=? WHERE order_id=?",(now(),row["order_id"]))
                c.execute("UPDATE storefront_orders SET state='expired',updated_at=? WHERE id=? AND state IN ('checkout_pending','authorization_pending')",(now(),row["order_id"]))
            return len(rows)
    def claim_webhook(self,event_id,event_type):
        with self.transaction(immediate=True) as c:
            try: c.execute("INSERT INTO stripe_webhook_events(event_id,event_type,state,created_at,updated_at) VALUES(?,?,'processing',?,?)",(event_id,event_type,now(),now())); return True
            except sqlite3.IntegrityError: return False
    def finish_webhook(self,event_id,error=None):
        with self.transaction() as c: c.execute("UPDATE stripe_webhook_events SET state=?,error=?,updated_at=? WHERE event_id=?",("failed" if error else "processed",error,now(),event_id))
    def finalize_storefront_sale(self,public_id):
        with self.transaction(immediate=True) as c:
            order=c.execute("SELECT * FROM storefront_orders WHERE public_id=?",(public_id,)).fetchone()
            if not order: raise ValueError("Order not found")
            if order["state"]=="sold": return self.get_order(public_id,c)
            if order["state"] not in ("paid","capture_pending"): raise ValueError("Order is not captured")
            item=c.execute("SELECT quantity,status FROM items WHERE id=?",(order["item_id"],)).fetchone()
            if item["quantity"]<1 or item["status"]=="Sold":
                c.execute("UPDATE storefront_orders SET state='manual_resolution',conflict_details='Inventory sold by another channel',updated_at=? WHERE id=?",(now(),order["id"])); return self.get_order(public_id,c)
            c.execute("UPDATE items SET quantity=0,status='Sold',storefront_visible=0,updated_at=? WHERE id=?",(now(),order["item_id"]))
            c.execute("UPDATE storefront_orders SET state='sold',payment_state='captured',updated_at=? WHERE id=?",(now(),order["id"]))
            c.execute("UPDATE item_reservations SET state='closed',close_reason='sold',updated_at=? WHERE order_id=?",(now(),order["id"]))
            return self.get_order(public_id,c)
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
