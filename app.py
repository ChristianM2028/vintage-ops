import json, logging, os, re, secrets
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from flask import Flask, abort, flash, jsonify, make_response, redirect, render_template, request, send_from_directory, session, url_for
from functools import wraps
from urllib.parse import urlsplit
from werkzeug.security import check_password_hash
import requests
from config import Config
from db import Database, STATUSES
from integrations.ebay import EbayService, MockEbayAdapter, EbayError, SandboxSellerSetup, EbayMetadataService
from integrations.ebay.aspects import parse_aspects, parse_conditions, validate_specifics
from integrations.ebay.client import EbayClient
from integrations.ebay.oauth import OAuthManager
from integrations.ebay.photos import EbayImagePublisher
from integrations.stripe import StripeAdapter, FakeStripeAdapter, StripeIntegrationError
from order_service import OrderService
from fulfillment_service import FulfillmentService, FulfillmentError
from refund_service import RefundService, RefundError
from manual_resolution_service import ManualResolutionService, ManualResolutionError

IMAGE_SIGNATURES={"jpg":lambda b:b.startswith(b"\xff\xd8\xff"),"jpeg":lambda b:b.startswith(b"\xff\xd8\xff"),"png":lambda b:b.startswith(b"\x89PNG\r\n\x1a\n"),"gif":lambda b:b.startswith((b"GIF87a",b"GIF89a")),"webp":lambda b:len(b)>=12 and b[:4]==b"RIFF" and b[8:12]==b"WEBP"}

def create_app(config=None, http=None, stripe_adapter=None):
    cfg=config or Config(); app=Flask(__name__); app.secret_key=cfg.secret_key
    upload_dir=Path(cfg.upload_directory).resolve(); upload_dir.mkdir(parents=True,exist_ok=True)
    class RedactOAuthQuery(logging.Filter):
        def filter(self, record):
            if record.args: record.args=tuple(re.sub(r"(/ebay/callback)\?[^ ]+",r"\1?[REDACTED]",str(v)) for v in record.args)
            return True
    logging.getLogger("werkzeug").addFilter(RedactOAuthQuery())
    db=Database(cfg.database_path); db.migrate(); http_session=http or requests.Session()
    stripe_gateway=stripe_adapter or (FakeStripeAdapter(cfg) if cfg.stripe_fake_mode else StripeAdapter(cfg))
    password_hash_valid=True
    if cfg.seller_password_hash:
        try: check_password_hash(cfg.seller_password_hash,"startup-validation-only")
        except (ValueError,TypeError): password_hash_valid=False
    app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Lax",SESSION_COOKIE_SECURE=cfg.session_cookie_secure)
    def real_components(overrides=None):
        current=replace(cfg,**(overrides or {})); oauth=OAuthManager(current,db,http_session)
        return oauth,EbayService(current,db,EbayClient(current,oauth,http_session))
    def metadata_component(overrides=None):
        current=replace(cfg,**(overrides or {})); oauth=OAuthManager(current,db,http_session)
        return EbayMetadataService(current,db,EbayClient(current,oauth,http_session))
    SNAPSHOT_FIELDS={"location_key":"ebay_merchant_location_key","fulfillment_policy_id":"ebay_fulfillment_policy_id",
                     "payment_policy_id":"ebay_payment_policy_id","return_policy_id":"ebay_return_policy_id",
                     "marketplace_id":"ebay_marketplace_id"}
    def resolve_ebay(item, establish=False):
        """Resolve from persisted item state, classifying an unlisted item once."""
        mode=item.get("ebay_integration_mode")
        if not mode and establish:
            associated=any(item.get(k) for k in ("ebay_sku","ebay_offer_id","ebay_listing_id","ebay_publication_state"))
            if associated: raise EbayError("classification_required","This legacy published item needs an explicit eBay integration mode")
            mode="local_mock" if cfg.marketplace_mock_mode else ("ebay_sandbox" if cfg.environment=="sandbox" else "ebay_production")
            values={"ebay_integration_mode":mode}
            if mode!="local_mock": values.update({column:getattr(cfg,key) for key,column in SNAPSHOT_FIELDS.items()})
            item=db.update_item(item["id"],**values)
        if mode=="local_mock": return MockEbayAdapter(cfg,db),item
        if mode in ("ebay_sandbox","ebay_production"):
            if any(not item.get(column) for column in SNAPSHOT_FIELDS.values()):
                raise EbayError("classification_required","This item is missing its persisted eBay seller configuration")
            overrides={key:item[column] for key,column in SNAPSHOT_FIELDS.items()}
            overrides["environment"]="sandbox" if mode=="ebay_sandbox" else "production"
            overrides["marketplace_mock_mode"]=False
            return real_components(overrides)[1],item
        raise EbayError("classification_required","Choose an eBay integration mode before operating on this item")
    order_service=OrderService(db,stripe_gateway,resolve_ebay,cfg)
    manual_resolution_service=ManualResolutionService(db,stripe_gateway,resolve_ebay,cfg)
    fulfillment_service=FulfillmentService(db)
    refund_service=RefundService(db,stripe_gateway)
    def adapters():
        return {"ebay": MockEbayAdapter(cfg,db) if cfg.marketplace_mock_mode else real_components()[1]}
    def item_or_404(item_id): return db.get_item(item_id) or abort(404)
    def csrf_token():
        if "csrf_token" not in session: session["csrf_token"]=secrets.token_urlsafe(32)
        return session["csrf_token"]
    app.jinja_env.globals["csrf_token"]=csrf_token
    @app.before_request
    def csrf_protect():
        expected=session.get("csrf_token")
        if request.method=="POST" and request.endpoint!="stripe_webhook" and (not expected or not secrets.compare_digest(expected, request.form.get("csrf_token", ""))): abort(400,"Invalid CSRF token")

    public_endpoints={"shop","shop_item","media","checkout","order_status","order_success","order_cancel","fake_complete_order","fake_cancel_order","stripe_webhook","static","login"}
    @app.before_request
    def seller_guard():
        # Order fulfillment data is never public, even on an incompletely
        # configured development install where the legacy seller UI is open.
        always_protected={"orders","order_detail","packing_slip","seller_cancel_order","seller_retry_order","seller_refund_order","seller_recheck_refund","manual_recheck","manual_resume","manual_cancel","manual_reconcile_canceled","manual_reconcile_captured","mark_packing","mark_shipped","correct_tracking","mark_delivered","add_order_note","acknowledge_attention"}
        if request.endpoint not in always_protected and (not cfg.seller_username or not cfg.seller_password_hash): return
        if request.endpoint in public_endpoints: return
        if not session.get("seller_authenticated"):
            return redirect(url_for("login",next=request.full_path if request.method=="GET" else ""))

    @app.route("/login",methods=["GET","POST"])
    def login():
        if request.method=="POST":
            valid_user=secrets.compare_digest(request.form.get("username",""),cfg.seller_username)
            try:
                valid_password=password_hash_valid and check_password_hash(cfg.seller_password_hash,request.form.get("password","")) if cfg.seller_password_hash else False
            except (ValueError,TypeError): valid_password=False
            if valid_user and valid_password:
                destination=request.form.get("next","")
                session.clear(); session["seller_authenticated"]=True; session["csrf_token"]=secrets.token_urlsafe(32)
                if not destination or urlsplit(destination).netloc or not destination.startswith("/"): destination=url_for("home")
                return redirect(destination)
            flash("Invalid seller credentials.","error")
        return render_template("login.html",next=request.args.get("next", ""))

    @app.post("/logout")
    def logout(): session.clear(); return redirect(url_for("login"))

    def category_id(value):
        value=str(value or "").strip()
        if not re.fullmatch(r"[0-9]{1,20}",value): abort(400,"Invalid category ID")
        return value
    def metadata_for(category):
        category=category_id(category)
        return parse_aspects(metadata_component().aspects(category)),parse_conditions(metadata_component().conditions(category))
    def submitted_specifics():
        result={}
        for key in request.form:
            if key.startswith("aspect.") and key[7:]: result[key[7:]]=request.form.getlist(key)
        return result
    def validate_metadata_form(values):
        errors=[]
        category=values.get("category","").strip(); name=values.get("category_name","").strip()
        # Local mock mode has no taxonomy API. Retain its deliberately isolated
        # legacy fixtures; real seller workflows always require a suggestion.
        if cfg.marketplace_mock_mode and category and not name:
            values["category_name"]=category
            return {},[],[],[]
        if not category or not name: return {},[],[],["Select a category from the suggestions."]
        try: fields,conditions=metadata_for(category)
        except EbayError: return {},[],[],["Category metadata is temporarily unavailable; your entries were preserved."]
        specifics,specific_errors=validate_specifics(fields,submitted_specifics()); errors.extend(specific_errors)
        allowed={x["value"] for x in conditions}
        if values.get("condition") not in allowed: errors.append("Choose a condition available for the selected category.")
        selected=next((x for x in conditions if x["value"]==values.get("condition")),None)
        if selected and selected["requires_descriptors"]: errors.append("This condition requires supplementary descriptors that this form does not yet support; choose another condition.")
        # Canonical garment fields mirror their exact eBay-named controls.
        for local in ("brand","size","color"):
            match=next((f["name"] for f in fields if f["name"].casefold()==local),None)
            if match and specifics.get(match): values[local]=specifics[match][0]
        return specifics,fields,conditions,errors

    @app.get("/ebay/metadata/category-suggestions")
    def category_suggestions():
        query=" ".join(request.args.get("q","").split()).strip()[:100]
        if len(query)<2: return jsonify({"suggestions":[]})
        data=metadata_component().suggestions(query); safe=[]
        for entry in data.get("categorySuggestions",[])[:20]:
            cat=entry.get("category") or {}; cid=str(cat.get("categoryId", ""))
            if re.fullmatch(r"[0-9]{1,20}",cid) and cat.get("categoryName"):
                safe.append({"id":cid,"name":str(cat["categoryName"])[:200]})
        return jsonify({"suggestions":safe})
    @app.get("/ebay/metadata/categories/<category>/aspects")
    def category_aspects(category): return jsonify({"aspects":parse_aspects(metadata_component().aspects(category_id(category)))})
    @app.get("/ebay/metadata/categories/<category>/conditions")
    def category_conditions(category): return jsonify({"conditions":parse_conditions(metadata_component().conditions(category_id(category)))})

    @app.route("/",methods=["GET","POST"])
    def home():
        values=request.form.to_dict() if request.method=="POST" else {}
        errors=[]; saved=[]
        if request.method=="POST":
            for field in ("title","description","price","condition","category"):
                if not request.form.get(field,"").strip(): errors.append(f"{field.replace('_',' ').title()} is required.")
            try:
                price=Decimal(request.form.get("price",""));
                if price <= 0 or price.as_tuple().exponent < -2: raise InvalidOperation
                values["price"]=f"{price:.2f}"
            except (InvalidOperation,ValueError): errors.append("Price must be a positive amount with at most two decimal places.")
            specifics,aspect_fields,conditions,metadata_errors=validate_metadata_form(values); errors.extend(metadata_errors)
            files=[f for f in request.files.getlist("photos") if f and f.filename]
            if not files: errors.append("Add at least one photo.")
            if len(files)>cfg.max_photo_count: errors.append(f"You may upload at most {cfg.max_photo_count} photos.")
            prepared=[]
            if not errors:
                for f in files:
                    ext=Path(f.filename).suffix.lower().lstrip("."); data=f.read(cfg.max_upload_size+1)
                    if ext not in cfg.permitted_image_extensions or ext not in IMAGE_SIGNATURES or not IMAGE_SIGNATURES[ext](data): errors.append(f"{f.filename}: file contents do not match a permitted image type."); continue
                    if len(data)>cfg.max_upload_size: errors.append(f"{f.filename}: image exceeds the {cfg.max_upload_size} byte limit."); continue
                    name=f"{secrets.token_hex(20)}.{ext}"; prepared.append((name,Path(f.filename).name,data))
            if not errors:
                try:
                    for name,_,data in prepared: (upload_dir/name).write_bytes(data); saved.append(upload_dir/name)
                    photo_urls=[url_for("media",filename=name,_external=True) for name,_,_ in prepared]
                    item=db.create_item(title=values["title"].strip(),description=values["description"].strip(),price=values["price"],currency=cfg.currency,quantity=1,condition=values["condition"].strip(),brand=values.get("brand","").strip(),size=values.get("size","").strip(),color=values.get("color","").strip(),category=values["category"].strip(),category_name=values["category_name"].strip(),item_specifics=specifics,photo_urls=photo_urls,photos=[{"filename":n,"original_filename":o} for n,o,_ in prepared])
                    flash(f"Created {item['sku']}","success"); return redirect(url_for("item_page",item_id=item["id"]))
                except Exception:
                    for path in saved: path.unlink(missing_ok=True)
                    errors.append("The item could not be saved. Uploaded files were cleaned up.")
            for error in errors: flash(error,"error")
        return render_template("create_item.html",values=values,max_photos=cfg.max_photo_count,extensions=cfg.permitted_image_extensions,submitted_specifics=submitted_specifics())

    @app.get("/media/<path:filename>")
    def media(filename):
        if not re.fullmatch(r"[0-9a-f]{40}\.(?:jpg|jpeg|png|gif|webp)",filename) or not any(p["filename"]==filename for item in db.inventory() for p in item["photos"]): abort(404)
        return send_from_directory(upload_dir,filename)
    @app.get("/inventory")
    def inventory():
        status=request.args.get("status"); return render_template("inventory.html",items=db.inventory(status),statuses=STATUSES,selected=status)
    @app.get("/items/<int:item_id>")
    def item_page(item_id):
        item=item_or_404(item_id); missing=[]
        try: missing=(json.loads(item.get("last_error") or "{}").get("missing_aspects") or [])
        except (ValueError,TypeError): pass
        return render_template("item.html",item=item,missing_aspects=missing)
    @app.get("/shop")
    def shop():
        allowed={"q","brand","size","min_price","max_price","sort"}
        if set(request.args)-allowed: abort(400,"Unknown catalog parameter")
        values={key: request.args.get(key,"").strip() for key in allowed}
        if any(len(values[key])>100 for key in ("q","brand","size")): abort(400,"Catalog parameter is too long")
        sort=values["sort"] or "newest"
        if sort not in {"newest","price_asc","price_desc"}: abort(400,"Invalid sort")
        prices={}
        for key in ("min_price","max_price"):
            if not values[key]: prices[key]=None; continue
            try:
                price=Decimal(values[key])
                if not price.is_finite() or price < 0 or price.as_tuple().exponent < -2: raise InvalidOperation
                prices[key]=price
            except (InvalidOperation,ValueError): abort(400,f"Invalid {key.replace('_',' ')}")
        if prices["min_price"] is not None and prices["max_price"] is not None and prices["min_price"]>prices["max_price"]: abort(400,"Minimum price exceeds maximum price")
        items=db.storefront_catalog(values["q"] or None,values["brand"] or None,values["size"] or None,prices["min_price"],prices["max_price"],sort)
        return render_template("shop_catalog.html",items=items,filters=values)
    @app.get("/shop/item/<sku>")
    def shop_item(sku):
        item=db.get_storefront_item_by_sku(sku) or abort(404)
        return render_template("shop_item.html",item=item)
    @app.post("/shop/item/<sku>/checkout")
    def checkout(sku):
        prior_id=(session.get("checkout_orders") or {}).get(sku)
        try: order,provider_url=order_service.create_checkout(sku,prior_id)
        except ValueError: abort(409,"This piece is no longer available")
        except StripeIntegrationError: abort(503,"Checkout is temporarily unavailable")
        checkout_orders=dict(session.get("checkout_orders") or {}); checkout_orders[sku]=order["public_id"]; session["checkout_orders"]=checkout_orders
        destination=url_for("order_status",public_id=order["public_id"]) if cfg.stripe_fake_mode else provider_url
        return redirect(destination,code=303)

    @app.post("/stripe/webhook")
    def stripe_webhook():
        payload=request.get_data(cache=False)
        try: event=stripe_gateway.verify_webhook(payload,request.headers.get("Stripe-Signature"))
        except StripeIntegrationError: abort(400,"Invalid signature")
        event_id=str(event.get("id", "")); event_type=str(event.get("type", ""))
        claim=db.claim_webhook(event_id,event_type) if event_id else None
        if not event_id: return "",400
        if claim in ("already_complete","busy"): return "",200
        if claim != "claimed": return "",503
        try:
            obj=(event.get("data") or {}).get("object") or {}
            relevant={"checkout.session.completed","checkout.session.expired","payment_intent.amount_capturable_updated","payment_intent.succeeded","payment_intent.canceled","charge.refunded","refund.created","refund.updated","refund.failed"}
            if event_type not in relevant:
                db.correlate_webhook(event_id,"irrelevant")
                db.finish_webhook(event_id,claim_token=claim["claim_token"]); return "",200
            normalized_refunds=refund_service.normalize_webhook(event_type,obj)
            if normalized_refunds:
                for refund in normalized_refunds:
                    pi_id=refund.get("payment_intent")
                    reference=refund.get("reference")
                    order=db.find_order_by_stripe(payment_intent_id=pi_id) if pi_id else None
                    if not order and reference: order=db.get_order(reference)
                    # Metadata may locate an order, but only its already-persisted
                    # PaymentIntent is trusted for the eventual exact match.
                    if order: refund_service.reconcile(order,refund,actor="stripe_webhook")
                db.correlate_webhook(event_id,"matched" if order else "unmatched_retryable",payment_intent_id=pi_id,reference=reference,matched_order_id=order["id"] if order else None)
                db.finish_webhook(event_id,claim_token=claim["claim_token"]); return "",200
            sid=obj.get("id") if event_type.startswith("checkout.session") else obj.get("checkout_session")
            metadata=obj.get("metadata") or {}; reference=obj.get("client_reference_id") or metadata.get("order")
            pi=obj.get("payment_intent") or (obj.get("id") if event_type.startswith("payment_intent") else None)
            pi_id=pi.get("id") if isinstance(pi,dict) else pi
            order=db.find_order_by_stripe(sid,pi_id) or (db.get_order(reference) if reference else None)
            if order and sid: order=db.attach_checkout(order["public_id"],sid,pi_id,source="stripe_webhook")
            if order and pi_id: order=db.attach_payment_intent(order["public_id"],pi_id,order_service._fulfillment(obj))
            if order and event_type in ("checkout.session.completed","payment_intent.amount_capturable_updated"):
                session_obj=obj if event_type.startswith("checkout.session") else None
                intent=obj if event_type.startswith("payment_intent") else (pi if isinstance(pi,dict) else None)
                outcome=order_service.process_authorization(order,session_obj,intent)
                if outcome["state"]=="manual_resolution": raise RuntimeError("manual_resolution")
            elif order and event_type in ("checkout.session.expired","payment_intent.canceled") and order["state"] in ("checkout_pending","authorization_pending"):
                db.release_order(order["public_id"],"expired" if event_type=="checkout.session.expired" else "cancelled",event_type,source="stripe_webhook")
            db.correlate_webhook(event_id,"matched" if order else "unmatched_retryable",sid,pi_id,reference,order["id"] if order else None)
            db.finish_webhook(event_id,claim_token=claim["claim_token"]); return "",200
        except Exception as exc:
            classification="manual_resolution" if type(exc).__name__=="RuntimeError" else "processing_failure"
            known=order["public_id"] if "order" in locals() and order else None
            app.logger.warning("Stripe webhook failure event_id=%s event_type=%s order=%s classification=%s",event_id,event_type,known,classification)
            db.finish_webhook(event_id,classification,claim["claim_token"]); return "",503

    def fake_development_mode(): return cfg.stripe_fake_mode and cfg.environment != "production"
    def render_order(public_id, **context):
        response=make_response(render_template("order_status.html",order=db.public_order_status(public_id) or abort(404),fake_development_mode=fake_development_mode(),**context))
        response.headers.update({"Cache-Control":"no-store","X-Robots-Tag":"noindex, nofollow","Referrer-Policy":"no-referrer"})
        return response
    @app.get("/shop/order/<public_id>")
    def order_status(public_id): return render_order(public_id)
    @app.get("/shop/order/<public_id>/success")
    def order_success(public_id): return render_order(public_id,redirect_kind="success")
    @app.get("/shop/order/<public_id>/cancel")
    def order_cancel(public_id): return render_order(public_id,redirect_kind="cancel")

    @app.post("/shop/order/<public_id>/complete")
    def fake_complete_order(public_id):
        if not fake_development_mode(): abort(404)
        order=db.get_order(public_id) or abort(404)
        if order["state"]=="sold": return redirect(url_for("order_status",public_id=public_id),code=303)
        if order["state"] not in ("checkout_pending","authorization_pending"): abort(409,"Order can no longer be completed")
        # Fake checkout models an already-authorized manual-capture intent.
        order_service.process_authorization(order,payment_intent={"id":order["stripe_payment_intent_id"],"status":"requires_capture","capture_method":"manual"},source="shopper")
        return redirect(url_for("order_status",public_id=public_id),code=303)

    @app.post("/shop/order/<public_id>/cancel-checkout")
    def fake_cancel_order(public_id):
        if not fake_development_mode(): abort(404)
        order=db.get_order(public_id) or abort(404)
        if order["state"]=="cancelled": return redirect(url_for("order_status",public_id=public_id),code=303)
        if order["state"] not in ("checkout_pending","authorization_pending"): abort(409,"Order can no longer be cancelled")
        order_service.seller_cancel(public_id)
        return redirect(url_for("order_status",public_id=public_id),code=303)

    @app.get("/orders")
    def orders():
        allowed={"state","payment_state","fulfillment_state","refund_state","attention","date","sku","order_id","page"}
        if set(request.args)-allowed: abort(400,"Unknown order filter")
        states=set(db.ORDER_TRANSITIONS); payment_states={"unpaid","authorized","captured","cancelled"}
        filters={key:request.args.get(key,"").strip() for key in allowed-{"page"}}
        if filters["state"] and filters["state"] not in states: abort(400,"Invalid order state")
        if filters["payment_state"] and filters["payment_state"] not in payment_states: abort(400,"Invalid payment state")
        fulfillment_states={"not_applicable","unfulfilled","packing","shipped","delivered"}; refund_states={"none","pending","refunded","manual_resolution"}
        if filters["fulfillment_state"] and filters["fulfillment_state"] not in fulfillment_states: abort(400,"Invalid fulfillment state")
        if filters["refund_state"] and filters["refund_state"] not in refund_states: abort(400,"Invalid refund state")
        if filters["attention"] not in ("","open","none"): abort(400,"Invalid attention filter")
        if filters["date"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}",filters["date"]): abort(400,"Invalid date")
        if any(len(filters[x])>100 for x in ("sku","order_id")): abort(400,"Order filter is too long")
        try: page=int(request.args.get("page","1"))
        except ValueError: abort(400,"Invalid page")
        if page<1 or page>10000: abort(400,"Invalid page")
        rows,total=db.seller_orders(filters,page,25)
        return render_template("orders.html",orders=rows,filters=filters,page=page,pages=max(1,(total+24)//25),states=sorted(states),payment_states=sorted(payment_states),fulfillment_states=sorted(fulfillment_states),refund_states=sorted(refund_states),summary=db.seller_order_summary())

    @app.get("/orders/<public_id>")
    def order_detail(public_id):
        response=make_response(render_template("order_detail.html",order=db.seller_order(public_id) or abort(404),manual=manual_resolution_service.available_actions(public_id)))
        response.headers.update({"Cache-Control":"private, no-store","Referrer-Policy":"no-referrer"}); return response
    @app.get("/orders/<public_id>/packing-slip")
    def packing_slip(public_id):
        order=db.seller_order(public_id) or abort(404)
        response=make_response(render_template("packing_slip.html",order=order,business_name=cfg.business_name,business_address=cfg.business_address))
        response.headers.update({"Cache-Control":"private, no-store","Referrer-Policy":"no-referrer","X-Robots-Tag":"noindex"}); return response
    def manual_action(call):
        try: call()
        except ManualResolutionError as exc: abort(409,str(exc))
        return redirect(url_for("order_detail",public_id=request.view_args["public_id"]),code=303)
    @app.post("/orders/<public_id>/manual-resolution/recheck")
    def manual_recheck(public_id): return manual_action(lambda: manual_resolution_service.recheck(public_id,request.form.get("request_token")))
    @app.post("/orders/<public_id>/manual-resolution/resume")
    def manual_resume(public_id):
        if request.form.get("confirmation")!="CAPTURE": abort(400,"Type CAPTURE to continue")
        return manual_action(lambda: manual_resolution_service.resume_purchase(public_id,int(request.form.get("review_id",0)),request.form.get("request_token")))
    @app.post("/orders/<public_id>/manual-resolution/cancel")
    def manual_cancel(public_id):
        if request.form.get("confirmation")!="CANCEL": abort(400,"Type CANCEL to continue")
        return manual_action(lambda: manual_resolution_service.cancel_authorization(public_id,int(request.form.get("review_id",0)),request.form.get("request_token")))
    @app.post("/orders/<public_id>/manual-resolution/reconcile-canceled")
    def manual_reconcile_canceled(public_id): return manual_action(lambda: manual_resolution_service.reconcile_canceled(public_id,int(request.form.get("review_id",0)),request.form.get("request_token")))
    @app.post("/orders/<public_id>/manual-resolution/reconcile-captured")
    def manual_reconcile_captured(public_id):
        if request.form.get("confirmation")!="CAPTURE": abort(400,"Type CAPTURE to continue")
        return manual_action(lambda: manual_resolution_service.reconcile_captured(public_id,int(request.form.get("review_id",0)),request.form.get("request_token")))
    def fulfillment_action(call):
        try: call()
        except FulfillmentError as exc: abort(409,str(exc))
        return redirect(url_for("order_detail",public_id=request.view_args["public_id"]),code=303)
    @app.post("/orders/<public_id>/packing")
    def mark_packing(public_id): return fulfillment_action(lambda: fulfillment_service.mark_packing(public_id,request.form.get("request_token")))
    @app.post("/orders/<public_id>/ship")
    def mark_shipped(public_id): return fulfillment_action(lambda: fulfillment_service.mark_shipped(public_id,request.form.get("carrier"),request.form.get("tracking_number"),request.form.get("request_token")))
    @app.post("/orders/<public_id>/tracking/correct")
    def correct_tracking(public_id): return fulfillment_action(lambda: fulfillment_service.correct_tracking(public_id,request.form.get("carrier"),request.form.get("tracking_number"),request.form.get("reason"),request.form.get("request_token")))
    @app.post("/orders/<public_id>/delivered")
    def mark_delivered(public_id): return fulfillment_action(lambda: fulfillment_service.mark_delivered(public_id,request.form.get("request_token")))
    @app.post("/orders/<public_id>/notes")
    def add_order_note(public_id): return fulfillment_action(lambda: fulfillment_service.add_note(public_id,request.form.get("note"),request.form.get("request_token")))
    @app.post("/orders/<public_id>/attention/<int:case_id>/acknowledge")
    def acknowledge_attention(public_id,case_id): return fulfillment_action(lambda: fulfillment_service.acknowledge_attention(public_id,case_id,request.form.get("request_token")))
    def confirmed():
        if request.form.get("confirmation") != "CONFIRM": abort(400,"Type CONFIRM to continue")
    @app.post("/orders/<public_id>/cancel")
    def seller_cancel_order(public_id):
        confirmed()
        try: order_service.seller_cancel(public_id)
        except ValueError: abort(409,"Order cannot be cancelled")
        return redirect(url_for("order_detail",public_id=public_id),code=303)
    @app.post("/orders/<public_id>/retry")
    def seller_retry_order(public_id):
        confirmed()
        try: order_service.retry(public_id)
        except ValueError: abort(409,"Order cannot be retried")
        return redirect(url_for("order_detail",public_id=public_id),code=303)
    @app.post("/orders/<public_id>/refund")
    def seller_refund_order(public_id):
        confirmed(); order=db.get_order(public_id) or abort(404)
        if order["fulfillment_state"] in ("shipped","delivered") and request.form.get("fulfillment_confirmation")!="CONFIRM":
            abort(400,"Confirm the shipped or delivered refund warning")
        try: refund_service.create_full(public_id)
        except RefundError as exc: abort(409,str(exc))
        return redirect(url_for("order_detail",public_id=public_id),code=303)
    @app.post("/orders/<public_id>/refund/recheck")
    def seller_recheck_refund(public_id):
        confirmed()
        try: refund_service.recheck(public_id)
        except RefundError as exc: abort(409,str(exc))
        return redirect(url_for("order_detail",public_id=public_id),code=303)

    @app.post("/maintenance/storefront")
    def storefront_maintenance():
        count=db.expire_reservations(100); flash(f"Expired {count} stale reservation(s).","success"); return redirect(url_for("inventory"))
    @app.post("/items/<int:item_id>/storefront-visibility")
    def storefront_visibility(item_id):
        item_or_404(item_id)
        visible=request.form.get("storefront_visible")
        if visible not in {"0","1"}: abort(400,"Invalid storefront visibility")
        db.update_item(item_id,storefront_visible=int(visible))
        flash("Storefront visibility updated.","success")
        return redirect(url_for("item_page",item_id=item_id))
    def run_action(item_id, operation, success):
        item=item_or_404(item_id)
        try:
            adapter,item=resolve_ebay(item,establish=operation=="list")
            if operation in ("list","update") and item.get("ebay_integration_mode")!="local_mock" and item.get("photos"):
                EbayImagePublisher(adapter.cfg,db,adapter.client).publish(item); item=db.get_item(item_id)
            item=getattr(adapter,operation)(item); flash(success,"success")
        except EbayError as e: db.update_item(item_id,last_error=json.dumps(e.safe_dict())); flash(str(e),"error")
        return redirect(url_for("item_page",item_id=item_id))
    @app.post("/items/<int:item_id>/list-ebay")
    def list_ebay(item_id): return run_action(item_id,"list","Listed on eBay.")
    @app.post("/items/<int:item_id>/delist-ebay")
    def delist_ebay(item_id): return run_action(item_id,"delist","Delisted from eBay.")
    @app.post("/items/<int:item_id>/sync-ebay")
    def sync_ebay(item_id): return run_action(item_id,"synchronize","eBay state synchronized.")
    @app.post("/items/<int:item_id>/update-ebay")
    def update_ebay(item_id): return run_action(item_id,"update","Edits synchronized to eBay.")
    @app.route("/items/<int:item_id>/edit",methods=["GET","POST"])
    def edit_item(item_id):
        item=item_or_404(item_id); values=dict(item); errors=[]
        if request.method=="POST":
            values.update(request.form.to_dict())
            for field in ("title","description","price","quantity","condition","category"):
                if not request.form.get(field,"").strip(): errors.append(f"{field.title()} is required.")
            try:
                price=Decimal(request.form.get("price","")); quantity=int(request.form.get("quantity",""))
                if price<=0 or price.as_tuple().exponent < -2 or quantity<0: raise ValueError
            except (ValueError,InvalidOperation): errors.append("Price and quantity must be valid non-negative values.")
            specifics,aspect_fields,conditions,metadata_errors=validate_metadata_form(values); errors.extend(metadata_errors)
            prepared=[]; saved=[]; ordered=[]
            current={p["id"]:p for p in item["photos"]}
            # Reject foreign/stale IDs even when smuggled in a field name.
            submitted_ids=set()
            for key in request.form:
                match=re.fullmatch(r"photo_(?:action|order)_(\d+)",key)
                if match: submitted_ids.add(int(match.group(1)))
            for key in request.files:
                match=re.fullmatch(r"replace_photo_(\d+)",key)
                if match: submitted_ids.add(int(match.group(1)))
            if submitted_ids != set(current): errors.append("The submitted photo selection is stale or invalid.")
            positions=[]
            for pid,photo in current.items():
                action=request.form.get(f"photo_action_{pid}")
                if action not in {"retain","remove","replace"}: errors.append(f"{photo['original_filename']}: choose retain, remove, or replace."); continue
                if action=="remove": continue
                try: position=int(request.form.get(f"photo_order_{pid}",""))
                except ValueError: position=-1
                if position<1: errors.append(f"{photo['original_filename']}: enter a valid display position.")
                upload=request.files.get(f"replace_photo_{pid}")
                if action=="replace" and (not upload or not upload.filename): errors.append(f"{photo['original_filename']}: choose a replacement file."); continue
                if action=="retain" and upload and upload.filename: errors.append(f"{photo['original_filename']}: select Replace to use that file."); continue
                positions.append((position,{"id":pid} if action=="retain" else (upload,photo["original_filename"])))
            if len([p for p,_ in positions])!=len(set(p for p,_ in positions)): errors.append("Each existing photo must have a unique display position.")
            additions=[f for f in request.files.getlist("photos") if f and f.filename]
            if len(positions)+len(additions)>cfg.max_photo_count: errors.append(f"You may upload at most {cfg.max_photo_count} photos in total.")
            def prepare(upload, affected_name=None):
                ext=Path(upload.filename).suffix.lower().lstrip("."); data=upload.read(cfg.max_upload_size+1); label=affected_name or upload.filename
                if ext not in cfg.permitted_image_extensions or ext not in IMAGE_SIGNATURES or not IMAGE_SIGNATURES[ext](data): errors.append(f"{label}: invalid image type."); return None
                if len(data)>cfg.max_upload_size: errors.append(f"{label}: image is too large."); return None
                return {"filename":f"{secrets.token_hex(20)}.{ext}","original_filename":Path(upload.filename).name,"data":data}
            for position,spec in sorted(positions,key=lambda x:x[0]):
                if isinstance(spec,dict): ordered.append(spec)
                else:
                    made=prepare(spec[0],spec[1])
                    if made: ordered.append(made); prepared.append(made)
            for upload in additions:
                made=prepare(upload)
                if made: ordered.append(made); prepared.append(made)
            if not errors:
                try:
                    for p in prepared: (upload_dir/p["filename"]).write_bytes(p["data"]); saved.append(upload_dir/p["filename"])
                    mutation=[{k:v for k,v in p.items() if k!="data"} for p in ordered]
                    _,removed=db.mutate_item_photos(item_id,{"title":request.form["title"].strip(),"description":request.form["description"].strip(),"price":f"{price:.2f}","quantity":quantity,"condition":request.form["condition"].strip(),"category":request.form["category"].strip(),"category_name":request.form["category_name"].strip(),"brand":values.get("brand","").strip(),"size":values.get("size","").strip(),"color":values.get("color","").strip(),"item_specifics":specifics},mutation)
                    for filename in removed: (upload_dir/filename).unlink(missing_ok=True)
                    flash("Edits saved. Synchronize to apply them to eBay.","success"); return redirect(url_for("item_page",item_id=item_id))
                except Exception:
                    for path in saved: path.unlink(missing_ok=True)
                    errors.append("The edit could not be saved; replacement uploads were cleaned up.")
            for error in errors: flash(error,"error")
        return render_template("edit_item.html",item=item,values=values,submitted_specifics=submitted_specifics() if request.method=="POST" else item.get("item_specifics",{}))
    @app.post("/items/<int:item_id>/list-everywhere")
    def list_everywhere(item_id):
        # The registry intentionally contains implemented adapters only.
        item=item_or_404(item_id)
        try:
            adapter,item=resolve_ebay(item,establish=True)
            for adapter in (adapter,):
                if item.get("ebay_integration_mode")!="local_mock" and item.get("photos"):
                    EbayImagePublisher(adapter.cfg,db,adapter.client).publish(item); item=db.get_item(item_id)
                item=adapter.list(item)
            flash("Listed on every configured marketplace (eBay).","success")
        except EbayError as e: db.update_item(item_id,last_error=json.dumps(e.safe_dict())); flash("Not all marketplaces were listed: "+str(e),"error")
        return redirect(url_for("item_page",item_id=item_id))
    @app.post("/items/<int:item_id>/sold-delist-everywhere")
    def sold_everywhere(item_id):
        item=item_or_404(item_id); failures=[]
        try: adapter,item=resolve_ebay(item)
        except EbayError as e:
            db.update_item(item_id,last_error=json.dumps(e.safe_dict())); flash(str(e),"error")
            return redirect(url_for("item_page",item_id=item_id))
        for adapter in (adapter,):
            if item.get("ebay_publication_state")=="published":
                try: item=adapter.delist(item)
                except EbayError as e: failures.append({"marketplace":adapter.name,"error":e.safe_dict()})
        # Failure policy: mark Sold only after every active adapter confirms
        # withdrawal. On any failure, persist details and leave lifecycle intact.
        if failures:
            db.update_item(item_id,last_error=json.dumps({"sold_delist_failures":failures})); flash("Not marked Sold: one or more marketplaces could not be delisted.","error")
        else:
            db.transition_status(item_id,"Sold"); db.update_item(item_id,last_error=None); flash("Marked Sold after all active marketplaces were delisted.","success")
        return redirect(url_for("item_page",item_id=item_id))

    @app.get("/ebay/authorize")
    def authorize():
        oauth,_=real_components()
        try: url,_=oauth.begin(); return redirect(url)
        except EbayError as e: flash(str(e),"error"); return redirect(url_for("ebay_settings"))
    @app.get("/ebay/callback")
    def callback():
        oauth,_=real_components()
        try:
            if request.args.get("error"): raise EbayError("authentication",f"eBay authorization was declined: {request.args.get('error_description') or request.args['error']}")
            oauth.validate_state(request.args.get("state")); oauth.exchange_code(request.args.get("code","")); flash("eBay seller connected","success")
        except EbayError as e: flash(str(e),"error")
        return redirect(url_for("ebay_settings"))
    @app.get("/ebay/settings")
    def ebay_settings():
        oauth,_=real_components(); authenticated=False; auth_error=None
        try: oauth.access_token(); authenticated=True
        except EbayError as e: auth_error=e.safe_dict()
        return render_template("ebay_settings.html",cfg=cfg,authenticated=authenticated,auth_error=auth_error)
    @app.post("/ebay/reconcile-orders")
    def reconcile_orders():
        try:
            result=adapters()["ebay"].reconcile_orders() if not cfg.marketplace_mock_mode else {"processed":0,"duplicate":0,"unknown":0}
            flash(f"Order reconciliation complete: {result}","success")
        except EbayError as e: flash(str(e),"error")
        return redirect(url_for("ebay_settings"))
    @app.route("/sandbox",methods=["GET","POST"])
    def sandbox():
        if cfg.environment != "sandbox": abort(404)
        oauth,_=real_components(); authenticated=False
        try: oauth.access_token(); authenticated=True
        except EbayError: pass
        if request.method=="POST":
            setup=db.get_seller_setup()
            if not setup:
                flash("Set up the Sandbox seller before listing an item.","error")
                return render_template("sandbox.html",cfg=cfg,authenticated=authenticated,setup=None,values=request.form.to_dict(),submitted_specifics=submitted_specifics())
            values=request.form.to_dict(); specifics,_,_,errors=validate_metadata_form(values)
            if errors:
                for error in errors: flash(error,"error")
                return render_template("sandbox.html",cfg=cfg,authenticated=authenticated,setup=setup,values=values,submitted_specifics=submitted_specifics())
            overrides={"location_key":setup["merchant_location_key"], "fulfillment_policy_id":setup["fulfillment_policy_id"],
                       "payment_policy_id":setup["payment_policy_id"], "return_policy_id":setup["return_policy_id"],
                       "marketplace_id":setup["marketplace"]}
            item=db.create_item(title=request.form["title"],description=request.form["description"],price=request.form["price"],currency=cfg.currency,quantity=1,condition=request.form["condition"],brand=values.get("brand","").strip(),size=values.get("size","").strip(),color=values.get("color","").strip(),category=request.form["category"],category_name=request.form["category_name"],item_specifics=specifics,photo_urls=[request.form["photo_url"]],ebay_integration_mode="ebay_sandbox",**{SNAPSHOT_FIELDS[k]:v for k,v in overrides.items()})
            try: real_components(overrides)[1].list(item); flash("Fake item published to eBay Sandbox","success")
            except EbayError as e: flash(json.dumps(e.safe_dict()),"error")
            return redirect(url_for("item_page",item_id=item["id"]))
        return render_template("sandbox.html",cfg=cfg,authenticated=authenticated,setup=db.get_seller_setup(),values={},submitted_specifics={})
    @app.post("/sandbox/setup")
    def sandbox_setup():
        if cfg.environment != "sandbox": abort(404)
        oauth,service=real_components()
        try:
            oauth.access_token()  # Explicitly require an authenticated seller.
            setup=SandboxSellerSetup(cfg,db,service.client).run()
            flash("Sandbox seller setup is ready.","success")
        except EbayError as error:
            flash(json.dumps(error.safe_dict()),"error")
        return redirect(url_for("sandbox"))
    app.extensions.update(vintage_db=db,stripe_adapter=stripe_gateway,order_service=order_service,manual_resolution_service=manual_resolution_service,fulfillment_service=fulfillment_service,refund_service=refund_service,ebay_components=real_components,ebay_metadata=metadata_component(),ebay_metadata_factory=metadata_component,marketplace_adapters=adapters)
    return app

if __name__ == "__main__":
    create_app().run(debug=True, ssl_context="adhoc")
