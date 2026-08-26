# Railway operational readiness and recovery record

## Environment/resource contract (mandatory)

Sandbox and live are separate Railway **projects** (not merely variable scopes). Each has its own web and cron services, domain, `/data` volume, SQLite file, uploads directory, private backup bucket, bucket access principal, object prefix, token/backup encryption keys, Flask key, seller credentials and session generation, Stripe key/webhook endpoint secret, eBay application/token/seller/policies, and maintenance/backup bearer tokens. Shared variables, Railway `${{other-service.VAR}}` references, public buckets, and credentials capable of reaching both environments are prohibited. An inventory review must compare project/service IDs, domain, volume ID, bucket/prefix, Stripe account/mode, and eBay seller/environment before every activation.

Never clone/promote the Sandbox volume or database, encrypted eBay token rows, cookies, seller setup, or backup objects into live. Build live with an empty mounted directory and run reviewed, version-controlled migrations. Enter/validate live seller setup and provider authorization independently. Checkout and listing kill switches start false.

SQLite has one process and one web replica. Horizontal replicas, rolling/overlapping deployments, and a second service mounting the volume remain disabled. Startup owns an advisory writer lock and fails closed. `journal_mode=DELETE`, `synchronous=FULL`, foreign keys, a 30-second busy timeout, and SQLite's backup API are deliberate. WAL is not enabled: enabling it requires checkpoint-under-load, Railway-volume durability, online backup and full restore tests first. Alert at 60% volume or 5 GB DB, page at 75%/10 GB; migrate to managed PostgreSQL before 80%, sustained >250 ms write-lock p95, >5 writes/s, any replica requirement, or the backup window exceeding 15 minutes.

## Monitoring and response

`/healthz` is process-only. `/readyz` performs no provider calls and checks a DB read, exact migration set, writable DB/upload paths, and production activation configuration. Logs/metrics are structured allow-listed aggregates only. Never log names/emails, addresses/phones, tracking, seller notes, filenames or image bytes, raw provider bodies, cookies/tokens, or Authorization headers.

External monitoring polls web availability and `/readyz`, and alerts on sustained 5xx/checkout failures (5 minutes), any webhook signature failure, unmatched/failed webhook oldest age (10 minutes), every manual-resolution payment, eBay reconciliation failure/staleness (15 minutes), attention-case age (24 hours), maintenance lease/success age (10 minutes), and backup success age (26 hours). Primary owner: operations on-call; escalation: store owner, then payment/marketplace provider support and Railway support. Payment/manual-resolution and outage acknowledge in 15 minutes; stale jobs in 30 minutes; privacy/security in 15 minutes. Disable `CHECKOUT_ENABLED` for payment/webhook uncertainty and `EBAY_LISTING_ENABLED` for marketplace uncertainty.

## Privacy register

| Data | Purpose | Retention |
|---|---|---|
| order contact/address | fulfilment, fraud/disputes | 2 years, then anonymize |
| notes, attention summaries, resolution evidence | support/audit | 2 years; evidence is minimal provider state/amount/IDs only |
| tracking and revisions | delivery/disputes | 2 years, then clear |
| eBay order lines | stock reconciliation | minimal IDs/state/amount projection; 7 years |
| operational logs | security/reliability, no customer fields | 30 days |
| encrypted backups | recovery | 7 daily + 4 weekly, then object expiry |
| non-identifying totals/provider references | tax/accounting | 7 years |

The bounded maintenance job is idempotent and clears expired contact/address/tracking and notes while preserving totals. Deletion propagates by retention expiry: encrypted immutable backups are never edited. Access/correction/export/deletion requests are logged by the owner, verified using the order email plus reference, searched across orders/support systems, fulfilled within 30 days, and exceptions documented. Status capability URLs require the order email as an additional factor for disclosure/change requests.

## Restore procedure

1. Declare incident, disable checkout/listing, remove public domain/traffic, and stop web plus both cron services. Record last good backup time/object/version and incident time (RPO).
2. Detach and preserve the damaged volume read-only; never restore over it. Snapshot it and record IDs/checksums.
3. Create a new volume on an isolated staging service with no domain, cron, or provider mutation credentials. Fetch the immutable/versioned private object with least-privilege credentials and retained historical backup key from escrow.
4. Use the restore command into an empty staging directory. It authenticates AES-256-GCM, validates remote/plaintext hashes, safe regular archive members, every manifest size/SHA-256, and SQLite integrity. Keep modes 0700/0600.
5. Point staging at restored DB/uploads, run reviewed migrations, then verify migration list, DB integrity/read-write, photo presence/rendering, order totals/status/history, seller authentication, and decryption of an eBay token using current or retained historical token key. Provider calls remain mocked/disabled.
6. Record counts, sampled IDs (not personal data), screenshots/check output, timestamps, operator/reviewer, source object version, key version, achieved RPO and RTO. Take a pre-cutover backup.
7. Attach the new volume to a single live service, validate `/readyz`, rotate sessions/job tokens if exposed, then cut domain traffic. Enable listing and checkout separately only after provider identity validation.
8. Observe errors/webhooks/reconciliation. Roll back by disabling switches/traffic and reattaching the preserved old volume; never merge diverged SQLite files.

Railway outage: freeze provider mutations, preserve volume, follow Railway status/support, recover to a new service/volume and DNS only after readiness. Stripe incident: stop checkout, retain/replay signed webhooks and manually resolve ambiguous payments before release. eBay incident: stop listings/reconciliation mutations, retain reservations, and reconcile after identity/API recovery.

## Drill evidence and launch gates

A Sandbox drill and a synthetic-data live-environment drill (provider mutations disabled) are mandatory before activation and quarterly thereafter. Record date, operators/reviewer, object/version, key version/custodians, source/restore timestamps, measured RPO/RTO, integrity/hash output, migrations, authentication/photo/order/token checks, rollback result, and evidence location. **Current state: not yet performed; live activation is blocked.** Target RPO 24 hours and RTO 2 hours. Backup bucket versioning plus object lock/immutability and lifecycle retention must be verified. Backup key custodians are store owner and operations lead in separate audited password-manager vaults; retain each historical key until its last encrypted object expires plus 30 days.

The isolated load gate exercises concurrent checkout creation, webhook bursts, seller writes, eBay reconciliation, reservation cleanup, and backup initiation. Capture p50/p95/p99 latency, SQLite busy/lock waits, job/backup duration, 5xx/error rate, and checkout disruption; require zero inconsistent media/hash failures, <1% errors, p95 checkout <1 s, no lock timeout, and backup <15 min. Credential egress tests must prove each cron/domain/bucket/provider credential reaches only its intended environment. Attach results here before live activation.
