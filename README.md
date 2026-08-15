# Vintage Ops

A deliberately small local Flask/SQLite application for preparing clothing inventory and publishing it to **eBay Sandbox**. It uses one Python runtime, server-rendered pages, encrypted seller tokens, and no browser-side credentials. All eBay code is isolated in `integrations/ebay/`; there is intentionally no premature marketplace abstraction.

## Stack and setup

Prerequisites: Python 3.11+ and an eBay Developers Program Sandbox account.

```sh
cp .env.example .env
make setup
set -a; . ./.env; set +a
make migrate
make run
```

Generate secrets rather than copying the development fallback:

```sh
python -c 'import secrets; print(secrets.token_urlsafe(48))'
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Put those values in `FLASK_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY`. The latter must remain stable or stored refresh tokens cannot be decrypted. `.env`, SQLite databases, token/session files, and `instance/` are ignored. Run tests with `make test`.

## eBay Sandbox application and OAuth

1. Create Sandbox keys and a Sandbox seller in the [eBay Developer Program](https://developer.ebay.com/signin?tab=register).
2. In **User Tokens**, add an eBay sign-in redirect URL. eBay calls its redirect identifier the **RuName**. Set `EBAY_RUNAME` to that identifier, and configure its accepted URL to exactly match `EBAY_CALLBACK_URL` (normally `http://127.0.0.1:5000/ebay/callback`). Set Sandbox App ID/Cert ID as `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET`.
3. Keep `EBAY_ENVIRONMENT=sandbox`. Visit `/sandbox` and click **Authorize Sandbox seller**. The app requests `sell.inventory`, `sell.account`, and the base OAuth scope. Random state is hashed in SQLite, expires after ten minutes, is single-use, and is validated before the code exchange.
4. Tokens are exchanged and refreshed only server-side. Refresh and short-lived access tokens are Fernet-encrypted at rest. A revoked/invalid refresh token produces a reauthorization instruction. Credentials, codes, and tokens are never rendered or logged.

See eBay's official [authorization-code grant](https://developer.ebay.com/api-docs/static/oauth-authorization-code-grant.html), [OAuth scopes](https://developer.ebay.com/api-docs/static/oauth-scopes.html), and [token endpoint](https://developer.ebay.com/api-docs/static/oauth-token-types.html) documentation.

## Seller prerequisites (do not guess values)

Before listing, confirm the Sandbox seller is eligible for the Inventory API and has selling privileges:

* Opt in to Business Policies and create fulfillment, payment, and return policies. Retrieve their IDs with Account API `getFulfillmentPolicies`, `getPaymentPolicies`, and `getReturnPolicies`, or copy IDs created in Seller Hub. Supply them in `.env` or the Sandbox form. See the official [Account API](https://developer.ebay.com/api-docs/sell/account/overview.html).
* Create and enable a merchant inventory location with Inventory API `createInventoryLocation`/`enableInventoryLocation`; supply its exact key. A location cannot be invented. See [Inventory locations](https://developer.ebay.com/api-docs/sell/inventory/resources/location/methods/createInventoryLocation).
* Set the intended marketplace and its currency (for example `EBAY_US`/`USD`). Ensure every policy is for that marketplace.
* Find an appropriate **leaf** clothing category through Taxonomy API `getCategorySuggestions`/category tree calls. Check category aspects and accepted conditions through Taxonomy and Metadata APIs. Enter the actual category ID and Inventory API condition enum; the example intentionally does not pretend a value is universally valid. See [Taxonomy API](https://developer.ebay.com/api-docs/commerce/taxonomy/overview.html) and [Metadata API](https://developer.ebay.com/api-docs/sell/metadata/overview.html).
* Provide externally reachable public HTTPS image URLs. URLs requiring cookies/authentication, local URLs, placeholders, and unsupported image content will be rejected. Review eBay's [Inventory item method](https://developer.ebay.com/api-docs/sell/inventory/resources/inventory_item/methods/createOrReplaceInventoryItem) requirements.

## Lifecycle and recovery

The Sandbox form makes an obviously fake local garment, accepts verified category/condition/location/policy values, then performs the official Inventory API sequence:

1. `PUT /sell/inventory/v1/inventory_item/{sku}` creates or replaces inventory. The stable local SKU is persisted as the eBay SKU first.
2. `POST /sell/inventory/v1/offer` creates the fixed-price offer. Its ID is persisted **immediately**.
3. `POST /sell/inventory/v1/offer/{offerId}/publish` publishes it and persists the listing ID/state.

The item page can retry listing, update inventory plus the offer with `PUT`, or withdraw through `POST .../withdraw`. Identifiers are preserved. Withdrawal is locally idempotent and local state changes only after remote confirmation. Refer to the official [Inventory API offer methods](https://developer.ebay.com/api-docs/sell/inventory/resources/offer/methods/createOffer), [publishOffer](https://developer.ebay.com/api-docs/sell/inventory/resources/offer/methods/publishOffer), and [withdrawOffer](https://developer.ebay.com/api-docs/sell/inventory/resources/offer/methods/withdrawOffer).

Each completed remote stage writes an explicit local state. If publishing fails after offer creation, `partial_failure` retains the offer ID; **List / retry** replaces inventory and publishes that same offer rather than duplicating it. Updating a field eBay locks after publication returns `relist_required`: withdraw and then create/relist as eBay directs. Do not delete the database while recovering a partial operation.

## Troubleshooting

* **Not authorized / reauthorization required:** authorize again; verify App ID, Cert ID, RuName, and exact accepted callback URL. Changing the encryption key makes existing tokens unreadable and requires reauthorization.
* **Missing prerequisite:** fill every policy/location/category value with data belonging to the same Sandbox seller and marketplace; ensure the location is enabled and business policies are opted in.
* **Invalid item:** validate leaf category, condition, required aspects, currency, quantity, and public HTTPS photos against the category metadata.
* **Conflict / relist required:** eBay restricts edits based on offer/listing state. Withdraw before changing locked data, then relist.
* **Partial publication:** keep the saved SKU/offer ID and use retry. The structured error on the item page includes safe error IDs, domain, category, parameters, and eBay request/correlation ID without tokens.
* **Timeout/remote error:** retries are safe after checking the displayed partial state. The HTTP timeout defaults to 15 seconds and can be changed with `EBAY_TIMEOUT_SECONDS`.

The development lifecycle route returns 404 outside Sandbox, preventing this test tool from targeting production. Production support would require a separate reviewed UI and configuration rather than changing this guard.
