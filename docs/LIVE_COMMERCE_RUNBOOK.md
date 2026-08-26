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
