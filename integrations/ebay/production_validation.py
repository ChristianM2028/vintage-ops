"""Read-only production seller validation. This module never mutates eBay."""
from .errors import EbayError

REQUIRED_SCOPES={"https://api.ebay.com/oauth/api_scope/sell.account","https://api.ebay.com/oauth/api_scope/sell.inventory","https://api.ebay.com/oauth/api_scope/sell.fulfillment"}

class ProductionSellerValidator:
    def __init__(self, config, db, client): self.cfg,self.db,self.client=config,db,client
    def validate(self, token_identity, scopes):
        if self.cfg.environment != "production": raise EbayError("configuration","Production validation requires production endpoints")
        if not token_identity or token_identity != self.cfg.expected_ebay_seller_id: raise EbayError("provider_identity","Production token seller identity mismatch")
        if not REQUIRED_SCOPES.issubset(set(scopes)): raise EbayError("authentication","Production token is missing required selling scopes")
        identity=self.client.request("GET","/commerce/identity/v1/user/")
        if identity.get("username") != self.cfg.expected_ebay_seller_id: raise EbayError("provider_identity","Production seller identity mismatch")
        location=self.client.request("GET",f"/sell/inventory/v1/location/{self.cfg.location_key}")
        policies={kind:self.client.request("GET",f"/sell/account/v1/{kind}_policy/{value}") for kind,value in (("fulfillment",self.cfg.fulfillment_policy_id),("payment",self.cfg.payment_policy_id),("return",self.cfg.return_policy_id))}
        if location.get("merchantLocationStatus") != "ENABLED": raise EbayError("configuration","Production merchant location is not enabled")
        if any(p.get("marketplaceId") != self.cfg.marketplace_id for p in policies.values()): raise EbayError("configuration","Production policy marketplace mismatch")
        privileges=self.client.request("GET","/sell/account/v1/privilege")
        if privileges.get("sellingLimit",{}).get("amount",{}).get("currency") != self.cfg.currency: raise EbayError("configuration","Production currency or selling privileges mismatch")
        activation={"activation_id":self.cfg.production_provider_activation_id,"seller_id":identity["username"],"token_identity":token_identity,"marketplace":self.cfg.marketplace_id,"merchant_location_key":self.cfg.location_key,"fulfillment_policy_id":self.cfg.fulfillment_policy_id,"payment_policy_id":self.cfg.payment_policy_id,"return_policy_id":self.cfg.return_policy_id,"currency":self.cfg.currency}
        return self.db.save_production_activation(activation)

def require_production_activation(config, db, token_identity):
    a=db.get_production_activation()
    expected={"activation_id":config.production_provider_activation_id,"seller_id":config.expected_ebay_seller_id,"token_identity":token_identity,"marketplace":config.marketplace_id,"merchant_location_key":config.location_key,"fulfillment_policy_id":config.fulfillment_policy_id,"payment_policy_id":config.payment_policy_id,"return_policy_id":config.return_policy_id,"currency":config.currency}
    if not a or any(a.get(k)!=v for k,v in expected.items()): raise EbayError("provider_identity","Complete production seller activation contract is required")
    return a
