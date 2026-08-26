# Post-launch product milestones

## Status, sequencing, and boundary

This roadmap begins only after the live-commerce launch gates in
`OPERATIONAL_READINESS.md` have passed and production has met an explicitly
recorded stability window. The owner must define that window before work starts;
the recommended minimum is 30 consecutive days with no unresolved severity-one
incident, restore and reconciliation objectives met, and no launch-blocking
manual-resolution backlog.

These milestones are **not** provider-activation work. No milestone may relax,
reuse, or make itself a prerequisite of `PRODUCTION_PROVIDER_ACTIVATION_ID`, the
Stripe/eBay identity checks, environment isolation, or the checkout/listing kill
switches. Each ships behind its own disabled-by-default feature flag, migration,
rollback plan, metrics, runbook, privacy/threat-model review, and staged cohort.
Provider credentials remain service identities; seller users never receive them.

The order aggregate is not a single state machine. Payment, fulfillment, refund,
inventory, marketplace publication, return, and notification each retain their
own state, version, timestamps, idempotency keys, and failure/manual-review state.
Transitions in one lifecycle may enqueue a command or fact for another but must
not directly overwrite its state or imply its success. A projection may describe
an order for the UI, but it is never authoritative for a component lifecycle.

## Milestone 1 — individual seller identity and accountable operations

Replace `SELLER_USERNAME`/`SELLER_PASSWORD_HASH` with individual, non-shared
seller identities before adding any other privileged workflow.

### Scope

* Store users with immutable internal IDs and normalized, unique login
  identifiers. Never use an email address as an audit identity.
* Require phishing-resistant MFA (WebAuthn/passkeys) where supported, with TOTP
  as a controlled fallback. Encrypt TOTP seeds, hash single-use recovery codes,
  rate-limit challenges, and require a recent MFA challenge for recovery,
  credential changes, refunds, privacy actions, and role administration.
* Start with least-privilege roles: `owner`, `operations`, `fulfillment`,
  `support`, and read-only `auditor`. Enforce permissions in the service layer,
  not only in routes or templates; deny by default and test the permission matrix.
* Back sessions with server-side records containing an opaque hashed token, user,
  issue/last-seen/absolute-expiry times, MFA assurance, and revocation time.
  Rotate on login and elevation; allow a user to revoke other sessions and an
  owner to revoke all sessions for a user. Retain `SESSION_GENERATION` only as
  the independent emergency global kill switch.
* Recovery uses expiring, single-use, purpose-bound tokens, generic responses,
  rate limits, a second verified channel or owner-reviewed process, and alerts to
  the affected user. Recovery never bypasses MFA enrollment or silently restores
  a privileged role.
* Offboarding atomically disables login, revokes sessions and recovery tokens,
  removes role grants and pending invitations, rotates any credential known by
  the person, and preserves audit history under the immutable user ID.
* Migrate with a one-time bootstrap owner invitation. Do not translate the shared
  password into a user credential. Disable and remove shared-login configuration
  only after bootstrap and rollback validation.

### Attribution contract

Append-only audit events record `event_id`, occurred/recorded times, authenticated
user ID, session ID, effective role/assurance, action, target type/ID, request or
operation ID, safe before/after field names or hashes, outcome, and source. Do not
store secrets, customer message bodies, addresses, raw provider payloads, or
recovery tokens. System/provider activity uses a distinct service actor and keeps
the initiating user ID when it is causally available.

The following mutations require an authenticated individual and an audit event:
listing create/edit/publish/withdraw, fulfillment decisions and label operations,
refund request/approval, seller-note create/edit/delete, tracking changes,
privacy export/correction/deletion, role/session changes, recovery, and
offboarding. Audit persistence and the business mutation share a local
transaction; outbound provider completion adds a correlated result event. Audit
records are access-controlled, integrity-monitored, retained by policy, and
exportable without exposing customer data.

### Exit criteria

No shared credential remains; concurrent users can revoke sessions without
affecting unrelated users; every listed mutation is attributable; permission,
MFA/recovery replay, offboarding, audit redaction, and emergency-revocation tests
pass; and the access/recovery/offboarding runbooks have been drilled.

## Milestone 2 — carrier boundary and fulfillment evidence

Introduce a carrier port independent of marketplace and payment adapters. Its
typed contract covers address/service validation, quoted service expiry, label
purchase with an operation idempotency key, label retrieval, tracking
normalization, void/refund request plus reconciliation, and tracking lookup.
Carrier-specific codes and states remain in adapter evidence; canonical states
are deliberately small and include `unknown`, `pre_transit`, `in_transit`,
`out_for_delivery`, `delivered`, `exception`, and `returned`.

Persist shipments, packages, immutable label attempts, normalized tracking
observations, and raw-event digests separately from `fulfillment_state`. A label
purchase does not mean shipped; tracking does not mean fulfilled; `delivered`
comes only from a provider-backed observation and never from a seller toggle.
Duplicate purchases are prevented by the local operation key plus the carrier's
idempotency key. Ambiguous timeouts reconcile before retry. Void/refund has its
own pending/succeeded/failed state and never rewinds shipment or payment state.

Carrier webhooks must authenticate with the carrier's supported signature over
the original bytes, enforce timestamp/replay bounds, store the provider event ID
uniquely, and acknowledge only after durable receipt. Consumers are idempotent
and tolerate duplicates, delay, and out-of-order delivery; periodic polling
repairs missed events. Tracking and label data follow the privacy register and
must not enter logs or notification URLs.

Exit requires adapter contract tests (including a fake), service/parcel
validation, purchase ambiguity and replay tests, signed-event fixtures, polling
repair, void/refund reconciliation, provider-backed delivery evidence, and a
carrier-outage runbook. The first real carrier adapter is a separately releasable
slice behind its own flag.

## Milestone 3 — partial refunds and return disposition

### Provider-authoritative partial refunds

Represent a refund request separately from allocations and provider refund
attempts. Amounts are integer minor units in the payment currency. Every request
has a client idempotency key scoped to the order; every provider attempt has a
stable provider idempotency key. Allocation rows uniquely identify the order
line, shipping, tax, or explicit adjustment being refunded and cannot be applied
twice.

Before submission and after every webhook/reconciliation, retrieve or consume a
signed provider view and calculate:

`remaining refundable = provider captured total - provider cumulative successful refunds`

Reject a request above that amount and never infer the cumulative total by
summing only local attempts. Persist the provider object/version and observed
total used for the decision. Pending or ambiguous calls reserve their allocation
but do not count as successful; reconcile them before retry. Concurrent requests
serialize per payment, and currency/total/allocation invariants hold in the same
transaction. Disputes and provider-side refunds enter manual review rather than
being overwritten. `refund_state` does not change payment, inventory,
fulfillment, marketplace, return, or notification state.

### Receipt-gated return disposition

A return is its own lifecycle: `requested`, `authorized`, `in_transit`,
`received`, `assessed`, and `closed`, with explicit cancelled/rejected/manual
review paths. The system must not restock or relist from a refund, return label,
tracking event, or customer assertion.

An authorized receiver records physical receipt time, package/return identity,
and minimal evidence. A different explicit assessment records quantity and a
controlled condition/disposition per unit: restock, relist after edits/repair,
quarantine, recycle/donate, or other manual review. Only units with confirmed
receipt and an approved saleable assessment may emit an idempotent inventory
adjustment. Relisting is a later, separately authorized marketplace command and
never follows automatically from restocking. Damaged/missing/swapped quantities
remain quarantined; assessment corrections are revisions, not destructive edits.

Exit requires concurrent and replayed partial-refund tests, external-refund
reconciliation, exact allocation invariants, separate approval controls for
high-value refunds, receipt/assessment authorization tests, and proof that every
refund/return permutation leaves unrelated lifecycle states unchanged.

## Milestone 4 — transactional notification outbox

Write a minimal, versioned notification intent to an outbox in the same local
transaction as an eligible order fact. A unique semantic key such as
`order_id + event_type + event_version + recipient_purpose` prevents duplicate
mail. Delivery workers lease rows, retry with capped exponential backoff, and
record provider message IDs and sanitized outcomes. A dead-letter state and
operator replay preserve the same semantic key. Notification failure never
rolls back or advances an order lifecycle.

Authenticate dedicated sender domains with SPF, DKIM, and DMARC before enabling
delivery. Verify signed delivery/bounce/complaint events, deduplicate them, and
maintain address-level hard-bounce/complaint suppressions. Soft bounces use a
bounded retry policy. Transactional, operational, and optional marketing
preferences are separate; mandatory transactional messages remain minimal and
cannot be repurposed as marketing.

Templates contain only the order reference, coarse event description, store
contact, and a status link—no address, item details, tracking number, refund
reason, seller note, or sensitive provider data unless strictly necessary and
approved by privacy review. Status links carry random high-entropy capabilities
stored only as hashes, expire quickly, are single-purpose and revocable, reveal
the least data needed, require an additional factor before sensitive disclosure
or mutation, and never grant a customer account session. Referrers, caches,
analytics, and logs must not leak tokens.

Exit requires outbox crash/replay and ordering tests, authenticated-domain
evidence, signed feedback-event tests, suppression/preference enforcement,
template privacy snapshots, expired/revoked/wrong-purpose link tests, and a
provider-outage/dead-letter runbook.

## Conditional milestone — customer accounts

Customer accounts are not scheduled by default. Status capabilities and verified
support remain the baseline. Product and privacy owners may approve discovery
only when measured demand shows repeated account-dependent tasks that cannot be
met safely with purpose-bound links. The decision record must include research,
support-volume data, alternatives, data inventory/retention, consent and account
deletion/export behavior, abuse and account-takeover threat models, age/region
requirements, accessibility, and operating cost.

If approved, customer identity, sessions, MFA/recovery, preferences, and profile
data are separate from seller identity and from commerce lifecycle records.
Account deletion must not delete legally required transaction records, and order
claiming requires verified control without leaking whether an order or email
exists. If demand or privacy review does not justify the data and risk, record a
no-build decision and continue improving capability links.

## Cross-milestone release rules

For every slice, publish a transition table declaring the owning aggregate,
allowed actors, idempotency boundary, authoritative evidence, emitted facts, and
states it is forbidden to mutate. Use transactional outboxes rather than
cross-aggregate database writes. Consumers keep inbox/deduplication records and
must tolerate duplicate and out-of-order facts. Never use a notification,
tracking event, refund, or return as proof of another lifecycle's success.

Migrations are additive and backward compatible until rollback expires. Feature
flags are independent (`SELLER_IDENTITY_V2`, `CARRIER_LABELS`,
`PARTIAL_REFUNDS`, `RETURN_DISPOSITION`, `ORDER_NOTIFICATIONS`, and, only after
approval, `CUSTOMER_ACCOUNTS`); none aliases an activation or another milestone's
flag. Rollout metrics are lifecycle-specific, while correlation IDs allow an
operator to reconstruct causality. Backups, retention jobs, exports, deletion,
redaction, access controls, observability allow-lists, and disaster-recovery
drills must cover each new table and secret before cohort expansion.
