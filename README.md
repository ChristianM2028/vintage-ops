# Vintage Ops

A deliberately small local Flask/SQLite seller application for preparing clothing inventory and publishing it to eBay. It uses one Python runtime, server-rendered pages, encrypted seller tokens, and no browser-side credentials. A narrow adapter boundary keeps the real eBay integration isolated.

## Stack and setup

Prerequisites: Python 3.11+ and an eBay Developers Program Sandbox account.

```sh
cp .env.example .env
make setup
set -a; . ./.env; set +a
make migrate
make run
```

The example defaults to `MARKETPLACE_MOCK_MODE=true`. Visit `/` to create an item with local photos, then use `/inventory` to list or delist it against the network-free mock. Uploaded media lives in `instance/uploads`; its directory, per-file size limit, extensions, and photo-count limit are configurable with `UPLOAD_DIRECTORY`, `MAX_UPLOAD_SIZE`, `PERMITTED_IMAGE_EXTENSIONS`, and `MAX_PHOTO_COUNT`. `.env`, uploaded media, SQLite databases, and all of `instance/` remain ignored.

Set `MARKETPLACE_MOCK_MODE=false` only when you intend to use the preserved real eBay OAuth/Inventory API flow below. Local uploads are intentionally not public HTTPS URLs, so real publication rejects them. Use the secondary `/sandbox` development route with a suitable externally reachable HTTPS photo URL when exercising real eBay Sandbox.

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

## Automatic Sandbox seller setup

After authorizing the Sandbox seller, open `/sandbox` and click **Set up Sandbox seller**. The setup is deliberately restricted to `EBAY_ENVIRONMENT=sandbox` and `EBAY_US`. It uses the seller OAuth grant to:

* inspect the Account API `SELLING_POLICY_MANAGEMENT` privilege and opt in only when needed;
* reuse distinctively named Vintage Ops fulfillment, payment, and return policies, or create sensible Sandbox defaults and confirm them by querying again; and
* reuse `vintage-ops-main`, or create the US warehouse location and enable it when needed.

The four resulting identifiers are persisted for the authorized setup (an API-provided seller identity is stored when eBay supplies one); OAuth credentials are never part of this record. Repeated submissions re-query and reuse the same resources. Do not manually copy policy IDs from Seller Hub or enter location/policy values in the form. See the official [Account API](https://developer.ebay.com/api-docs/sell/account/overview.html) and [Inventory locations](https://developer.ebay.com/api-docs/sell/inventory/resources/location/methods/createInventoryLocation).

The remaining per-item prerequisites still come from eBay's category contracts:
* Find an appropriate **leaf** clothing category through Taxonomy API `getCategorySuggestions`/category tree calls. Check category aspects and accepted conditions through Taxonomy and Metadata APIs. Enter the actual category ID and Inventory API condition enum; the example intentionally does not pretend a value is universally valid. See [Taxonomy API](https://developer.ebay.com/api-docs/commerce/taxonomy/overview.html) and [Metadata API](https://developer.ebay.com/api-docs/sell/metadata/overview.html).
* Provide externally reachable public HTTPS image URLs. URLs requiring cookies/authentication, local URLs, placeholders, and unsupported image content will be rejected. Review eBay's [Inventory item method](https://developer.ebay.com/api-docs/sell/inventory/resources/inventory_item/methods/createOrReplaceInventoryItem) requirements.

## Lifecycle and recovery

The Sandbox form makes an obviously fake local garment and uses the persisted automatic setup values, then performs the official Inventory API sequence:

1. `PUT /sell/inventory/v1/inventory_item/{sku}` creates or replaces inventory. The stable local SKU is persisted as the eBay SKU first.
2. `POST /sell/inventory/v1/offer` creates the fixed-price offer. Its ID is persisted **immediately**.
3. `POST /sell/inventory/v1/offer/{offerId}/publish` publishes it and persists the listing ID/state.

The item page can retry listing, update inventory plus the offer with `PUT`, or withdraw through `POST .../withdraw`. Identifiers are preserved. Withdrawal is locally idempotent and local state changes only after remote confirmation. Refer to the official [Inventory API offer methods](https://developer.ebay.com/api-docs/sell/inventory/resources/offer/methods/createOffer), [publishOffer](https://developer.ebay.com/api-docs/sell/inventory/resources/offer/methods/publishOffer), and [withdrawOffer](https://developer.ebay.com/api-docs/sell/inventory/resources/offer/methods/withdrawOffer).

Each completed remote stage writes an explicit local state. If publishing fails after offer creation, `partial_failure` retains the offer ID; **List / retry** replaces inventory and publishes that same offer rather than duplicating it. Updating a field eBay locks after publication returns `relist_required`: withdraw and then create/relist as eBay directs. Do not delete the database while recovering a partial operation.

## Troubleshooting

* **Not authorized / reauthorization required:** authorize again; verify App ID, Cert ID, RuName, and exact accepted callback URL. Changing the encryption key makes existing tokens unreadable and requires reauthorization.
* **Missing prerequisite:** run **Set up Sandbox seller**, then supply a valid leaf category and condition for the fake item.
* **Invalid item:** validate leaf category, condition, required aspects, currency, quantity, and public HTTPS photos against the category metadata.
* **Conflict / relist required:** eBay restricts edits based on offer/listing state. Withdraw before changing locked data, then relist.
* **Partial publication:** keep the saved SKU/offer ID and use retry. The structured error on the item page includes safe error IDs, domain, category, parameters, and eBay request/correlation ID without tokens.
* **Timeout/remote error:** retries are safe after checking the displayed partial state. The HTTP timeout defaults to 15 seconds and can be changed with `EBAY_TIMEOUT_SECONDS`.

The development lifecycle route returns 404 outside Sandbox, preventing this test tool from targeting production. Production support would require a separate reviewed UI and configuration rather than changing this guard.

## Seller lifecycle failure policy

“List Everywhere” calls only adapters present in the implementation registry (currently eBay); the application does not manufacture states or identifiers for other marketplaces. “Sold / Delist Everywhere” attempts withdrawal for every implemented marketplace whose item is published. It marks the local item `Sold` only after all withdrawals succeed. If any withdrawal fails, the item is not marked sold, safe structured details are persisted in `last_error`, and the seller sees an actionable failure rather than a false all-delisted message.

## Production eBay checklist

Production requires a separate production keyset and an authorized production seller. Configure the production App ID as `EBAY_CLIENT_ID`, production Cert ID as `EBAY_CLIENT_SECRET`, the registered OAuth redirect identifier (RuName) as `EBAY_RUNAME`, and its **exact registered callback URL** as `EBAY_CALLBACK_URL`. Set `EBAY_ENVIRONMENT=production` and `MARKETPLACE_MOCK_MODE=false`. Keep stable, randomly generated `FLASK_SECRET_KEY` and Fernet `TOKEN_ENCRYPTION_KEY` values.

Set `EBAY_MARKETPLACE_ID` and `EBAY_CURRENCY`, an enabled `EBAY_MERCHANT_LOCATION_KEY`, and fulfillment, payment, and return policy IDs owned by the same seller and marketplace. The seller must have production selling privileges, be enrolled in Business Policies, and authorize the base, Inventory, Account, and Fulfillment scopes. Existing grants made before Fulfillment was added must be reauthorized.

Local storefront files remain canonical. For real eBay listings the application uploads them directly to eBay Picture Services with the item's persisted Sandbox or Production OAuth configuration; EPS identities and URLs are tracked separately from local filenames.

Vintage Ops uses scheduled Fulfillment API reconciliation rather than browser automation. Invoke the seller reconciliation action periodically and ensure the production account has the Fulfillment grant. If notifications are added, separately configure the eBay notification destination/account challenge-response requirements; notifications do not remove the need for idempotent order-line processing.

The integration follows the official eBay REST resources: Taxonomy category trees/suggestions/aspects, Metadata condition policies, Inventory create-or-replace inventory item and offer lifecycle methods, and Fulfillment `getOrders`. Metadata is cached for `EBAY_METADATA_CACHE_SECONDS`; a cached value remains usable during a transient refresh failure so seller edits are not discarded.

### Order-state policy

Fulfillment reconciliation records every `(orderId, lineItemId)` once. A `PAID` line decrements quantity atomically and hides zero-stock items immediately. Pending/failed payment is recorded but does not reserve or decrement local stock. Cancellation and refund data is retained for audit and synchronization, but Vintage Ops does not automatically relist or increase stock because return disposition may be unknown; the seller reviews and edits inventory explicitly. Multi-quantity paid lines decrement by the reported quantity, and unknown SKUs/listing IDs are retained and counted for manual resolution.
