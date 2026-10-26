# Limitless Mechanical CRM v1.10 — private testing update

Current deployment instructions, feature details and limitations are in the parent `README.md`. This source includes the simplified call workspace, owner pricing controls, customer lifetime history and repeat-system watchlist. It has not been deployed. The sections below describe earlier development milestones.

# Limitless Mechanical | Operations Hub v1.4 (local development)

## Version 1.4 — Technician mobile documentation, field photos, and customer acknowledgment

This release is an **incremental local-development build**, not a production application.
All existing v1.3 customer, job, purchase order, billing, and labor functions remain available.
**PO purchasing policy stays unchanged:** no manager approval; David = DR, Adrian = AR,
Danny = DB; one purchase order must reference exactly one existing job. Receipt uploads
remain in the purchasing interface.

### Field technician workflow

1. Sign in with a technician account and open `/tech`. Assigned work orders now link
   to **Open field service form** (`/field?work_order=<work_order-uuid>`).
2. **Work details:** enter customer concern, diagnosis, work performed, recommendations,
   and a follow-up flag. Save an editable draft.
3. **Equipment readings:** office first links the equipment to the work order; the
   assigned technician logs a numeric reading, named parameter, unit, timestamp and note.
4. **Field photos:** capture an image on the phone or select from Files/Photos, add a
   caption and optional equipment ID, and upload it to a private work-order attachment.
   JPEG/PNG/WebP/HEIC are accepted, up to 10 MB. Non-HEIC images are decoded/validated.
5. **Customer acknowledgment:** typed name/title plus a drawn signature on the mobile
   canvas. The technician must confirm the customer's acknowledgment before signing.
   The API rejects blank signatures and requires saved diagnosis and work performed.
6. Signing automatically marks the work order **Completed** and locks the field report,
   future measurements, new notes, photo uploads, and status updates. Corrections after
   signing require a new work order rather than overwriting the signed snapshot.
7. **Print/PDF:** `/api/field/work-orders/{id}/report.pdf` provides an authenticated,
   branded PDF with equipment readings, technician notes, photo thumbnails where supported,
   and customer acknowledgment. Before signing the PDF is visibly marked an unsigned draft.

The signature is an **acknowledgment of receiving service documentation**, not a
verified third-party digital identity, payment authorization, or automatically legally
binding electronic signature. The document includes a SHA-256 digest of the signed
report snapshot and a private signature image. The PDF may not embed HEIC images if
Pillow cannot decode them; original photos remain available in the CRM.

### Permissions, storage, and limitations

- Only the assigned technician can edit their work-order field documentation; owner and
  office can supervise unsiged drafts. Other roles are read-only as permitted.
- Field photos and signatures reside **outside public static routes** in
  `private_field_uploads/`, or `LMS_FIELD_UPLOAD_DIR` if set. Files must be kept on
  persistent storage and backed up alongside the database. The ZIP ships no real
  customer photos or signatures.
- Equipment readings require an equipment asset attached to the correct work order,
  so a measurement cannot be attributed to an unrelated location.
- Work-order and photo content is delivered only through authenticated API routes.
- Offline field drafts/photo queues, customer portals, identity verification,
  full photo malware scanning, hardened online login, object storage, CSRF protection,
  infrastructure rate limiting, and encrypted production backups are **not** complete.
  Do not publish this version to the internet or use it as the official business system.
- Local schema creation uses SQLAlchemy `create_all`; production requires versioned
  migration scripts, PostgreSQL validation and a formal security review.

### v1.4 validation

`15 passed` using a **disposable empty SQLite database**, including v1.3 regression
coverage and the new end-to-end field work-order/photo/signature/PDF test. The
imported company database shipped with this build was not used for automated tests.
The mobile HTML was visually checked at iPhone width, and a sample service PDF was
rendered and inspected.

## What this release does

- Retains the existing charcoal + deep-teal desktop/mobile UI.
- Adds a FastAPI backend and SQLAlchemy relational models for customers, locations, jobs, purchase orders, labor entries, and audit events.
- Uses a local SQLite database by default; set `DATABASE_URL` to a PostgreSQL SQLAlchemy URL for a PostgreSQL instance.
- Browser automatically switches to API mode when run through the backend server. Customer, job and PO creation use real API endpoints.
- Enforces **exactly one existing job per PO** through a non-null foreign key, database constraints and server checks. PO number uniqueness also enforced.
- Includes a read-only-from-source CSV migration utility for the **active LMS Job Board only**, with dry-run by default and exception reporting.
- Separates historical material totals from historical PO detail in job costing to avoid double counting.

## Start locally

Install Python 3.11+ and create a virtual environment, then:

```bash
pip install -r requirements.txt
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Visit `http://127.0.0.1:8000` on the same computer. This is **not** a secured production deployment. Do **not** bind to 0.0.0.0 or port-forward to the public internet. For access from other devices, implement authentication and HTTPS first.

The bundled `limitless-dev.sqlite3` is a clean local copy of the original LMS Job Board import, with 31 customers, 253 jobs, 407 purchase orders and 351 labor records. No test-created customers, invoices or user accounts are included. Create your own owner account before login.

## Importing the active LMS Job Board

Export the following tabs from the **active LMS Job Board** as individual CSVs (other boards are explicitly excluded):

- `Customer.csv`
- `Jobs Open_Costing.csv` (renamed export of the `Jobs Open/Costing` tab)
- `David POs.csv`, `Adrian POs.csv`, `Danny POs.csv`
- `David Hrs Mileage.csv`, `Adrian Hrs Mileage.csv`, `Danny Hrs Mileage.csv`

Put them into a single folder, and run a **dry run first**:

```bash
python -m server.import_job_board /path/to/csv-folder --report migration-report.json
```

Review `migration-report.json` for missing job references, questionable mileage, and other exceptions. Only then run:

```bash
python -m server.import_job_board /path/to/csv-folder --commit --report migration-committed.json
```

The script never writes to Google Sheets. It can be re-run without duplicating records already imported by their IDs. The source spreadsheet has not been automatically imported into this ZIP. Do not treat historical PO receipts as independently verified receiving events: they are backfilled cost references. The included import intentionally defaults to historical status.

## Current limitations / production blockers

- **No authentication, authorization, multi-tenant access control or HTTPS yet.** API is intended for a localhost developer environment only.
- Historical job totals require reconciliation with purchase entries and accounting data before being used as certified financial reports.
- Proposal, maintenance, invoicing, dispatch, inventory and technician mobile workflows are UI scaffolds, not full backend modules.
- The migration process currently uses exported CSVs; no deployed Google account synchronization is included.
- Existing localStorage prototype records are *not* silently copied to the database. Explicit migration/review required.
- New local job records support one newly created location. Customer address editing, location deduplication, receiving and PO correction flows are future work.
- Real migrations should use Alembic schema versioning; `create_all` is only for this development milestone.

## Validation

```bash
python -m pytest -q
node --check app.js
```

## Version 0.3: Authentication and authorization foundation

- Create an initial account after installing requirements:
  `python -m server.create_user --username owner --role owner --name "Company Owner"`
  Enter a unique password of at least 12 characters when prompted.
- Start local dev server: `uvicorn server.main:app --host 127.0.0.1 --port 8000`
- Visit http://127.0.0.1:8000 and sign in.
- Accounts are stored in `users`, password hashes use salted PBKDF2-SHA256, and server-side sessions use an HttpOnly SameSite cookie. Set `COOKIE_SECURE=1` only when serving over HTTPS.
- Roles available for account setup: owner, office, estimator, dispatcher, technician, bookkeeper. **The current office UI is enabled only for owner/office/estimator.** Technician, dispatcher, bookkeeper, and customer-facing views are not implemented yet. No staff access should be provisioned until those views are built and independently tested.
- API request access is enforced server-side. Unauthenticated access to customer/job/PO and finance records is rejected, and the static router now serves only index.html, app.js and styles.css (not the project directory).
- Your existing LMS Job Board is not modified or automatically imported. The migration script still requires explicit CSV exports and validation.
- The existing database file created by a previous version can remain in place; the new user/session tables are added on startup. For production use, replace automatic create_all with versioned database migrations.

### Still required before production

TLS/HTTPS and secure cookie settings; authentication rate limiting and account lockout; CSRF/origin verification on state-changing endpoints; password resets, MFA and session administration; a real per-technician job-assignment model and granular scope enforcement; security reviews and dependency pinning; PostgreSQL configuration and migration tooling; centralized logs, encrypted backups, data retention policy, tested restores, monitoring, and hosted deployment. Do **not** expose this local development server to the public internet.

## Version 0.4: historical data package (2026-10-24)

An extracted database is included at `data/limitless-imported.sqlite3` and was built from the active `LMS Job Board` only. Excluded: `Danny Employee Portal`, `LMS CASH BOARD`.

Imported: 31 customer accounts, 136 locations, 253 jobs including DTM and OVHD, 407 POs, 351 labor entries. Unresolved records were excluded and described in `data/import-report.json` (91 PO and 8 labor exceptions). No PO may have a missing job foreign key. Historical PO received values are backfill bookkeeping placeholders, **not verified receipt evidence**. Historical job material totals already contain historical purchases; do not add their imported PO totals again.

To run the application on imported data:

```bash
export DATABASE_URL=sqlite:///$(pwd)/data/limitless-imported.sqlite3
python -m server.create_user --username owner --role owner --name 'Company Owner'
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Use a unique strong password. The ZIP contains real company financial and customer records. Store privately, do not email publicly, and do not expose the development server to the internet. This copy is a point-in-time snapshot; it does not automatically sync with subsequent Sheet changes. Reconcile flagged exceptions and accounting totals before treating reports as final.

## Version 0.5: Dispatch and technician workflow (local development)

- Office users with manage_jobs access can create work orders against existing LMS job numbers, assign active technician accounts, and browse the dispatch schedule.
- Assignment endpoints reject overlapping appointments for the same technician and require timezone-aware timestamps. Office-side `datetime-local` values are converted to UTC before submission.
- `/tech` provides a mobile-sized technician portal with assigned work orders, status changes, service notes, and note history.
- Unauthorized technicians cannot view or update another technician's work orders. They cannot access the company-wide dispatch view or office financial snapshot.
- Tests: `python -m pytest -q`; JS syntax: `node --check app.js && node --check tech.js`.
- The bundled `data/limitless-imported.sqlite3` contains a point-in-time import of company data; new work orders and dispatch assignments are created when running the application against it. The old spreadsheet remains untouched.
- In v1.2 the included `limitless-dev.sqlite3` is a **clean copy** of the historical import and contains no development test accounts. The separate QA database is excluded.

**Not ready for internet-facing production:** no hardened login throttling, CSRF protection, MFA, deployment backup procedures, or cross-instance session/dispatch concurrency guarantees. Technician time punches, inventory, photo storage, signatures, equipment registration and invoicing workflows still require implementation.

## Version 0.6 equipment and technician timekeeping
- Office equipment registry: /equipment (authenticated owner/office/estimator/dispatcher access; creation requires manage_jobs).
- Equipment assets belong to one customer location, with a unique asset tag within that location.
- Attach equipment to a work order through POST /api/work-orders/{id}/equipment/{equipment_id}; the backend validates job/customer/location matching.
- Technician portal at /tech now shows linked assets and can submit completed time intervals and mileage.
- Technician time is stored separately from historical labor entries. It is NOT yet converted to payroll or automatically monetized for margin calculations; labor rates and approval workflows must be added before production accounting.
- Overlapping time entries are rejected, and work-order assignment checks apply to time and asset access.
- Local SQLite uses create_all for new tables. Production deployment requires versioned Alembic migrations, TLS/CSRF protections, staging, backup verification and PostgreSQL testing.
- Equipment information was not present in the core Job Board import as a normalized asset registry and must be built through field verification or separate documented sources.

## v0.7 (local development)

New functionality:
- `GET/PUT /api/labor-rates`: set and inspect loaded hourly cost, overtime multiplier, mileage cost for technicians (office financial roles).
- `POST /api/time/{entry_id}/approve`: a financial user approves the entered time with an explicit overtime split; approved costs are a separate immutable snapshot and cannot be approved twice.
- `GET /api/jobs/{job_number}/field-costs`: totals approved **new field entries only**, displayed separately from historical spreadsheet totals to avoid double-counting.
- `GET /api/work-orders/{wo_id}/report`: access-controlled, HTML printable service summary (browser Print -> Save as PDF; not a certified PDF/signature workflow).
- `POST/GET /api/pm/plans`: equipment-specific interval and checklist management.
- `POST /api/pm/plans/{id}/work-order`: generate a PM work order with an *existing job number from the same customer and location*; duplicates per scheduled due date rejected.
- `POST /api/pm/visits/{id}/inspection`: assigned technician enters complete pass/fail/N/A checklist, records failures, and advances the next due date based on the scheduled date.
- `/pm`: basic office form for plans, cost rates and approvals. Requires existing login cookie. The technician inspection currently uses the API; full mobile UI is pending.

**Limitations:** This is not production-ready; approval editing/reversal, recurring automation runner, PM agreement billing, invoice integration, photo/signature PDF, time-policy holidays/weekly overtime, robust payroll accounting, and production migration scripts remain outstanding. In this version, the local SQLite data is provided as a development snapshot and no Google Sheets connection is needed at runtime. Existing source workbook is unchanged.

Run from project root: `python -m uvicorn server.main:app --host 127.0.0.1 --port 8000`, then `/pm`. Test with `python -m pytest -q`.

## v0.8 - Proposals, invoicing and collection tracking

- New `GET /financials` office interface (requires office/owner login for API data).
- Proposals: draft -> approved, with unique proposal numbers and job associations.
- Invoice drafts can be generated independently or linked to one approved proposal for the same job. Approved proposal-linked invoices require matching full amount; progress billing requires a separate unlinked invoice.
- Invoices: draft -> issued -> partially paid -> paid. Payments require unique references and cannot exceed remaining balance.
- `GET /api/jobs/{job_number}/financial-overview` separates billed, collected, receivable, historical cost, new PO spend, committed PO amounts and approved field labor.
- Historical Wave invoices remain in source records only; this release does NOT recreate or resync historic Wave invoices.
- Payment recording is bookkeeping only: no card charging, bank verification, tax engine, refunds, voids, credit memos, notification emails, or customer e-signature yet.
- This is a local development package and is **not safe for public deployment** without security hardening, database migrations, backup/restore testing and staging acceptance.

## v0.9 - Customer billing visibility (development)

- `/billing` provides billed/collected/outstanding/overdue totals and customer-by-customer job and invoice lists.
- `/api/billing/customers/{id}` returns job count, invoice balances and historical contract totals separately.
- `/api/invoices/{id}/document` renders a Limitless-branded print/PDF-ready invoice. This is a simple one-line invoice; detailed line items, taxes, terms and PDF attachments still require development.
- A `mailto:` email draft is available in the billing page. It does NOT send the email, attach the PDF or mark the invoice delivered.
- Manual payment recording remains available through `/financials`; it does not move money or confirm bank deposits.
- Future bank verification: use a bank-feed provider such as Plaid, treat pending vs posted deposits separately, propose matches based on reference/amount, and require confirmation before applying payment. No bank credentials or feeds are connected.
- Only issued CRM invoices count in the new billing dashboard; older historical Wave invoices are not included as paid/unpaid ledger invoices.
- Access requires the existing `view_financials` scope. This is a local development build, not approved for internet deployment.

## v1.0 (development) — Direct PDF invoice email

The Billing page now supports real PDF invoice downloads and a **Send PDF Invoice** action using the Gmail REST API. Every successful provider send is recorded with recipient, timestamp, and Gmail message ID. The system refuses to send draft invoices; permission `view_financials` is required. Failed provider requests do not create send-history records.

### Required configuration (not included in this package)

The CRM operator must authorize a company-controlled Google Workspace Gmail mailbox using the `https://www.googleapis.com/auth/gmail.send` OAuth scope. Configure these four environment variables on the **server**, never in frontend JS or repository files:

- `LMS_GMAIL_CLIENT_ID`
- `LMS_GMAIL_CLIENT_SECRET`
- `LMS_GMAIL_REFRESH_TOKEN`
- `LMS_GMAIL_SENDER` (the same Google Workspace mailbox authorized by OAuth)

Obtain the refresh token through a properly registered Google OAuth consent and authorization flow with offline access. The current release **does not include a self-serve Connect Google Account button**. Do not share refresh tokens by chat or commit them to Git. The sender is disabled until all settings exist. Use a restricted Google account for staging and send only to test recipients. Production requires managed secret storage and a security review.

Existing invoices continue to be recorded and manually paid regardless of email configuration. Sending is not proof of delivery, read receipt, or customer payment. Existing historical Wave invoices are not recreated. This is **not yet hosted or production hardened**.

Endpoint summary: `GET /api/billing/email/status`, `GET /api/invoices/{id}/pdf`, `POST /api/invoices/{id}/send-email`, `GET /api/invoices/{id}/email-history`.

### Known limitations

- Gmail delivery is not yet end-to-end tested with the actual company Google account; provider calls are mocked in automated tests.
- Customer-facing email compose supports one recipient at a time; CC, reminders, and statements are future work.
- Retry after uncertain network outcomes could send a duplicate message; add a durable send-queue/idempotency key before production.
- This PDF is a clean single-line invoice presentation; detailed line items, taxes, legal terms, and payment instructions require final specification.

## Version 1.1: Statements and send safeguards

- Billing customer detail includes a **Download Customer Statement PDF** action covering issued invoices, payments received, and balance due. Draft invoices are excluded.
- Server-side PDF statements are protected by the same financial permission requirement as billing details.
- Invoice email endpoint blocks repeat sends to the same recipient unless `confirm_resend: true` is explicitly supplied. UI asks for resend confirmation before retrying.
- Successful Gmail submission is recorded in `crm_invoice_email_events`. Google OAuth remains intentionally unconfigured; no live emails are sent unless deployment secrets are supplied.
- Payment reminders are **not automated** in this release, and bank matching is not enabled. This is not a live hosted system.
- `tests/test_v11.py` tests statement PDFs, authentication, duplicate-send rejection, and explicit resend; all eight automated tests currently pass.

## Version 1.2 — Financial completion (local development)

**Release scope:** itemized invoices with per-line tax rates, credit memos, recorded customer refunds, payment reversals, A/R aging, updated customer statements, and customer financial summaries. The Limitless charcoal/deep-teal styling is unchanged.

### Itemized invoices

On **Financials** (`/financials`), choose an existing LMS job, enter a unique invoice number, and add one or more lines with description, quantity, unit price, and an **explicit tax rate percentage**. Tax is calculated per line with half-up cent rounding. With lines supplied, the invoice total is calculated by the server. If you also provide a total, it must match. Legacy invoices containing only a total remain readable and printable.

An invoice may be edited using `PUT /api/invoices/{id}/lines` **only while in Draft**. Issued invoices are immutable: correct an issued amount with a credit memo rather than rewriting its contents. Invoices based on approved proposals must continue matching approved totals.

Tax applicability and rates are not inferred from geography or product type. The office must select the correct tax rate/exemption for each line before issuing an invoice. Accounting/tax review is still required before production use.

### Adjustments

- `POST /api/invoices/{id}/credits`: auditable numbered credit memo; total credits cannot exceed the original invoice amount.
- `POST /api/payments/{payment_id}/reverse`: auditable partial or full correction of a recorded payment; requires a unique reference and reason and cannot reverse funds already refunded.
- `POST /api/invoices/{id}/refunds`: record a **manual** refund of money to a customer (does not transfer funds). Refund cannot exceed credit balance created by credits/overpayment and requires a unique reference and reason.
- `GET /api/invoices/{id}/transactions`: immutable ledger entries for payments, credits, refunds, reversals.

Credits decrease net billed revenue, while payment reversals and refunds decrease net collections. The system separately reports outstanding receivables and customer credit balances. These adjustments do not alter imported historical Job Board invoices or process bank transactions.

### Customer A/R aging and statements

The Billing page (`/billing`) includes aging buckets: current, 1–30, 31–60, 61–90, and 90+ days overdue. The API supports `GET /api/billing/aging?as_of=YYYY-MM-DD` and `GET /api/billing/summary?as_of=YYYY-MM-DD`. As-of calculations use transaction dates for payments, credits, reversals, and refunds. Printable customer statements now include an aging summary. Invoice PDFs display line details, tax, credits, net collected, balance due, and any credit pending refund.

### Data safety and production limitations

- One canonical source was used for the historical import: the active LMS Job Board. Danny Employee Portal and LMS CASH BOARD are excluded.
- Release database is a clean copy of the original normalized import, **not** the QA database. No live customer bills or emails were sent.
- Google Workspace/Gmail integration remains unconfigured; no Google Cloud uploads or account configuration are required for this release.
- Bank matching, online payment processing, live email delivery, automatic tax determination, detailed historical Wave invoice reconciliation, stronger authorization, transaction-level race protection, production HTTPS/backups, and PostgreSQL deployment remain outstanding.
- SQLite is for local testing only. Do not expose this build to the public internet or use it for authoritative financial operations yet.

### Tests

Run on a disposable database (not the shipped imported one):

```
cp data/limitless-imported.sqlite3 /tmp/limitless-v12-tests.sqlite3
PYTHONPATH=. DATABASE_URL=sqlite:////tmp/limitless-v12-tests.sqlite3 pytest -q
```

Eleven automated tests cover existing functionality and v1.2 adjustments, aging and access controls. Static JavaScript syntax was checked with Node, and an itemized PDF was rendered and inspected. The mobile file preview is not a substitute for the actual local server.


## Version 1.3 — Purchasing, job costing, and buyer receipt uploads

This release follows the actual Limitless purchasing policy (superseding any earlier approval-workflow descriptions): **no manager approval is required for a purchase order**. A PO is immediately issued when created, with no separate approval or issue step. New POs remain tied to **exactly one existing LMS job** by a NOT NULL foreign key and API checks.

### Buyer-specific PO number sequences

Each account has one unique assigned PO designator. The CRM **generates the PO number on the server**; buyers cannot type or change their prefix. Numbering resumes after the historical imported records:

| Buyer | Designator | Next number in the included historical snapshot |
| --- | --- | --- |
| David | `DR` | `DR0211` |
| Adrian | `AR` | `AR0307` |
| Danny | `DB` | `DB0032` |

These starting numbers are calculated from the included snapshot, **not guaranteed against any new POs entered into the Google Sheet after the snapshot**. Reconcile recent purchases at final cutover. Unique PO number and user designator constraints are enforced in the database; each number is allocated in a database transaction. A cancelled or deleted request does not re-use an issued number. The buyer's designator is configured per login, not merely inferred from their display name.

Create actual company accounts on the local development server (desktop setup later, not required for the current mobile preview):

```bash
python -m server.create_user --username david --role owner --name "David" --po-prefix DR
python -m server.create_user --username adrian --role estimator --name "Adrian" --po-prefix AR
python -m server.create_user --username danny --role technician --name "Danny" --po-prefix DB
```

These commands prompt for strong unique passwords. Do **not** embed company passwords in scripts. A given designator can only belong to one account. Roles and username assignments are examples and can be changed before onboarding staff. The included release database has **no pre-created employee accounts**.

### Purchasing and proof of purchase

- `/purchasing` is the office purchasing interface; authenticated assigned buyers may create POs, receive materials, enter returns, and upload proof. Owner/office roles can view all records; technicians assigned a prefix see only POs bearing that designator and **cannot view job profitability**.
- POs are immediately committed to a job when created. Receipt entries accrue material costs, vendor invoices reconcile received values and document variances, returns reduce unbilled received value, and vendor credits reduce invoiced expenses. Imported historical POs are excluded from new expense accumulation, avoiding duplicate historical material charges.
- The new receipt upload accepts **JPEG, PNG, WebP, HEIC, and PDF**, up to 12 MB per file. On iPhone use Take Photo, Photo Library, or Files. Select the PO, then upload; the file is linked to the PO, with an uploader and timestamp audit event. The attachment is not a monetary receipt transaction by itself; financial costs still use receiving/vendor invoice entry.
- Files are stored **privately outside the public static routes**. `LMS_ATTACHMENT_DIR` may override the local `private_uploads/` path. Storage must be persistent on hosting and included in encrypted backups; database backups alone are insufficient to restore attachments.
- `GET /api/purchasing/my-number` previews the next number but does not reserve it; `POST /api/purchasing/pos` reserves the next number, validates the job, and issues the PO. `POST /api/purchasing/pos/{number}/attachments` accepts multipart form fields `file` and `kind`, and uploads are retrieved through authenticated download routes. The legacy `/api/purchase-orders` manual-number endpoint is retired with HTTP 410 to prevent bypass.
- This uses a local development schema upgrade for the `users.po_prefix` column. Production hosting still requires versioned SQL migrations, protected object storage, malware scanning, max request size enforcement at the gateway, MFA, CSRF protection, restore drills, and a security review.

The buyer prefix and receipt policies are tested alongside job-specific costs. **14 automated tests passed** against a disposable SQLite database; the included original historical database was not mutated by testing. The full CRM is still a local development build, not a publicly hosted production system. Google Sheets, Danny Employee Portal and LMS CASH BOARD were not modified.


## v1.4.1 — Permanent equipment history and operating-condition tracking

- Numeric readings from every field work order remain associated with their equipment ID, including electrical, compressor/fan amperage, refrigerant pressures, superheat/subcooling, temperature, and airflow measurements.
- A new `equipment_conditions` table tracks each work order's condition rating and inspection notes for each linked asset, included in signed service-report snapshots and PDFs.
- Technician field form `/field?work_order=<id>` displays previous readings automatically upon selecting linked equipment; **View Equipment History** opens per-unit historical visits, conditions, PM inspection counts, specific equipment photographs, and previous-vs-current reading summaries.
- Office `/equipment` registry has an equipment history button for each asset.
- `GET /api/field/work-orders/{wo_id}/equipment/{equipment_id}/history` is restricted to authorized work-order users and only equipment explicitly linked to that work order. `GET /api/equipment/{equipment_id}/history` is office-scoped. Equipment photo retrieval is similarly scoped.
- `PUT /api/field/work-orders/{wo_id}/equipment/{equipment_id}/condition` saves or updates a pre-signature condition; signed work orders remain locked. Audit events are generated.
- The history endpoint includes up to the 40 latest service visits with a `has_more` indicator. Historical equipment readings are not presently imported from the LMS Job Board because source data lacks per-asset measurements. A future iteration can add more detailed circuit/phase labels and chartable long-term trends.
- New version is local development only. Do not serve the SQLite database, invoice data, or uploaded photographs publicly. No changes made to Google Sheets.

## v1.5 — Advanced proposals, customer acceptance records and maintenance agreements

**New office screens:** `/estimates-workspace` (proposals, costing options, customer authorization records and PDF generation), `/pm` (maintenance agreements, covered equipment, schedule generation, PM deficiency estimates). These are served by the authenticated local development API; open them on a machine running the Python server.

### Proposals

- Build company-branded proposals with scope, inclusions, exclusions, warranty, payment terms, estimated lead time, and up to five priced alternatives. Internal estimated costs remain in privileged office API data; customer PDF only shows selling prices and scope. **Customer PDF is not an official customer e-sign flow.**
- Draft estimates store immutable, hashed revision snapshots. When an authorized office employee records independently verified customer acceptance, they must select one option and enter the approver's name and external evidence reference. The accepted price becomes the proposal total used by the existing invoice workflow. Accepted drafts cannot be edited; create a replacement proposal/change order for revisions.
- `POST /api/estimates/advanced`, `PUT /api/estimates/advanced/{id}`, `POST /api/estimates/advanced/{id}/record-acceptance`, `GET /api/estimates/advanced/{id}/pdf`, `GET /api/estimates/advanced/{id}/revisions`.
- The legacy proposal approve endpoint refuses enhanced estimates so the selected-option confirmation cannot be bypassed. Legacy/basic proposals remain supported for compatibility.
- Convert a technician's documented `field_reports.recommendations` into a draft through `POST /api/estimates/from-work-order`; duplicate conversion of the same work order is rejected.

### Preventive maintenance agreements

- Create maintenance agreements by customer and date range with amount, terms, notice period and covered equipment. Each asset is associated with an **existing job from that same equipment location**. No LMS job numbers are synthesized. Plans can be linked to a preexisting compatible PM plan to avoid duplicate equipment schedules.
- Activate a draft agreement; generate work orders for equipment due before a selected date. The scheduler is idempotent: it will not duplicate a due date or generate a new visit while the previous visit still requires inspection. Completion advances `next_due` from the scheduled due date, not the date when a technician submits the form. Work orders remain unassigned until office dispatch.
- `POST /api/pm/agreements`, `GET /api/pm/agreements`, `GET /api/pm/agreements/renewals`, `POST /api/pm/agreements/{id}/activate`, `POST /api/pm/agreements/{id}/schedule`. Maintenance agreements do **not** automatically issue invoices or email customers.
- Failed inspection items can be converted into one linked draft repair quote via `POST /api/estimates/from-pm-visit/{visit_id}`. The office must review its scope, price, and exclusions before approval or invoicing.
- A scheduler-ready command is available: `python -m server.schedule_maintenance --days-ahead 7`; this must be installed as a trusted recurring job after cloud deployment. **It is not currently running as an automated background job.**

### Safety and launch limitations

- Existing SQLite database gets additional tables automatically on application startup, without rewriting historical customers, jobs, POs or labor records. The package includes the clean point-in-time historical import. The Google Sheet remains the official operating source until final reconciliation and cutover.
- No live Gmail authorization, customer portal, public e-signature, automatic dispatch, bank sync or hosted scheduler are active. The local server must not be exposed directly to the internet. Production migration management, MFA, CSRF protection, backups, monitoring and a secure web host remain ahead.
- v1.5 regression suite: **19 automated tests passed** on a disposable database, including three new v1.5 tests and the corrected legacy PO regression fixture that previously duplicated real imported PO `DR0210`.

## v1.6 — Private staging and Google Cloud / Render readiness

- Added runtime safeguards (`server/deploy_config.py`) that reject hosted SQLite databases, insecure HTTP origins, insecure auth cookies, and wildcard host settings. `APP_ENV=production` remains disabled without a deliberate go-live override and professional security review.
- Added HTTPS/same-origin checks for browser write actions, secure response headers, and an authenticated application login requirement inherited from v0.3. These are **defense in depth**, not a substitute for a complete production security review.
- Added `Dockerfile` and `.dockerignore` to package only application code: no real company database, photos, credentials, or private receipts are baked into the container.
- Added a guarded, repeatable `server.import_staging_snapshot` import for the validated business tables into an **empty** PostgreSQL staging database, with dry-run default and no orphaned job/PO/labor relationships. This was rehearsed against a separate local database. **No real cloud database has been created or updated**.
- Hosted staging blocks receipt and field photo uploads until durable private storage is configured, because Cloud Run container filesystems are ephemeral.
- See `deployment/HOSTING.md` for Google Cloud Run + Cloud SQL and Render setup steps, and `tests/test_v16_deploy.py` for deployment and migration tests.
- The Google Sheet remains the operational source until a final reconciliation and formal cutover. Gmail OAuth setup is intentionally deferred for desktop setup.
