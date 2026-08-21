"""Idempotent provisioning of the application's eBay Sandbox seller resources."""
from urllib.parse import quote

from .errors import EbayError


MARKETPLACE = "EBAY_US"
LOCATION_KEY = "vintage-ops-main"
POLICIES = {
    "fulfillment": ("Vintage Ops Sandbox Fulfillment", "fulfillmentPolicyId"),
    "payment": ("Vintage Ops Sandbox Payment", "paymentPolicyId"),
    "return": ("Vintage Ops Sandbox Returns", "returnPolicyId"),
}


class SandboxSellerSetup:
    """Create/reuse the resources required for Inventory API offers."""

    def __init__(self, config, db, client):
        self.cfg, self.db, self.client = config, db, client

    def run(self):
        if self.cfg.environment != "sandbox":
            raise EbayError("configuration", "Sandbox seller setup is unavailable outside the Sandbox environment")
        if self.cfg.marketplace_id != MARKETPLACE:
            raise EbayError("configuration", "Sandbox seller setup requires EBAY_US")

        programs = self.client.request("GET", "/sell/account/v1/program/get_opted_in_programs")
        enrolled = any(program.get("programType") == "SELLING_POLICY_MANAGEMENT"
                       for program in programs.get("programs", []))
        if not enrolled:
            self.client.request("POST", "/sell/account/v1/program/opt_in",
                                json={"programType": "SELLING_POLICY_MANAGEMENT"}, expected=(200,))

        values = {kind + "_policy_id": self._policy(kind) for kind in POLICIES}
        values["merchant_location_key"] = self._location()
        values["marketplace"] = MARKETPLACE
        # The opted-in-programs contract does not supply seller identity. Never
        # derive identity from the bearer credential.
        return self.db.save_seller_setup(seller_id=None, **values)

    def _policy(self, kind):
        name, id_field = POLICIES[kind]
        path = f"/sell/account/v1/{kind}_policy"

        def matches():
            data = self.client.request("GET", f"{path}?marketplace_id={MARKETPLACE}")
            return [p for p in data.get(kind + "Policies", [])
                    if p.get("marketplaceId") == MARKETPLACE and p.get("name") == name]

        found = matches()
        if len(found) > 1:
            raise EbayError("manual_resolution", f"Multiple app-managed {kind} policies exist; resolve duplicates in Seller Hub")
        if found:
            return found[0][id_field]
        try:
            self.client.request("POST", path, json=self._policy_payload(kind, name), expected=(201,))
        except EbayError as error:
            # A timeout/conflict can mean another request completed the create.
            if error.kind not in ("remote", "conflict"):
                raise
        found = matches()
        if len(found) != 1:
            message = "No" if not found else "Multiple"
            raise EbayError("manual_resolution", f"{message} app-managed {kind} policy was found after creation")
        return found[0][id_field]

    @staticmethod
    def _policy_payload(kind, name):
        common = {"name": name, "marketplaceId": MARKETPLACE,
                  "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES", "default": True}]}
        if kind == "fulfillment":
            common.update({
                "handlingTime": {"value": 1, "unit": "DAY"},
                "shippingOptions": [{"optionType": "DOMESTIC", "costType": "FLAT_RATE",
                    "shippingServices": [{"sortOrder": 1, "shippingCarrierCode": "USPS",
                        "shippingServiceCode": "USPSPriority", "shippingCost": {"value": "0.00", "currency": "USD"},
                        "freeShipping": True}]}],
            })
        elif kind == "return":
            common.update({"returnsAccepted": True, "returnPeriod": {"value": 30, "unit": "DAY"},
                           "returnShippingCostPayer": "BUYER"})
        return common

    def _location(self):
        path = f"/sell/inventory/v1/location/{quote(LOCATION_KEY, safe='')}"
        try:
            location = self.client.request("GET", path)
        except EbayError as error:
            if error.status != 404:
                raise
            payload = {
                "location": {"address": {"addressLine1": "2025 Hamilton Avenue", "city": "San Jose",
                    "stateOrProvince": "CA", "postalCode": "95125", "country": "US"}},
                "locationTypes": ["WAREHOUSE"], "name": "Vintage Ops Sandbox Main",
            }
            try:
                self.client.request("POST", path, json=payload, expected=(204,))
            except EbayError as create_error:
                # The key makes create naturally idempotent; confirm an uncertain
                # or racing create rather than issuing a second create request.
                if create_error.kind not in ("remote", "conflict"):
                    raise
            location = self.client.request("GET", path)
        if location.get("merchantLocationStatus") == "DISABLED":
            self.client.request("POST", f"{path}/enable", expected=(204,))
        return LOCATION_KEY
