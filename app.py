import json, logging, re
from dataclasses import replace
from flask import Flask, abort, flash, redirect, render_template, request, url_for
import requests
from config import Config
from db import Database
from integrations.ebay import EbayService, EbayError
from integrations.ebay.client import EbayClient
from integrations.ebay.oauth import OAuthManager

def create_app(config=None, http=None):
    cfg=config or Config()
    app=Flask(__name__); app.secret_key=cfg.secret_key
    class RedactOAuthQuery(logging.Filter):
        def filter(self, record):
            # Werkzeug formats the request target into args; remove callback query data
            # before it reaches any configured access-log handler.
            if record.args:
                record.args=tuple(re.sub(r"(/ebay/callback)\?[^ ]+",r"\1?[REDACTED]",str(v)) for v in record.args)
            return True
    logging.getLogger("werkzeug").addFilter(RedactOAuthQuery())
    db=Database(cfg.database_path); db.migrate(); session=http or requests.Session()
    def components(overrides=None):
        current=replace(cfg,**(overrides or {})); oauth=OAuthManager(current,db,session)
        return oauth,EbayService(current,db,EbayClient(current,oauth,session))
    def item_or_404(item_id): return db.get_item(item_id) or abort(404)
    @app.get("/")
    def home(): return redirect(url_for("sandbox"))
    @app.get("/items/<int:item_id>")
    def item_page(item_id): return render_template("item.html",item=item_or_404(item_id))
    @app.post("/items/<int:item_id>/<action>")
    def item_action(item_id,action):
        item=item_or_404(item_id); _,service=components()
        try: item={"list":service.listItem,"update":service.updateItem,"delist":service.delistItem}[action](item); flash(f"eBay {action} succeeded","success")
        except (EbayError,KeyError) as e: flash(json.dumps(e.safe_dict()) if isinstance(e,EbayError) else "Unknown action","error")
        return redirect(url_for("item_page",item_id=item["id"]))
    @app.get("/ebay/authorize")
    def authorize():
        oauth,_=components()
        try: url,_=oauth.begin(); return redirect(url)
        except EbayError as e: flash(str(e),"error"); return redirect(url_for("sandbox"))
    @app.get("/ebay/callback")
    def callback():
        oauth,_=components()
        try:
            if request.args.get("error"): raise EbayError("authentication",f"eBay authorization was declined: {request.args.get('error_description') or request.args['error']}")
            oauth.validate_state(request.args.get("state")); oauth.exchange_code(request.args.get("code","")); flash("Sandbox seller connected","success")
        except EbayError as e: flash(str(e),"error")
        return redirect(url_for("sandbox"))
    @app.route("/sandbox",methods=["GET","POST"])
    def sandbox():
        if cfg.environment != "sandbox": abort(404)
        oauth,_=components(); authenticated=False
        try: oauth.access_token(); authenticated=True
        except EbayError: pass
        if request.method=="POST":
            item=db.create_item(sku=request.form["sku"],title=request.form["title"],description=request.form["description"],price=request.form["price"],currency=cfg.currency,quantity=1,condition=request.form["condition"],brand=request.form["brand"],size=request.form["size"],color=request.form["color"],category=request.form["category"],photo_urls=[request.form["photo_url"]])
            overrides={k:request.form[k] for k in ("location_key","fulfillment_policy_id","payment_policy_id","return_policy_id") if request.form.get(k)}
            _,service=components(overrides)
            try: service.listItem(item); flash("Fake item published to eBay Sandbox","success")
            except EbayError as e: flash(json.dumps(e.safe_dict()),"error")
            return redirect(url_for("item_page",item_id=item["id"]))
        return render_template("sandbox.html",cfg=cfg,authenticated=authenticated)
    app.extensions["vintage_db"]=db; app.extensions["ebay_components"]=components
    return app

if __name__=="__main__": create_app().run(debug=True)
