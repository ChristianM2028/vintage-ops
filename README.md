# Vintage Ops

A deliberately small local Flask/SQLite seller application for preparing clothing inventory and publishing it to eBay. It uses one Python runtime, server-rendered pages, encrypted seller tokens, and no browser-side credentials. A narrow adapter boundary keeps the real eBay integration isolated.

## Railway Sandbox deployment (not for live commerce)

> **Sandbox only:** this checked-in Railway configuration must never be used with
> live Stripe credentials or production eBay. Use only `sk_test_` Stripe keys and
> keep `EBAY_ENVIRONMENT=sandbox`; the reconciliation command refuses production.

Provision one persistent Railway volume mounted at `/data`. The checked-in
configuration fixes `DATABASE_PATH=/data/vintage_ops.sqlite3` and
`UPLOAD_DIRECTORY=/data/uploads`, enables exactly one trusted Railway hop for
forwarded scheme and host, and checks `GET /healthz`. Railway supplies `PORT`;
do not define or hard-code it. Gunicorn runs one worker (required for this SQLite
deployment), four threads, a 60-second timeout, and a 30-second graceful timeout.

Configure these Railway variables/secrets: a strong random `FLASK_SECRET_KEY`, a
stable Fernet `TOKEN_ENCRYPTION_KEY`, `SELLER_USERNAME`, a Werkzeug-generated
`SELLER_PASSWORD_HASH`, `SESSION_COOKIE_SECURE=true`, `PROXY_FIX_PROTO=1`,
`PROXY_FIX_HOST=1`, `EBAY_ENVIRONMENT=sandbox`, Sandbox-only `EBAY_CLIENT_ID`,
`EBAY_CLIENT_SECRET`, `EBAY_RUNAME`, and `EBAY_CALLBACK_URL`, plus the applicable
marketplace/location/policy settings. Set `MARKETPLACE_MOCK_MODE=false`. For
Stripe use only `STRIPE_FAKE_MODE=false`, an `sk_test_...` `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, and public HTTPS `STRIPE_SUCCESS_URL` and
`STRIPE_CANCEL_URL` values containing `{ORDER_ID}`. Keep every credential in
Railway secrets, never deployment files.

In Stripe test mode, create a webhook at
`https://<railway-domain>/stripe/webhook`, select the events used by the app
(`checkout.session.completed`, `checkout.session.expired`, the relevant
PaymentIntent authorization/success/cancellation events, and refund events), and
store its signing secret only as `STRIPE_WEBHOOK_SECRET`. In the eBay Sandbox
application, register the exact `https://<railway-domain>/ebay/callback`; that
accepted callback, `EBAY_CALLBACK_URL`, and the Sandbox RuName in `EBAY_RUNAME`
must stay consistent.

Use one external Railway Cron trigger per task—never multiple overlapping
schedulers—and invoke bounded commands such as:

```sh
flask --app 'app:create_app()' storefront-maintenance --limit 100
flask --app 'app:create_app()' ebay-reconcile-orders --limit 100
```

There is deliberately no in-process scheduler or unauthenticated maintenance
endpoint. Both commands validate their 1–500 bound; provider/configuration errors
produce a nonzero exit status, and eBay automation is Sandbox-only.

Before migrations, retain and verify a backup. For a running database, use
SQLite's online backup API (for example Python `sqlite3.Connection.backup`) or
`VACUUM INTO` to create a consistent database copy; never copy the live database
file naively. Back up `/data/uploads` with it. To restore, stop the web service
and every Cron/automation process, replace the database and uploads together,
verify ownership and permissions, then start one web instance. Confirm `/healthz`,
seller login, inventory/photo access, schema migration completion, and safe CLI
dry operational results before resuming automation. Keep the verified pre-migration
backup until post-restore/application validation is complete.

## Stack and setup

Prerequisites: Python 3.11+ and an eBay Developers Program Sandbox account.

```sh
cp .env.example .env
make setup
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

Put those values in `FLASK_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY`. The latter must remain stable or stored refresh tokens cannot be decrypted. The application loads `.env` itself: **do not run `source .env`**, because shell expansion can corrupt dollar signs in values such as password hashes. `.env`, SQLite databases, token/session files, and `instance/` are ignored. Run tests with `make test`.

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
# Two-channel purchase operations

## Authority and independent lifecycles

Authority is deliberately split rather than inferred from whichever page was
visited last:

* the local SQLite item row is the authority for inventory, storefront
  visibility, reservations, and the application's checkout workflow;
* immutable server-side order snapshots are the authority for the amount,
  currency, quantity, item identity, and shipping details submitted to Stripe;
  browser fields and the success redirect are never payment evidence;
* Stripe's signed webhooks and explicit API reads are the authority for
  authorization, capture, cancellation, and refund outcomes; and
* eBay remains the authority for its listing and order state. Local eBay
  identifiers and reconciliation records make those remote operations
  retryable, but do not turn SQLite into the marketplace authority.

Checkout/payment, fulfillment, and refunds are separate lifecycles. A captured
sale starts fulfillment at `unfulfilled`; seller actions move it forward through
`packing`, `shipped`, and `delivered`. The customer-facing display progression is
**Paid → Packing → Shipped → Delivered**. Refund state independently moves through
`none`, `pending`, `refunded`, or `manual_resolution`; neither shipping nor a
refund rewrites the historical fact that payment was captured.

Tracking is entered manually by the seller when marking an order shipped and
corrections are retained in an audit history. Vintage Ops does not buy a label,
call a carrier, validate tracking, poll delivery, or infer delivery from tracking;
**Delivered is also a manual seller confirmation**. Treat both values as staff
records, not carrier-backed proof.

## Cancellation, refunds, and attention

Before capture, cancellation means canceling/releasing the Stripe authorization
and retaining the inventory reservation until the result is known. After capture,
the payment cannot be “cancelled”: the seller must issue a refund. Vintage Ops
supports only an exact **full refund** of the persisted captured amount and
currency. Partial refunds are explicitly deferred and must not be approximated
by changing a local amount.

Issuing a refund after the order is `shipped` or `delivered` requires an additional
seller confirmation because the item has left the business. A refund has no
inventory or fulfillment side effects: cancellation/refund never automatically
increases quantity, makes the item visible on the storefront, or relists it on
eBay. Return condition and physical possession must be reviewed before the seller
manually changes inventory or listings.

An attention acknowledgement means only “a seller has seen this case.” It does
not resolve the underlying operation and is not provider-backed evidence. A case
is resolved only after the Stripe/eBay state has been safely established and the
corresponding reconciliation action records that outcome.

### Safe Stripe refund rechecks

Use **Recheck known refund** only for a `pending` or `manual_resolution` refund
that already has a persisted Stripe Refund ID. The recheck retrieves that exact
Refund; it does not create another refund. Before acting, confirm in the Stripe
Dashboard that the PaymentIntent, Refund ID, amount, currency, mode/account, and
order metadata match the local order. Never paste or substitute an ID, retry the
original refund button merely because a request timed out, or use a charge's
aggregate refunded amount as proof of one full refund. Let signed refund webhooks
converge normally. Identifier, amount, currency, status, or event-order
contradictions must remain in `manual_resolution` for provider-backed review.

The local `items` row remains canonical inventory. Website checkout first acquires
an expiring SQLite reservation in a short `BEGIN IMMEDIATE` transaction, then
creates Stripe Checkout only after commit. Checkout uses manual capture: the
state progression is `checkout_pending` → `authorization_pending` → `authorized`
→ `ebay_withdrawal_pending` → `capture_pending` → `paid` → `sold`. Cancellation,
expiry, unavailability, and `manual_resolution` are explicit terminal/review
paths. Remote Stripe and eBay calls are never made while a database transaction
is open, and stable order-derived idempotency keys protect create, capture, and
cancel operations.

Reservations are normally valid for `STOREFRONT_RESERVATION_SECONDS`. Schedule a
bounded POST to `/maintenance/storefront` (authenticated and CSRF protected) and
durable operation retries. A failed, confirmed checkout creation may release
stock; an ambiguous withdrawal, capture, or cancellation must retain stock and
be reviewed rather than optimistically reopening it. Stripe webhooks must be
registered at `/stripe/webhook`; their raw bodies are signature verified and
event IDs are atomically deduplicated.

## Configuration and local testing

Generate `SELLER_PASSWORD_HASH` with Werkzeug's `generate_password_hash`; paste
the complete hash, including its `scrypt:` prefix and every `$`-delimited section,
and never store a plaintext password. A malformed configured hash is treated as
an authentication configuration failure without displaying the hash or password.
Set `SELLER_USERNAME`, a strong `FLASK_SECRET_KEY`,
and enable `SESSION_COOKIE_SECURE` behind HTTPS. Set `STRIPE_FAKE_MODE=true` for
network-free development. Production uses `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, HTTPS success/cancel URLs, plus the existing eBay
credentials. Browser submissions never determine amount, currency, quantity,
status, or provider identifiers; those values come from the canonical item and
immutable order snapshots.

## Production checklist and conflict boundary

SQLite serializes local ownership decisions, but neither Stripe nor eBay can
participate in that transaction. Simultaneous external sales therefore cannot be
eliminated completely. Paid eBay reconciliation atomically hides/decrements the
item, records a website conflict, and durably schedules authorization
cancellation; an already possibly captured website payment enters prominent
manual resolution.

Production operation requires HTTPS, secure cookies, a reverse proxy and
production WSGI server, registered Stripe webhooks, real restricted Stripe/eBay
credentials, backups, monitoring/alerting, durable scheduled retries and
reservation cleanup, and documented staff procedures for every manual-resolution
case. Review cancellation, capture failure, eBay withdrawal failure, expiry,
retry, and cross-channel conflict queues routinely; a redirect success page is
never evidence of payment.

Coordinate migration execution with a maintenance window, a named operator, and
a verified database backup/restore plan; run migrations exactly once before the
new application processes traffic. Terminate HTTPS at a reviewed proxy, use HTTPS
for every public and webhook URL, and enable `SESSION_COOKIE_SECURE=true`. Restrict
Stripe and eBay credentials to the required account, mode, scopes, and operational
staff; keep secrets out of source control and rotate them under a documented
procedure. Back up both SQLite and required uploaded media, test restores, monitor
application/provider failures and queue age, and alert on stuck operations and
attention cases. Routinely review Stripe and eBay webhook-delivery dashboards for
non-2xx, delayed, disabled, or repeatedly retried deliveries and reconcile them
against local deduplication/audit records. Production migration and live
activation remain coordinated operational work, not an automatic application
feature.

## Stripe sandbox checkout procedure

1. Create or select a Stripe sandbox and obtain its `sk_test_` secret key.
2. Set `STRIPE_FAKE_MODE=false`, set `STRIPE_SECRET_KEY` to that test key, and
   configure `STRIPE_SHIPPING_COUNTRIES` (the example defaults to `US`). Live
   `sk_live_` keys are rejected at startup.
3. Run exactly one Stripe listener, connected to the same sandbox/account as
   `STRIPE_SECRET_KEY`, and forward it to `/stripe/webhook`. Copy its current
   `whsec_...` signing secret into `.env` as `STRIPE_WEBHOOK_SECRET`. Restart
   Flask whenever that secret changes.
4. Start Flask, publish a disposable item (mock withdrawal is supported), and
   buy it through Checkout with Stripe's official test card data.
5. Observe authorization, confirmed eBay withdrawal, capture, sold inventory,
   and the audit timeline in the authenticated `/orders` dashboard. Confirm the
   relevant deliveries in Stripe receive HTTP 200.
6. Test refunds with a separate disposable captured order. From the seller order
   page request the exact full refund, then use the Stripe Sandbox to exercise
   `refund.created`, `refund.updated`, `refund.failed`, and `charge.refunded`
   delivery/retry behavior. Confirm the listener receives HTTP 200, duplicate and
   out-of-order events converge without creating another refund, exact successful
   evidence reaches `refunded`, and mismatched/contradictory evidence opens
   attention or `manual_resolution`. Use **Recheck known refund** only after a
   Refund ID is stored, and verify that it reads that ID rather than issuing a new
   refund.

Migration `011_checkout_reliability.sql` preserves all prior rows and labels
their provider `legacy_unknown`; identifiers such as `cs_test_` are never used
to guess whether a session was locally faked. New orders persist `fake` or
`stripe_test`. A fake order may be released when deliberately switching to real
test Stripe. A missing/unreachable `stripe_test` or `legacy_unknown` session
retains its reservation and enters review rather than risking a second charge.

Withdrawal, capture, and authorization cancellation have separate durable,
leased per-order claims. Each ownership decision uses a short `BEGIN IMMEDIATE`
transaction, while provider calls occur only after commit. Expired claims and
failed webhook records can be retried; active duplicate delivery is distinguished
from completed work, and distinct event IDs converge on stable order-derived
idempotency keys. Ambiguous provider responses, conflicting Stripe identifiers,
post-capture inventory conflicts, and unclassifiable stale sessions intentionally
enter `manual_resolution`; they remain reserved for seller investigation and
webhooks return a retryable non-200 until resolved.

Checkout accepts cards only, uses manual capture, and collects email and a
shipping address. Shipping fees and tax pricing remain unfinished: the total is
only the server-side item-price snapshot. The Stripe SDK/API contract is pinned
by `requirements.txt`; webhook fixtures must be reviewed when that pin changes.

## Explicit non-goals

The current scope intentionally excludes:

* shipping-label purchase or generation;
* carrier API integrations and automatic delivery detection;
* calculated shipping and tax calculation/collection;
* partial refunds and exchanges;
* automatic restocking, storefront relisting, or eBay relisting;
* customer accounts and customer/seller notifications;
* multiple seller users, permissions, or seller roles;
* automated deployment or production/live activation; and
* a storefront redesign.
