# Live commerce launch contract and staff runbook

## Approved narrow pricing policy
Launch prices are **tax-inclusive**, with **shipping included at a fixed $0.00**, in USD, and checkout is limited to the configured US states (initially California). Stripe automatic tax is explicitly disabled. Business/legal must document approval and set `PRICING_LEGAL_REVIEWED=true` before production can boot. Do not activate providers or store credentials in source control.

## Incident procedures
1. **Stripe authorization/capture ambiguity:** disable new checkout, leave webhooks and refunds running, retrieve the PaymentIntent, compare its amount/currency to the immutable total, and capture/cancel only through the existing manual-resolution action.
2. **eBay publication/withdrawal ambiguity:** disable listings, inspect the persisted offer (never create another), withdraw in Seller Hub if necessary, then record the reviewed outcome.
3. **Oversold inventory:** stop capture, cancel the storefront authorization, preserve both channel records, and route any captured case to refund/manual resolution.
4. **Unmatched/retried webhooks:** retain the event, correlate by persisted session/PaymentIntent/order IDs, and retry idempotently; never infer ownership from an ID prefix.
5. **Stale reservations:** run only the bounded maintenance job; protected authorized/capture/manual-review reservations must not expire.
6. **Refunds:** compare the provider-confirmed captured amount/currency, use the idempotent full-refund workflow, and leave ambiguous outcomes in manual resolution for retrieval.
7. **Production eBay policy changes:** disable listings and reconciliation, run the read-only validator, obtain review, persist a new activation, and resume only when every identity/policy field matches.
8. **Kill switches:** `CHECKOUT_ENABLED=false` and `EBAY_LISTING_ENABLED=false` block only new work. Keep the application, Stripe/eBay webhooks, refunds, withdrawals, reconciliation recovery, and scheduled reservation cleanup online.

Production order reconciliation has one owner: the `/internal/scheduled-maintenance` Railway cron. Do not also run the CLI or seller UI scheduler for that environment. The cron has no volume and receives only `SCHEDULED_JOB_URL` and `SCHEDULED_JOB_TOKEN`.

## Authoritative provider selection

Set `STRIPE_PROVIDER_MODE` (`fake`, `test`, or `live`) and `EBAY_PROVIDER_MODE`
(`mock`, `sandbox`, or `production`) together with the matching
`APP_ENVIRONMENT`. These are the only provider selectors. The deprecated
`STRIPE_FAKE_MODE`, `MARKETPLACE_MOCK_MODE`, and `EBAY_ENVIRONMENT` variables
may be retained temporarily only when their values agree; startup rejects a
conflict. `SELLER_USERNAME` is a bounded non-secret identifier, not a credential
or entropy-bearing secret; continue protecting `SELLER_PASSWORD_HASH` and all
provider keys.
# Reviewed commerce policy (fail closed)

1. Record a non-placeholder policy ID/version and obtain documented review.
2. Configure every `COMMERCE_*` value shown in `.env.example`; explicitly choose
   fixed/calculated shipping and fixed/automatic tax and approved destinations.
3. Keep both kill switches false while reviewing. Setting only
   `PRICING_LEGAL_REVIEWED=true` cannot make checkout available.
4. Run `flask validate-stripe-production` and
   `flask validate-ebay-production`. These checks are read-only and a failure or
   identity mismatch must stop deployment. Do not paste credentials into logs.
5. Perform the separate coordinated activation for the exact reviewed
   configuration, then deliberately enable the desired kill switch. Activation
   must never toggle either switch.
