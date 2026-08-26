"""Normalized, customer-safe view of the reviewed commerce policy."""
import hashlib
import json
import re
from money import validate_cents


def normalized_policy(cfg):
    countries=tuple(dict.fromkeys(str(x).upper() for x in cfg.commerce_supported_countries))
    regions={str(country).upper():tuple(dict.fromkeys(str(x).upper() for x in values))
             for country,values in cfg.commerce_supported_regions.items()}
    policy={
        "id":cfg.commerce_policy_id, "version":cfg.commerce_policy_version,
        "currency":cfg.commerce_currency, "countries":countries, "regions":regions,
        "shipping_mode":cfg.commerce_shipping_mode, "shipping_amount":cfg.commerce_shipping_amount,
        "tax_mode":cfg.commerce_tax_mode, "tax_amount":cfg.commerce_tax_amount,
        "destination_wording":cfg.commerce_destination_wording,
        "shipping_wording":cfg.commerce_shipping_wording, "tax_wording":cfg.commerce_tax_wording,
        "total_wording":cfg.commerce_total_wording,
        "unavailable_wording":cfg.commerce_unavailable_wording,
    }
    return policy


def validate_policy(cfg, *, production=False):
    p=normalized_policy(cfg)
    if p["shipping_mode"] not in {"fixed","calculated"} or p["tax_mode"] not in {"fixed","automatic"}:
        raise ValueError("Commerce shipping/tax calculation mode is invalid")
    if not p["countries"] or any(not re.fullmatch(r"[A-Z]{2}", x) for x in p["countries"]):
        raise ValueError("Supported countries must be explicit two-letter codes")
    if set(p["regions"])-set(p["countries"]): raise ValueError("Region rules require a supported country")
    if p["shipping_mode"] == "fixed": validate_cents(p["shipping_amount"],"shipping amount")
    elif p["shipping_amount"] is not None: raise ValueError("Calculated shipping cannot specify a fixed amount")
    if p["tax_mode"] == "fixed": validate_cents(p["tax_amount"],"tax amount")
    elif p["tax_amount"] is not None: raise ValueError("Automatic tax cannot specify a fixed amount")
    required=(p["id"],p["version"],p["currency"],p["destination_wording"],p["shipping_wording"],p["tax_wording"],p["total_wording"],p["unavailable_wording"])
    reviewed=cfg.commerce_policy_reviewed and all(isinstance(x,str) and x.strip() for x in required)
    if production and not reviewed: raise ValueError("An explicit reviewed commerce policy is required")
    return p


def destination_allowed(policy, country, region=None):
    country=str(country or "").strip().upper(); region=str(region or "").strip().upper()
    if country not in policy["countries"]: return False
    restricted=policy["regions"].get(country,())
    return not restricted or region in restricted


def policy_fingerprint(cfg):
    payload=normalized_policy(cfg)
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
