# Limitless Mechanical CRM — private Google deployment

Project: `limitless-crm-512612` · Region: `us-central1` · CRM v1.15 update, based on v1.6.

Status: **v1.14 is deployed; v1.15 is prepared locally and not deployed.** This bundle contains application source, initial deployment templates and historical release notes. Customer databases, credentials and Terraform state are excluded.

## Automatic releases — setup prepared, not activated

See [deploy/SETUP.md](deploy/SETUP.md). The included Cloud Build pipeline tests and releases image updates from the `release` branch to the existing service. GitHub and Google Cloud repository authorization are still required. No live connection or trigger has been created.

## v1.15 workflow update — ready for review, not deployed

Create a service call from Dashboard or Customers. Select or add its customer and service address, describe the problem, then optionally assign and schedule a technician, all on one page. Team, customer sites, office review and exports have distinct views. Pricing defaults are moved out of routine call intake. The technician workspace remains unchanged.

The current v1.14 service was deployed successfully during the guided session and the user reached its dashboard. This v1.15 update has only been validated locally. See WORKFLOW-UPDATE.md before upgrading.

**Upgrade only the application image.** Extract this archive into a new folder and build its `app` directory. Keep the existing deployed service configuration, Terraform state, secrets, database and storage bucket. The bundled `infra` and `service` directories are initial deployment templates and do not include every change made in the guided deployment. Do not copy them over the deployed configuration, apply them as a new stack, or rerun owner bootstrap. Update the image digest through the existing deployment configuration after reviewing the new plan.

## v1.14 compact branded invoices

Newly generated invoice PDFs use the same original Limitless logo, charcoal header, sky-blue accents and bundled fonts as quote PDFs. Invoice numbers appear in Invoice Details; payment remittance appears below Notes / Terms. Routine invoices fit one US Letter page, with Bill To, named service location, service dates/customer PO, itemized charges, taxes, received payments, credits, balance and configured check remittance details. Longer invoices paginate with repeated headings; descriptions and totals are never dropped to force a one-page result. Scope, pricing, ledger math, permissions and email settings are unchanged. Previously queued/archived attachments retain their original immutable PDFs; newly generated attachments use the updated layout.

## v1.13 one standard proposal layout

Every freshly generated customer quote PDF uses the retained **!limitless / Limitless primary proposal layout** automatically. There is no template selector or command to enter. Open **Proposals → Open Proposal Builder**, enter the job's own facts and pricing, save the draft/revision, then use **Open PDF**.

The shared renderer covers enhanced quotes, call-pricing exports, recommendation/maintenance-generated estimates and simple/legacy proposals via `/api/proposals/{id}/pdf`. The existing enhanced PDF route also uses that same renderer. Existing downloaded/archived files are not rewritten; re-exporting a stored quote uses the standard design. Stored scope, price, selected option, warranty, payment terms and approval records are retained.

The primary design includes the original reference logo, condensed heading hierarchy, numbered project/equipment/scope/warranty/exclusion sections, open-line equipment schedule, investment cards, running headers/footers and signature layout. Its generator is bundled from the retained proposal skill, with portable DejaVu fonts and their license. The actual embedded logo was extracted from the retained reference PDF rather than redrawn or substituted from a mockup. Template source/reference assets were not modified. The proposal logo is packaged specifically for quote PDFs.

Optional document details support a subtitle, customer contact, proposal date/valid-through date and an equipment schedule, entered as `Component | Description` per line. Scope and commercial content come from the quote, not the old Estill project example. No standard 30-day expiry or warranty is invented. Leaving deposit fields blank uses the entered written terms; percentage or fixed-amount deposits are explicit. Fixed deposits require one option and cannot exceed its price. Alternatives use their own prices and percentage calculations. For a $15,001.01 option with a 50% deposit, $7,500.51 plus $7,500.50 equals the approved total exactly. Payment presets are optional admin actions, not automatic changes to terms.

Internal estimated costs, supplier prices and margin fields are never mapped into the customer PDF. Accepted new quotes retain document facts and presentation details in the acceptance snapshot; editing accepted quotes remains blocked. The customer approval view includes customer-facing equipment/presentation and explicit deposit figures. This release standardizes output appearance; it does not introduce new expiry-related acceptance restrictions or send any quote automatically.

The new `quote_presentations_v113` table is additive. Existing records lacking presentation data are also rendered in the standard layout. For PDF structure/text regression tests, install `app/requirements-dev.txt`; production needs only the existing runtime requirements. Cloud image packaging explicitly includes the logo despite the general upload-image exclusions.

No shared Drive folder is selected or written by this integration. Choose the confirmed customer folder/filename when exporting a finalized real proposal, as required by the proposal workflow; automatic shared-Drive filing is not configured by this release.

## v1.12 Business at a glance

Open **Business overview** from the admin header, the office dashboard or the Calls/Billing navigation. The new route is `/business-overview`; the data endpoint is admin-only. No additional database tables, paid service or external chart library is required by this update.

- Select **Day, Week, Month, Year or Custom**, choose a date in the desired period and use the previous/next buttons. Weeks start Monday; business dates use America/New_York. Current periods stop at today, with an in-progress label. Future periods have no recorded chart points.
- Four headline cards show invoices issued including tax, net payments received including tax, recorded job costs and reviewed matched-call gross margin. Calls created, new POs and credit memos appear below. Credit memos are shown separately from gross issued invoice totals.
- Green/purple trend lines compare invoices issued or payments received against recorded job costs. Week/month use daily buckets; year uses monthly buckets. Day shows one pair of date-total bars because invoice/check records store dates rather than hourly posting times. Tap/focus a point, open the accessible data table, download CSV or print the overview.
- Overdue invoices and invoices due within the next 30 days show both counts and unpaid amounts. Click a card to inspect invoice/customer/job/due-date/balance rows and open the document. These are balances as of the selected period end, capped at today; they include older outstanding invoices, not only new invoices in the selected range. Unknown due dates remain in total outstanding but cannot be classified as overdue/upcoming.
- Full check receipts count once, including unapplied amounts. Invoice allocations and bank deposits do not create another receipt. Returned checks, non-check reversals and customer refunds reduce payment receipts on their recorded dates. Wave opening balances do not count as new issued revenue.
- Purchasing cost is rebuilt through dated receipt, bill, return and credit changes. Bills replace the receipt value they match; a return of already billed goods does not reduce cost a second time before the vendor credit. Approved labor and legacy mileage follow the work-start local date; approved odometer vehicle costs follow the approval date. All costs are operational recorded costs, not proof of payroll/vendor cash payments.
- Reviewed call margin matches pre-tax invoice revenue to current reviewed call cost snapshots. Incomplete, adjusted and manual/unlinked invoice coverage is identified rather than assumed complete. Later adjustments can reduce historical coverage. The chart does not calculate company net profit or taxable income: historical undated spreadsheet cost, unrecorded/non-PO costs and business overhead are outside its dated cost series.

The mockup images supplied for reference were not imported as financial transactions. The existing detailed profitability, purchasing and accountant-export pages remain available for deeper review.

## v1.11 billing and field workflows

This is the normal CRM application. Test it with mock customers before using real business records; there is no separate reduced testing edition. The existing cloud deployment is not modified by downloading this package.

Open **Billing desk** (`/billing-desk`) and **Service office** (`/service-office`) from the admin header or Service calls. Technician workflows remain on the assigned call screen. No truck inventory or QR codes were added.

- **Invoices:** sequential number reservations, customer PO/reference, service-date range, payment terms, check payee and remittance address. Commercial/customer/site details are frozen when issued and reused in PDF/HTML documents. Reserved but unused numbers may leave gaps. Configure company information before issuing real invoices.
- **Delivery outbox:** preview the exact PDF attachment, prepare delivery, inspect status and explicitly confirm resends. Identical queued/previews and repeated request IDs do not create another message. A provider timeout or interrupted send becomes Unconfirmed and requires checking accounting Sent Mail before another attempt. Provider acceptance does not prove delivery/read. Email previews are a configurable setting, not a separate application build.
- **Check receipts:** receive once, allocate portions across issued invoices belonging to the same customer, retain unapplied amounts, record deposits and returned checks. Returned checks reverse remaining allocations and reopen balances. Job purchase orders remain exactly one job each.
- **Collections:** configurable overdue stages and pause reasons. Follow-ups are prepared into the same outbox. Paid/paused invoices are checked again when reminders process. The operator-run worker can be scheduled after deployment; no scheduler or external emails were activated here.
- **Contacts:** separate billing, scheduling and site-purpose contacts; collection recipients use billing contacts rather than sending to every contact.
- **Technician completion:** a Finish call review checks work performed, reported parts/no parts, stopped clock, equipment and mileage. Missing equipment or inapplicable mileage requires an explanation. Waiting for Parts and Waiting for Approval retain open work without falsely completing it.
- **Field recovery:** opened call data and unsynchronized technician notes, parts, photos, odometer/checklist changes and completed clock records can be retained per employee in IndexedDB. Static page resources use a service worker. Reconnect with the same employee, synchronize and review conflicts before finishing. Recovered offline time requires admin approval; captured clock-out preserves the actual captured end time. Previously unopened calls, customer signatures and admin pricing remain online workflows. Device storage is not a backup and should be used on a trusted employee device. Sign-out prevents silently discarding queued work or a device timer.
- **Equipment context:** recent linked-system visits, repeat-call warnings, warranties and private equipment documents appear on the call. Upload PDF manuals/wiring diagrams from Service office; access remains limited to linked, authorized calls. Existing durable storage configuration also governs documents/offline photos.
- **Customer workflows:** expiring, revocable scoped quote approval and service-request links; quote revisions invalidate stale approvals and internal costs are excluded. Requests choose a named customer site and are converted into a call only against a verified same-site/customer job. Default previews require admin sign-in. External customer access is an optional separate, restricted ASGI service; the staff CRM remains behind IAP.
- **Maintenance:** reusable visit checklist templates, due/overdue attention and renewal lists, and idempotent contracted visit generation. Template changes apply to future definitions, preserving existing inspection records.
- **Wave transition:** explicit validated opening-outstanding-balance mapping, not guessed Wave columns or automatic synchronization. Validate first, then commit. Opening balances are not reported as new sales in the accountant export. Check receipts/returns are exported separately from their reference-only invoice allocations to avoid counting the same receipt twice.

### Email from accounting@limitlessmechanical.net

No mailbox credentials are included. Authorize the accounting mailbox (or verified send-as identity) using the existing Gmail OAuth configuration and scopes sufficient for send plus send-as verification. Store credentials in the approved secret mechanism. Existing `LMS_GMAIL_CLIENT_ID`, `LMS_GMAIL_CLIENT_SECRET`, `LMS_GMAIL_REFRESH_TOKEN` and `LMS_GMAIL_SENDER` configuration must be completed by the operator. The sender must be `accounting@limitlessmechanical.net`; the transport checks the accepted send-as identity before sending. Do not paste refresh tokens into chat, source code or logs.

To enable delivery, set the server environment `LMS_ALLOW_EXTERNAL_EMAIL=1`, configure company remittance details, and turn off **Preview emails before enabling delivery** in Billing desk. A prior preview cannot silently become a live send: explicitly prepare a new copy. Issued PDFs without a frozen remittance address cannot be sent; review/correct the document first. For mock-customer testing, use only email addresses you control when exercising real delivery.

Optional office worker, from the app directory with the same database/secrets as the app:

```bash
python -m server.office_worker --admin-username YOUR_ADMIN_USERNAME --prepare-reminders
python -m server.office_worker --admin-username YOUR_ADMIN_USERNAME --process-mail
```

The worker does not provision scheduling. Concurrent production behavior must be checked on PostgreSQL. Preview settings and server enablement also govern worker delivery.

### Optional external customer surface

Internal previews need no public deployment. For external use, the separate entrypoint is `server.customer_portal:app`; it exposes only scoped resolve/accept/request endpoints, no staff APIs. Configure `LMS_CUSTOMER_PORTAL_ENABLED=1` and the exact HTTPS origin in `LMS_PORTAL_PUBLIC_URL` on the staff and portal services. The portal must share the CRM PostgreSQL database and relevant secrets. Explicit host/origin checks, hashed tokens, expiration, revocation and quote revisions protect access. Possession of a link records consent; it is not independently verified identity or a certified signature. Links are never sent automatically. Public deployment and any associated resources require separate cost/access review and were not performed.

## v1.10 rollout tools, named service sites and three-user private test

**No truck stock tracking or QR codes were added.** This release builds on v1.9 and remains local source until deployed. Start at `/operations` (**Review tools & sites** in the admin header).

- **Where data lives after deployment:** customer records, named sites, equipment, jobs, calls, hours, notes, prices, quotes, invoices and file metadata use PostgreSQL. Pictures, receipt documents and signature image bytes use a separately configured private Google Cloud Storage bucket. Local development uses private local files. Hosted uploads fail closed until durable storage is configured; files are never saved only to Cloud Run's temporary disk.
- **Multiple sites, one bill-to:** each customer has one shared billing address and any number of individually named service sites. `/operations` manages site names, addresses, contacts and phone numbers separately from shared billing details. Jobs and equipment retain their site/customer relationships. Existing general job creation and Quick call can select those same sites. Invoices show both the shared Bill To and the job's named Service Location; site changes do not create another customer or alter its billing address. Revisions and edits are audited.
- **Three separate logins:** David plus one additional full admin and one technician. The team screen creates named accounts with individual 12+ character passwords and exact Google email metadata, never shared owner credentials. The existing internal `owner` role supplies full admin privileges, including team management; the UI labels that choice Admin. The technician can open only assigned calls and cannot access new admin financial tools. Account deactivation revokes active app sessions; stop/review a running clock before deactivating its technician. Real employee accounts have NOT been created by this update.
- **Google access is separate:** `service/main.tf.json` accepts `additional_tester_emails` (default empty) for the exact Google identities of the admin and technician. The original `tester_email` remains allowed. This is source configuration only; IAM has not been applied. Add both employees as OAuth test users where required by the existing custom external OAuth configuration. App creation does not automatically grant IAP, IAM or Google Console access. Do not grant broad domain/public access.
- **Photos:** the call screen includes camera/file selection, caption, upload and authenticated retrieval. These photos belong to service documentation; use internal notes for private staff content and the purchasing workflow for receipts. Existing field photo and PO receipt routes, signed-report images and PDF image loading use the private storage adapter. A configured bucket must have uniform bucket access and explicitly enforced public access prevention. New objects are written with a create-only generation precondition; no public ACLs, signed URLs, bucket creation or IAM mutation are performed by the app.
- **Short checklists:** optional four-item templates for general service, heat pumps/cooling, gas furnaces, refrigeration and PM. Save Pass, Needs attention, Not applicable or Not checked per item, tied to a linked system on the call. Revisions prevent stale overwrites. These prompts supplement equipment-specific procedures, and checklist notes remain internal.
- **Additional work:** create a reviewed draft quote tied to the call, with internal estimated cost, whole-percent target and an optional explicit below-target exception. Record actual customer authorization separately by name, method and reference with an affirmative confirmation. The quote scope/price and approval record are retained. Technicians see approved scope/status without internal costs or customer prices. This records authorization obtained in person/by phone/email; it is not a remote customer e-sign portal. No work, invoice line or customer message is automatically authorized/sent by a timer or draft save.
- **Recommendation to quote:** choose a technician recommendation and create a draft without retyping customer-facing findings. Internal recommendations require the admin to write an explicit customer-facing scope. Source and request identifiers prevent duplicate conversions/retries. Reviewed quote floors also protect later price edits; billing remains a separate review to avoid double billing added work.
- **Warranty visibility:** enter manufacturer, labor or repair coverage, provider, start/end dates, reference and details against a particular system. Assigned technicians can see that system's coverage on the call. Expired/upcoming/inactive records are distinct. Coverage is entered/verified by the admin; active dates do not automatically waive charges or guarantee a claim.
- **Purchasing reconciliation:** allocate a job's received PO costs to reported part lines, without spanning jobs or allocating a receipt twice. Allocation cannot exceed received/actual available costs. On recalculation, actual call pricing uses the exact allocated extended cost in place of the part's catalog/declared extended cost, never in addition to it. Rounded display unit costs may differ from the exact allocation divided by quantity; the allocated extended total governs. Recalculate/save actual pricing after changes. Returns/credits that invalidate available costs flag re-review. Voiding an unissued reported part releases its receipt allocations; issued acquisition costs remain locked.
- **Payroll hours CSV:** preview a Monday–Sunday week in America/New_York, then export reviewed regular/overtime hours with employee, job, call and local timestamps. Pending approvals, time corrections or a running clock affecting the week block export. Cross-week entries assign approved overtime to the end of the entry and require explicit warning review. Hours are not net pay, withholding or an automatic overtime-law calculation. No breaks are deducted. No payroll provider is connected.
- **Admin inbox:** in-app reminders for due follow-ups, time approvals/corrections, completed unbilled calls, approved additional work awaiting billing review and repeat-system flags. The page refreshes once a minute while open. Snoozing is per admin and does not resolve the underlying work. Email, SMS and push delivery are not configured or sent.

### Private storage configuration (pending approval/setup)

No paid bucket or new cloud resources were created for this release. Before enabling hosted photos, obtain approval for storage costs, configure a private bucket and limited runtime permissions (including bucket metadata read and required object operations), then set `LMS_PRIVATE_UPLOAD_BUCKET` and `LMS_DURABLE_UPLOADS=1` on the service. Verify bucket privacy, upload/download, image embedding and file restoration in the real deployment. Keep the bucket/prefix mapping stable; existing database storage keys refer to this configured bucket. Configure a photo retention/recovery policy as well as database backups. App permission checks remain in front of every file download. Google SDK adapter tests use mocks and do not establish real cloud connectivity.

### Yearly accountant source export and Wave transition

The owner-only yearly export on `/operations` previews counts and downloads separate CSVs for issued invoices, customer payments, credits, refunds, payment reversals, vendor bills and vendor credits. Supporting files identify PO receiving, approved internal labor costs, odometer records and PO receipt documents. Optional receipt binaries can be included up to 50 MB; otherwise authenticated download paths are indexed. Undated issued invoices are isolated for review rather than assigned to an invented tax year. Unknown invoice/credit/vendor tax allocations remain blank and are flagged. Stable IDs support deduplication; spreadsheet formula-like text is escaped.

This is an accountant reconciliation package, **not a tax return, a complete general ledger, or an automatic Wave sync/import format**. Sales invoices and payments must not be added together as a single income figure. Vendor bill/receiving dates are not evidence of cash payments, and loaded labor/vehicle cost assumptions are not payroll paid or tax deductions. The export does not contain all overhead, bank/card transactions, payroll, assets/depreciation or other activity unless captured separately. Mileage uses call creation dates for its reference-year selection because the odometer record does not store a verified trip date; confirm actual travel dates before tax use. Keep original supporting documents and reconcile against bank records and any existing accounting system.

Use mock customers to test the normal workflow, and retain the spreadsheet/Wave history as a comparison and archive. Once verified, the CRM can be the invoice-generation/sending authority for the stated Wave usage. Choose one system to issue/send each real invoice; do not independently bill the same call in both. The CRM can take over basic quotes, itemized invoices, manual payments and balances after validation. Gmail sending remains unconfigured, online card/ACH payment processing is not connected, and bank reconciliation/full company financial statements are not implemented here. If Wave is used for those functions, retain it until an explicit replacement is implemented and verified. No existing Wave records were imported or modified.

Reference: Wave's current help describes bank reconciliation and company financial reports at https://support.waveapps.com/hc/en-us/articles/38599574877332-Reconcile-your-books and https://support.waveapps.com/hc/en-us/articles/115005085723-Overview-of-the-Reports-page . IRS source recordkeeping guidance: https://www.irs.gov/businesses/small-businesses-self-employed/what-kind-of-records-should-i-keep .

### First private rollout

Use `ROLLOUT-CHECKLIST.md`. No additional feature expansion is needed before the three-person pilot. Remaining work is authenticated deployment, employee Google/app access, private file storage, real rates/catalog/customer setup, a backup/restore exercise and a complete phone-to-invoice acceptance test. Production sign-off requires the actual phone, PostgreSQL and deployed IAP workflow checks; a local test pass is not that sign-off.

## v1.9 usability, profitability and long-term customer tracking

Open **Service calls** for day-to-day work, **Profit & customer history** (`/profitability`) for lifetime reporting and controls, and **Metrics** (`/admin-metrics`) for the existing date-filtered operational report. New profitability screens and their APIs require the owner role; technician and office employee logins cannot open them.

### Simple technician call screen

- `/tech` lists assigned calls and the running timer. **Open call** leads to one workspace with clock in/out, start/finish odometer, parts used, work performed, recommendations and follow-up notes. One clock covers travel and work; no breaks are collected or deducted.
- Parts can be selected from a safe catalog containing descriptions and units only, or entered as unlisted items with quantities. Customer-facing/internal note visibility is explicit. Internal notes and internal part notes are omitted from customer report outputs.
- Private note and pricing drafts autosave without submitting, publishing, sending or issuing anything. Revision conflicts stop an older device overwriting a newer draft. Technician drafts belong to that user and assigned call.
- Missed completed time and correction requests remain under the expanded details. Corrections require an admin decision and review note; approval preserves the original evidence and resets cost approval. Running clocks prevent marking a call Completed.
- Tap-to-call and directions use saved customer contact/location details. **Add a system** under Equipment links each serviced unit to the visit, without revealing costs. Cross-customer and cross-site links are rejected. Signed reports and issued call system links remain protected.

### Office shortcuts

- Quick call creation validates customer/site/job and can add an optional technician assignment. A retry identifier prevents a repeated mobile request creating another call.
- Company and customer pricing defaults, favorite parts, reusable repair bundles, and the ready-to-bill queue are included. The queue checks completed status, approvals, current actual costs and explicit parts review.
- Actual pricing loads all technician-reported parts; missing lines or changed quantities prevent invoice export. Internal acquisition costs are completed by the owner.

### Profit controls

- **Loaded labor:** enter base wage, employer payroll burden, workers' compensation and hourly benefits. Future time approvals use wages plus those burdens, with overtime multiplying wage-based costs and benefits counted once per worked hour. Existing approvals retain their costs. Original cost breakdown snapshots survive a later reviewed time correction. Legacy rates remain usable as entered; they are not silently assumed to include burdens. Quote mode's hourly cost should be the fully loaded estimated rate, not a second application of the burden.
- **Acquisition costs:** quote and invoice pricing include separate freight and nonrecoverable purchase-tax fields, in addition to snapshotted catalog or unlisted part costs. Only enter amounts not already included in unit costs or Other direct costs. Purchase tax is distinct from customer sales tax.
- **Minimum call prices:** company defaults and optional customer overrides set a minimum pre-tax call subtotal. Automatic pricing meets the greater of that minimum and the gross-margin floor. A margin exception cannot bypass the minimum.
- **35% target and whole-percent slider:** the target remains 35–95% in 1% steps. A manual below-target price requires the owner to check the explicit exception box and supply a reason of at least ten characters. Saving records the approving user, time, exact price, mode and reason. Missing costs cannot be overridden; further discounts below the approved price are blocked. Restored drafts do not restore the approval checkbox.
- **Unbilled alerts and variance:** identify recorded but unissued labor/mileage, reported parts missing from actual pricing, pending approvals/corrections, changed costs, total-cost variance, parts-cost variance and labor-hour variance.
- **Projection during a call:** a saved complete estimate supplies the selling price and planned costs. The provisional projection uses the greater of estimated or incurred labor, vehicle and parts costs, including a running clock. Unapproved/running labor assumes regular hours; overtime and unlisted part costs require review. Missing rates or unlisted costs do not become zero. Projections never change the saved quote or invoice.
- **Callbacks:** classify a return visit and link it directly to an earlier original call for the same customer. Chains, self-links and cycles are rejected. Family profitability includes original and callback costs once, including unbilled warranty cost. Missing callback costs leave family margin incomplete. The original invoice is not rewritten. For a no-charge warranty return, turn automatic pricing off, set customer rates to zero, and save the actual costs for family tracking; a zero-price invoice cannot be exported.
- **Overhead:** configure an allocation per call hour. Gross margin remains before overhead; profit after allocated overhead is shown separately. This is a management allocation, not accounting net income.
- Comparisons cover customers, job types and associated systems. Customer groups use customer IDs, and system groups use asset IDs, so identical names/tags at different sites do not merge. Calls linked to several systems appear in each associated system group; those rows overlap and must not be summed.

### Customer lifetime history and system watchlist

- Select a customer on `/profitability` for recorded service history, equipment/model/serial, maintenance due dates, internal and customer-facing recommendations, follow-up tracking, quotes, invoices, lifetime collections, open balances and aging.
- Admin follow-ups can be Open, Scheduled, Resolved or Declined, with an optional due date. Tracking a technician recommendation preserves its source. It does not send a message or generate a quote automatically.
- Quote acceptance uses decided proposals only. Average days to full payment uses fully paid, unadjusted invoices and displays its basis; it is not a complete cash-flow forecast.
- Default system flags: **3 service calls OR 2 callbacks within 90 days**. Company/customer settings can change the window and either threshold. Flags count distinct calls linked to that specific asset, not multiple notes, parts or time entries. The window uses call creation dates in UTC. Generated PM visits and explicitly classified planned maintenance are excluded unless they are callbacks.
- The watchlist shows the customer, site, specific asset, reason, counts and underlying visits. Flags update when new visits are linked, classifications change, thresholds change or old visits age outside the window. These are in-app flags; no email or push notifications are configured.
- Lifetime history includes only records in this database; historical spreadsheet/customer records have not been silently imported. Customer billing totals include all customer invoices and tax/adjustments. Realized profitability covers linked call-pricing invoices with complete costs. Manual/unlinked invoices, historical spreadsheet job totals and job-wide PO spend are not mixed into per-call margins, avoiding double counting. Reconcile credits/refunds/reversals before treating adjusted invoices as realized profit.

### Test and deployment status

Local automated verification is recorded in `VALIDATION.txt`. This package is **not deployed**. Actual iPhone visual testing, PostgreSQL concurrency and a new private cloud image/deployment remain outstanding. No cloud resources were created or changed for this release. When resuming at a PC, preserve existing infrastructure state and copy only the new application source; the old v1.6 container does not contain these changes.

## v1.8 admin parts catalog and margin slider

- Owner/admin access only: the page, scripts, catalog APIs, private cost snapshots, pricing and export endpoints reject technician and office employee logins. Open **Call pricing & parts** from the admin header or `/call-pricing`.
- Add frequently used parts with SKU, description, unit (each/lb/ft/etc.), supplier, acquisition cost, default selling price and active/archive status. A blank selling price calculates the minimum for 35% margin. Duplicate part codes are blocked. Catalog revisions and edits are audited. This is a price book, not perpetual inventory.
- **Target margin slider: 35%–95%, in whole 1% increments**, for both Quote and Invoice views. A numeric field provides the same whole-percent selection. Fractional percentages are rejected by the server.
- Formula: minimum pre-tax selling price = direct cost / (1 − target margin), rounded UP to the next cent. Margin is based on selling price, not markup on cost. The summary shows direct cost, pre-tax subtotal, gross profit, actual gross margin and required minimum price.
- Automatic target pricing fills any price shortfall through the clearly shown Service call charge. Original parts and hourly selling prices remain when they already produce a higher margin. Moving the slider does not compound earlier automatic adjustments. It changes the draft calculation only.
- Quote mode uses estimated hours, internal hourly cost, estimated mileage, internal vehicle cost, parts and other direct expenses. Customer hourly and mileage rates are separate from those internal costs. Invoice mode uses completed, approved time and approved odometer costs; parts quantities and other expenses are entered by the owner as consumed/actual amounts. Overtime cost comes from time approval. Missing approvals, a running clock or missing odometer readings prevent final pricing export. Record matching start/end readings for zero mileage.
- Save draft pricing, then create/refresh a draft proposal (Quote view) or itemized draft invoice (Invoice view). No customer message, acceptance, issuing or payment is automatic. The same call refreshes its existing draft rather than creating duplicate invoice records. Below-target pricing requires the explicit, audited owner exception introduced in v1.9; linked draft discounts below the floor are rejected on invoice issue. Pricing-derived proposal options cannot be revised below the saved floor.
- Customer sales tax is excluded from gross margin and entered separately when generating the invoice. Purchase freight and nonrecoverable tax should be included in acquisition/other direct costs. Gross margin does not equal net profit after company overhead and income tax.
- Saved costs are snapshots. Catalog edits do not rewrite existing saved call prices. Use **Refresh catalog costs**, recalculate, save and refresh the draft when you want current catalog costs. Approved proposals and issued invoices remain unchanged. Changed actual time/mileage requires a fresh review before issuing a linked invoice.
- Mileage exported with a full-call invoice is linked to the same odometer record, preventing separate duplicate mileage billing. Do not also invoice time-entry legacy mileage outside this workflow. Credits/refunds, unrecorded expenses and job-wide purchasing are not a complete per-call realized-profit ledger; recorded parts costs must be reconciled to actual purchases by the owner.
- Existing general billing/proposal workflows remain available. The new floor applies to documents linked through Call pricing; this update does not claim a universal margin guarantee for unrelated/manual invoices or future refunds.

## v1.7 technician time and odometer update

- One server-persisted clock per technician, tied to one assigned call/work order. Travel and on-site time share the same hourly clock. Breaks are neither collected nor deducted.
- Closing the phone or signing out does not clock out. An internet connection is required to start/stop; there is no offline queue or backdating of clock buttons. Repeated clock-out requests do not create duplicate entries or stop a newer clock. Missed completed time can still be entered manually with overlap checks.
- One odometer record per call: save the starting reading first, then the ending reading. Miles = ending minus starting reading. End must be at least start; an unfinished reading cannot be approved or invoiced. An audit log retains edits. Another technician cannot add a second set for the same call.
- Office: Billing → Time & mileage review (`/time-review`). Configure internal hourly labor and mileage cost rates, review time, assign overtime when appropriate, and approve calculated mileage. Cost rates are separate from customer selling prices.
- Invoice mileage: select the call and an itemized draft invoice for the same job, enter the customer charge per mile and applicable tax rate, then add the calculated quantity. Completion and approval are required; duplicate mileage billing is blocked. This does not silently add fees to customer invoices or change an approved proposal total.
- Invoiced odometer readings are locked. For a draft, remove the linked mileage line from the review page before correcting it. Issued invoices remain immutable and require the existing billing adjustment process. Editing an approved reading resets its cost approval.
- New time entries store zero mileage; call mileage is approved and costed separately. Old v1.6 mileage on time entries remains intact, and the app blocks adding duplicate call mileage when legacy entries already contain miles.
- Clock entries longer than 24 hours are saved and flagged for office review. New tables are additive; existing v1.6 time rows are not rewritten. PostgreSQL integration and phone-browser checks remain part of private deployment testing.
- The v1.8 admin catalog and margin workflow below builds on this time and mileage release.

### Resuming the existing Google setup

Keep the existing Cloud Shell `infra/terraform.tfstate`, plan files, credentials and generated secrets private and intact. Do not extract this package over generated state. Copy only the updated `app/` source into the existing deployment folder, then rebuild with `crm:v1-10-private` and use its new immutable digest in the service plan. The previously successful v1.6 image does not contain this update. No cloud changes were made while implementing this release.

Existing explicit cost authorization remains valid within its approved scope; the generic approval checkpoints below apply when authorization has not already been given.

## Private design

- IAP on Cloud Run requires Google sign-in and permits only the exact `tester_email` entered by the owner. Cloud Run's invoker binding contains only the IAP service agent; no `allUsers`, `allAuthenticatedUsers`, or domain-wide grants are included.
- Ingress is internet-reachable **behind IAP**, so an iPhone can reach the sign-in page. This is identity-restricted access, not a VPN-only endpoint. Do not replace IAP with an unrestricted public service.
- PostgreSQL 16 uses Cloud SQL Enterprise `db-f1-micro`, one zone, 10 GiB SSD, daily backups retained seven days, deletion protection, and **no public IP**. Application connections use TLS over the private VPC. This small test tier has no HA and is not sized for production.
- Cloud Run uses Direct VPC egress, no always-on VPC connector, no NAT, no load balancer, minimum zero and maximum one instance, concurrency eight, 1 CPU, and 512 MiB RAM. Maximum instances is a cost control, not a hard budget cap.
- Dedicated application and bootstrap identities can read only their required secrets. Runtime cannot read the owner bootstrap password. The database user owns the test schema; production privilege separation and migrations remain future work.
- Application origin/host allowlists use the deterministic Cloud Run hostname. The alternate hashed hostname will be rejected by the app. Secure cookies and same-origin writes remain enabled.
- Hosted photo/receipt uploads remain disabled until the v1.10 private durable-storage configuration is approved and supplied. Gmail, bank feeds and payment integrations remain unconfigured. Existing one-job-per-PO behavior is preserved.
- Database and service deletion protection are enabled. Disk auto-growth is disabled to avoid unapproved storage growth; monitor space during testing.

## 1. Read-only preflight in an authenticated Cloud Shell

Use the Google account that administers this project. Do not paste credentials into chat.

```bash
gcloud auth list
gcloud projects describe limitless-crm-512612
gcloud billing projects describe limitless-crm-512612
gcloud services list --enabled --project=limitless-crm-512612
gcloud run services list --project=limitless-crm-512612 --region=us-central1
gcloud sql instances list --project=limitless-crm-512612
```

Check for existing resources with these names before applying. If any exist, inspect and import the intended resources into state; do not overwrite them or create duplicates. Inspect inherited IAM for broad Cloud Run/IAP grants; service-local policies cannot remove inherited permissions. Do not enable billing or change an organization policy implicitly.

Verify whether the project belongs to your Workspace Google Cloud organization. Workspace email alone does not prove organization membership. Managed IAP OAuth works for users in the same organization. Projects with no organization or users outside it require the custom OAuth setup in Google's IAP guide. Keep `iap_ready=false` until that is verified. Never disable IAP to get past an OAuth error.

## 2. Review infrastructure, then request paid-resource approval

Upload/unzip this bundle privately in Cloud Shell. Terraform >=1.6 and the gcloud CLI are required. Authentication must permit resource management and Terraform ADC. Cloud Shell may supply ADC; if necessary use `gcloud auth application-default login` interactively, without sharing tokens.

```bash
cd infra
terraform init
terraform validate
terraform plan -out=infra.plan
terraform show -no-color infra.plan
```

Review region, billing, all additions, shared-network peering ranges, role grants, and the current price estimate. The plan enables APIs when applied; the plan itself does not create cloud infrastructure. Stop for user cost approval here. Keep state, plan files and all generated `.terraform` files private: Terraform state contains generated passwords and secret values. No remote state bucket is automatically created.

## 3. After explicit cost approval only: provision and build

```bash
terraform apply infra.plan
cd ..
gcloud builds submit app \
  --project=limitless-crm-512612 \
  --tag=us-central1-docker.pkg.dev/limitless-crm-512612/lms-test-images/crm:v1-10-private
gcloud artifacts docker images describe \
  us-central1-docker.pkg.dev/limitless-crm-512612/lms-test-images/crm:v1-10-private \
  --project=limitless-crm-512612 --format='value(image_summary.digest)'
```

The Cloud Build identity needs repository write permission and logging/storage permissions appropriate to the project's build configuration. Inspect the actual selected build identity and grant only missing permissions; do not grant Editor or Owner. Builds use `.gcloudignore` as well as `.dockerignore`; both exclude customer databases and secrets. Capture the immutable digest returned by the build/image lookup.

## 4. Review and apply the private service plan

```bash
cd service
cp terraform.tfvars.json.example terraform.tfvars.json
```

Edit this file: enter the complete `us-central1-docker.pkg.dev/.../crm@sha256:...` image reference and your exact Google account email. Set `iap_ready=true` only after completing the organization/OAuth check. Then:

```bash
terraform init
terraform validate
terraform plan -out=service.plan
terraform show -no-color service.plan
terraform apply service.plan
terraform output -raw testing_url
```

Review the plan before confirming. IAP is enabled on the first CRM deployment, with no anonymous-invocation grant. The bootstrap job is created but is not run by Terraform. The runtime startup creates the v1.10 schema in the empty PostgreSQL database; production needs reviewed versioned migrations. The database URL is pinned to secret version 1; future credential rotation requires updating service/job secret references explicitly.

## 5. Create the application owner and verify access

```bash
gcloud run jobs execute lms-crm-test-bootstrap \
  --project=limitless-crm-512612 --region=us-central1 --wait
```

The app username is `david`, role `owner`, PO prefix `DR`. Retrieve `lms-test-owner-password` in Secret Manager privately, then store it in your password manager. Do not paste it into chat or logs. The bootstrap job refuses to overwrite an existing owner. Disable its ability to read the password after setup, or remove the job and its secret grant from Terraform before the next apply. Retain the separate initial-password secret only as long as needed.

Verify before calling the CRM ready:

1. Signed-out/incognito visit to the testing URL redirects to Google sign-in and does not return the CRM or API records.
2. An unapproved Google account is denied; your allowlisted account can pass IAP.
3. The app still requires its own `david` login. Google sign-in is an outer access gate; it does not replace application roles.
4. `/api/health` works after Google authentication. Browser create/edit operations pass same-origin checks and persist after a new revision.
5. Create a test customer, job and PO; confirm each PO belongs to one specific job. Exercise dispatch and time entry. Confirm uploads return the configured unavailable message.
6. Inspect Cloud SQL: no public address. Inspect Cloud Run: IAP enabled, invoker IAM enforced. Inspect service and inherited project IAP/IAM policies for broad grants.
7. Verify backups complete successfully and configure a billing alert after choosing a threshold. Confirm a private backup restoration before production.

The original historical snapshot has not been imported. Start empty for initial testing. If requested later, use the original v1.6 archive's dry-run importer on a trusted private runner with database connectivity; review record counts and destination emptiness before committing. Never place the snapshot in an image or build source.

## Costs and shutdown

Scaling Cloud Run to zero does not stop Cloud SQL billing. Keep the test database running only for the approved testing period. Stopping Cloud SQL can reduce compute charges but storage/backup charges remain; deleting it requires a reviewed backup and disabling deletion protection intentionally. Inspect retained build objects, image versions, secrets, backups and logs when ending the test. Do not run a blanket project deletion or `terraform destroy` without explicit authorization.

## Validation and limits

See `VALIDATION.txt` for actual checks performed. Local tests do not establish live Google access, project permissions, IAP OAuth readiness, Cloud SQL networking, image builds, or a successful deployment. These require authenticated verification. No cloud credentials are included.

Official references:

- https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run
- https://docs.cloud.google.com/run/docs/configuring/vpc-direct-vpc
- https://docs.cloud.google.com/run/docs/triggering/https-request
- https://cloud.google.com/sql/pricing
- https://cloud.google.com/run/pricing
