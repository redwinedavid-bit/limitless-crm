# Limitless Mechanical CRM — v1.6 staging hosting guide

**Status: staging preparation, NOT production deployment.** No Google Cloud, Render, or Google Workspace accounts have been connected; no public URL exists.

## Architecture

- Python FastAPI backend on managed HTTPS application hosting (Cloud Run or Render).
- Cloud SQL PostgreSQL on Google Cloud, or managed PostgreSQL on Render.
- Attached receipts and field photos require **durable private storage**. Cloud Run's local filesystem is ephemeral, so uploads are blocked until a secure storage design is implemented.
- Gmail OAuth and bank feeds remain disabled until user-driven authorization.
- Google Sheets is a one-time data migration source, not an application dependency.

## Cloud deployment safety requirements

Provide these through your cloud host's **secret/environment configuration**, never in the repository:

- `APP_ENV=staging`
- `DATABASE_URL=postgresql+psycopg://...` (prefer private database connectivity)
- `APP_PUBLIC_URL=https://<your-staging-host>` (the exact browser origin)
- `ALLOWED_HOSTS=<your-staging-host>` (no wildcard)
- `COOKIE_SECURE=1`
- `LMS_DURABLE_UPLOADS=0` until persistent private photo/receipt storage is installed.

The runtime rejects staging configurations that use SQLite, non-HTTPS origins, insecure cookies or wildcard host allowlists. Browser writes require an exact same-origin `Origin` header. This helps against cross-site requests but is **not a complete production security audit**.

## Packaging

Build from the `limitless-crm` directory. `Dockerfile` is usable by Google Cloud Run; the same container can be used on other hosts. `.dockerignore` intentionally prevents shipping customer data, SQLite databases, uploaded receipts, PDFs and dotenv secrets in a container image. Do not remove those exclusions.

The application creates its schema at startup for this staging build. **Production must use reviewed versioned migrations** instead of automatic `create_all`.

## Google Cloud deployment (desktop setup later)

1. In Google Cloud Console, enable billing and create a separate **staging** project.
2. Configure Cloud SQL for PostgreSQL and a locked-down connection. Keep Cloud Run and Cloud SQL in the same region.
3. Create a dedicated Cloud Run service account with only required Cloud SQL and Secret Manager permissions.
4. Build/deploy the container to Cloud Run, keeping private ingress / identity controls in place for staging. Set exact `APP_PUBLIC_URL` and `ALLOWED_HOSTS` once the final HTTPS hostname is known.
5. Populate the **empty** staging database using the import tool below. Do not include the source SQLite database in the deployment image.
6. Create individual employee test accounts on staging, then perform private testing. Do not enable payment sending or real Gmail sends until configured and verified.
7. Implement Cloud Storage private buckets with application-level access checks for files; do not rely on ephemeral Cloud Run storage.
8. Before go-live: MFA, robust rate limiting, CSRF and user authorization security review, background scheduled jobs, monitoring, versioned DB migrations and successful encrypted backup restoration.

Google Cloud is not automatically included in Google Workspace hosting. Billing must be enabled separately. Cloud Run can have low idle usage costs, but **Cloud SQL is a separately billed always-on managed database**.

Official docs:
- https://cloud.google.com/run/pricing
- https://cloud.google.com/sql/pricing
- https://firebase.google.com/docs/hosting/cloud-run

## Render alternative

Use the same container image and environment variables with a managed PostgreSQL database. Set `APP_PUBLIC_URL` to the exact staging URL. Database snapshots and private object storage remain separate. Avoid free ephemeral databases for business records.

## Import historical snapshot to EMPTY staging database

Use the private `data/limitless-imported.sqlite3` from the secured release archive only on a trusted operator machine or temporary controlled runner. Do not upload it to a public project or commit to Git.

```bash
# First run checks and counts only (no writes):
DATABASE_URL='postgresql+psycopg://...' python -m server.import_staging_snapshot --source data/limitless-imported.sqlite3
# Only when empty destination, ownership, and counts are confirmed:
DATABASE_URL='postgresql+psycopg://...' python -m server.import_staging_snapshot --source data/limitless-imported.sqlite3 --commit
```

This tool imports customer, location, job, purchase-order and labor history only, with original IDs and numbers. It explicitly excludes login sessions, passwords, real media files and any unreconciled billing ledger. It aborts on orphan relationships or existing destination records. Current snapshot counts: 31 customers, 136 locations, 253 jobs, 407 POs, 351 labor records. Final migration must recapture any post-snapshot transactions before retiring LMS Job Board.

## Outstanding blockers to production

- No live managed database or staging HTTPS URL has been created.
- No live multiuser browser or penetration testing has been performed.
- No production-grade MFA, global sign-in rate limits, or restore drills yet.
- Cloud Run durable private photo/receipt storage has not been implemented, so upload functions are deliberately blocked in hosted staging.
- Historical Wave invoices, payment history and job financials need reconciliation before production conversion.
- No real email, bank or payment processing is connected.
- Customer/contract/legal review needed for stored signatures and personally identifying billing data.
