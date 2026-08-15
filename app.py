import asyncio, json, logging, os, re, secrets
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
import requests
from config import Config
from db import Database, STATUSES
from integrations.ebay import EbayService, MockEbayAdapter, EbayError
from integrations.ebay.client import EbayClient
from integrations.ebay.oauth import OAuthManager
from automation.adapters.ebay import EbayBrowserAdapter
from automation.classifier import ClassifierConfig, InteractionClassifier
from automation.engine import FieldEngine
from automation.repository import AutomationRepository
from automation.resolver import ResolverConfig, SemanticResolver
from automation.workflow import ListingWorkflow

def async_playwright():
    # Playwright is optional unless the explicitly enabled test route is used.
    from playwright.async_api import async_playwright as factory
    return factory()

IMAGE_SIGNATURES={"jpg":lambda b:b.startswith(b"\xff\xd8\xff"),"jpeg":lambda b:b.startswith(b"\xff\xd8\xff"),"png":lambda b:b.startswith(b"\x89PNG\r\n\x1a\n"),"gif":lambda b:b.startswith((b"GIF87a",b"GIF89a")),"webp":lambda b:len(b)>=12 and b[:4]==b"RIFF" and b[8:12]==b"WEBP"}

def create_app(config=None, http=None):
    cfg=config or Config(); app=Flask(__name__); app.secret_key=cfg.secret_key
    upload_dir=Path(cfg.upload_directory).resolve(); upload_dir.mkdir(parents=True,exist_ok=True)
    class RedactOAuthQuery(logging.Filter):
        def filter(self, record):
            if record.args: record.args=tuple(re.sub(r"(/ebay/callback)\?[^ ]+",r"\1?[REDACTED]",str(v)) for v in record.args)
            return True
    logging.getLogger("werkzeug").addFilter(RedactOAuthQuery())
    db=Database(cfg.database_path); db.migrate(); http_session=http or requests.Session()
    def real_components(overrides=None):
        current=replace(cfg,**(overrides or {})); oauth=OAuthManager(current,db,http_session)
        return oauth,EbayService(current,db,EbayClient(current,oauth,http_session))
    def adapters():
        return {"ebay": MockEbayAdapter(cfg,db) if cfg.marketplace_mock_mode else real_components()[1]}
    def item_or_404(item_id): return db.get_item(item_id) or abort(404)
    def csrf_token():
        if "csrf_token" not in session: session["csrf_token"]=secrets.token_urlsafe(32)
        return session["csrf_token"]
    app.jinja_env.globals["csrf_token"]=csrf_token
    @app.before_request
    def csrf_protect():
        if request.method=="POST" and not secrets.compare_digest(session.get("csrf_token", ""), request.form.get("csrf_token", "")): abort(400,"Invalid CSRF token")

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
                    item=db.create_item(title=values["title"].strip(),description=values["description"].strip(),price=values["price"],currency=cfg.currency,quantity=1,condition=values["condition"].strip(),brand=values.get("brand","").strip(),size=values.get("size","").strip(),color=values.get("color","").strip(),category=values["category"].strip(),photo_urls=photo_urls,photos=[{"filename":n,"original_filename":o} for n,o,_ in prepared])
                    flash(f"Created {item['sku']}","success"); return redirect(url_for("item_page",item_id=item["id"]))
                except Exception:
                    for path in saved: path.unlink(missing_ok=True)
                    errors.append("The item could not be saved. Uploaded files were cleaned up.")
            for error in errors: flash(error,"error")
        return render_template("create_item.html",values=values,max_photos=cfg.max_photo_count,extensions=cfg.permitted_image_extensions)

    @app.get("/media/<path:filename>")
    def media(filename):
        if not re.fullmatch(r"[0-9a-f]{40}\.(?:jpg|jpeg|png|gif|webp)",filename) or not any(p["filename"]==filename for item in db.inventory() for p in item["photos"]): abort(404)
        return send_from_directory(upload_dir,filename)
    @app.get("/inventory")
    def inventory():
        status=request.args.get("status"); return render_template("inventory.html",items=db.inventory(status),statuses=STATUSES,selected=status)
    @app.get("/items/<int:item_id>")
    def item_page(item_id): return render_template("item.html",item=item_or_404(item_id),mock_mode=cfg.marketplace_mock_mode,test_browser_flow=cfg.test_ebay_browser_flow)
    @app.get("/automation-runs/<run_id>")
    def automation_run_detail(run_id):
        run=AutomationRepository(db).get_run_detail(run_id)
        if not run: abort(404)
        return render_template("automation_run.html",run=run)
    @app.post("/items/<int:item_id>/test-ebay-browser-flow")
    def test_ebay_browser_flow(item_id):
        if not cfg.test_ebay_browser_flow: abort(404)
        item=item_or_404(item_id); repository=AutomationRepository(db); run=repository.create_run(item["id"],"ebay","list")
        async def execute():
            adapter=EbayBrowserAdapter()
            resolver=SemanticResolver(ResolverConfig(write_threshold=cfg.automation_resolution_threshold,score_margin=cfg.automation_resolution_margin))
            classifier=InteractionClassifier(ClassifierConfig(action_threshold=cfg.automation_classification_threshold))
            profile=Path(cfg.ebay_browser_user_data_directory).expanduser().resolve(); profile.mkdir(parents=True,exist_ok=True)
            async with async_playwright() as playwright:
                context=await playwright.chromium.launch_persistent_context(user_data_dir=str(profile),headless=False)
                page=await context.new_page()
                @asynccontextmanager
                async def existing_page(_adapter,_run): yield page
                try:
                    repository.set_run_state(run["id"],"running")
                    await ListingWorkflow(adapter,FieldEngine(resolver,classifier),repository,existing_page).execute(run)
                finally: await context.close()
        try:
            asyncio.run(execute()); repository.set_run_state(run["id"],"succeeded")
        except Exception as exc:
            app.logger.exception("Test-only eBay browser flow failed")
            safe=exc.safe_dict() if hasattr(exc,"safe_dict") else {"code":"browser_flow_failed","message":"Browser automation failed safely"}
            repository.set_run_state(run["id"],"failed",safe)
        return redirect(url_for("automation_run_detail",run_id=run["id"]))
    def run_action(item_id, operation, success):
        item=item_or_404(item_id)
        try: item=getattr(adapters()["ebay"],operation)(item); flash(success,"success")
        except EbayError as e: db.update_item(item_id,last_error=json.dumps(e.safe_dict())); flash(str(e),"error")
        return redirect(url_for("item_page",item_id=item_id))
    @app.post("/items/<int:item_id>/list-ebay")
    def list_ebay(item_id): return run_action(item_id,"list","Listed on eBay.")
    @app.post("/items/<int:item_id>/delist-ebay")
    def delist_ebay(item_id): return run_action(item_id,"delist","Delisted from eBay.")
    @app.post("/items/<int:item_id>/list-everywhere")
    def list_everywhere(item_id):
        # The registry intentionally contains implemented adapters only.
        item=item_or_404(item_id)
        try:
            for adapter in adapters().values(): item=adapter.list(item)
            flash("Listed on every configured marketplace (eBay).","success")
        except EbayError as e: db.update_item(item_id,last_error=json.dumps(e.safe_dict())); flash("Not all marketplaces were listed: "+str(e),"error")
        return redirect(url_for("item_page",item_id=item_id))
    @app.post("/items/<int:item_id>/sold-delist-everywhere")
    def sold_everywhere(item_id):
        item=item_or_404(item_id); failures=[]
        for adapter in adapters().values():
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
        except EbayError as e: flash(str(e),"error"); return redirect(url_for("sandbox"))
    @app.get("/ebay/callback")
    def callback():
        oauth,_=real_components()
        try:
            if request.args.get("error"): raise EbayError("authentication",f"eBay authorization was declined: {request.args.get('error_description') or request.args['error']}")
            oauth.validate_state(request.args.get("state")); oauth.exchange_code(request.args.get("code","")); flash("Sandbox seller connected","success")
        except EbayError as e: flash(str(e),"error")
        return redirect(url_for("sandbox"))
    @app.route("/sandbox",methods=["GET","POST"])
    def sandbox():
        if cfg.environment != "sandbox": abort(404)
        oauth,_=real_components(); authenticated=False
        try: oauth.access_token(); authenticated=True
        except EbayError: pass
        if request.method=="POST":
            item=db.create_item(title=request.form["title"],description=request.form["description"],price=request.form["price"],currency=cfg.currency,quantity=1,condition=request.form["condition"],brand=request.form["brand"],size=request.form["size"],color=request.form["color"],category=request.form["category"],photo_urls=[request.form["photo_url"]])
            overrides={k:request.form[k] for k in ("location_key","fulfillment_policy_id","payment_policy_id","return_policy_id") if request.form.get(k)}
            try: real_components(overrides)[1].list(item); flash("Fake item published to eBay Sandbox","success")
            except EbayError as e: flash(json.dumps(e.safe_dict()),"error")
            return redirect(url_for("item_page",item_id=item["id"]))
        return render_template("sandbox.html",cfg=cfg,authenticated=authenticated)
    app.extensions.update(vintage_db=db,ebay_components=real_components,marketplace_adapters=adapters)
    return app

if __name__=="__main__": create_app().run(debug=True)
