# Limitless Mechanical — three-person rollout and mock-customer trial

This is a readiness checklist, not a claim that the Google deployment or employee accounts already exist. No truck inventory or QR-code feature is required for this pilot.

## 1. Deploy the current source privately

- Resume at a PC and verify project `limitless-crm-512612`, the correct signed-in Google account, billing and existing Cloud SQL state.
- Preserve existing Terraform state, generated secrets and database contents. Follow the parent README to copy the application source, build the new image and review the deployment plan before applying within the authorized budget.
- Verify HTTPS/IAP is active and unsigned/unapproved identities cannot reach the CRM. Confirm PostgreSQL has no public IP and the app reaches it privately.
- Confirm application/database schema startup succeeds and the new version is available. A successful old v1.6 image does not contain this update.

## 2. Configure three individual users

| Person | App access | Google/IAP access |
| --- | --- | --- |
| David | Existing owner/full admin | Existing approved Google identity |
| Additional employee: admin | Full admin, financial tools and team management | Their exact Google email |
| Additional employee: technician | Assigned calls and field entry; no admin pricing/margins | Their exact Google email |

- Enter the two employees' actual names, usernames, unique passwords and Google emails on **Review tools & sites → Team accounts** after the private app is available.
- Add those exact Google emails to `additional_tester_emails` and, where required, the custom OAuth client's test-user audience. Review/apply only the intended identity changes. Do not grant domain-wide or anonymous access.
- Verify each employee signs in with their own identity and app login. Do not share David's credentials.
- Verify the admin can schedule, review prices/costs and use billing. Verify the technician sees only assigned calls and cannot retrieve admin APIs by URL.
- Configure unique PO buyer designators through the existing owner-managed buyer setup only for people authorized to create POs; leave them unassigned otherwise. Each PO remains tied to one job.
- Confirm the disable-account process revokes the app session, and remove Google allowlist access separately when offboarding.

## 3. Make photos durable before relying on them

- Approve any additional storage cost before creating a bucket or other paid resource.
- Configure the private upload bucket, required runtime permissions, uniform bucket access and enforced public access prevention. Supply both durable-upload environment settings from the README.
- Upload a photo and purchasing receipt. Reload/sign out/sign in and confirm the files remain available only to authorized users. Confirm PDF images/signatures load.
- Verify file recovery/retention independently of PostgreSQL; database backups alone do not back up photo bytes.

## 4. Enter the operating data

- Configure each customer's shared billing address, named service sites and site contacts.
- Add equipment to its actual service site, including tags, models, serials and known warranty terms/dates.
- Enter verified loaded labor assumptions, vehicle costs, customer rates, minimum charges, parts costs and desired margin targets. Avoid counting freight/tax twice.
- Choose any favorite parts/bundles and repeat-system alert thresholds. Import historical data only after a reviewed dry run; the new package does not silently import older spreadsheets.

## 5. Run a complete sample call on the actual phones

1. Use one customer with two differently named service addresses and one shared bill-to address. Create a job/call at each site and verify the correct site/equipment appears.
2. Assign a call to the technician. Verify the other unassigned call remains inaccessible.
3. Clock in, leave/reopen the browser and verify the timer continues. Record start/finish odometer, parts used, work performed, a customer recommendation, an internal note and a photo.
4. Complete optional relevant checks. Verify internal notes/checklists never enter the customer report. Verify a system's warranty and additional-work status can be viewed.
5. Create a repair quote from the recommendation. For additional work, record actual customer authorization before doing it. Verify saving alone does not send or accept a document.
6. Clock out, complete the call, approve hours/mileage and match actual purchased parts where applicable. Confirm no duplicate receipt cost or mileage charge.
7. Review estimated/projected/actual costs, the whole-percent margin target and the parts/billing checklist. Create the draft invoice, verify both central Bill To and actual Service Location, then issue only after review.
8. Record a test payment and confirm balance/customer history. Do not use a real payment processor; none is connected.
9. Link a sample callback, verify its cost reduces original-call family profit, and verify the system flag when the test threshold is reached. Restore the desired thresholds afterward.
10. Preview/export an approved payroll week and review any cross-week/overtime warnings. Verify payroll output includes hours, not fabricated wages or withholding.

## 6. Verify recovery and pilot ownership

- Confirm the real backup schedule and retention. Perform a restore exercise in an approved test environment before depending on the system. A new cloud restore instance can be billable and needs approval within the cost constraint.
- Name the admin responsible for daily unbilled-call, approval and follow-up review.
- Keep a short list of pilot issues and verify a small number of real calls end to end before expanding use. Customer email automation, SMS/push and payment integrations are optional later work, not prerequisites for this private pilot.

## 7. Spreadsheet and Wave cutover

- Keep the spreadsheet available for a parallel comparison until customers/sites, job/PO totals, hours, mileage, costs and balances agree. Local automated tests do not prove live rollout reliability.
- During the pilot, designate Wave as the authority for real invoices and use CRM drafts for comparison. Do not issue/send duplicate charges. Decide a specific cutover date and the system of record for old unpaid invoices before switching.
- If moving invoicing into the CRM, verify actual invoice delivery, payment recording and customer balances. Gmail sending and online card/ACH processing are not connected by this package. Keep Wave for any functions still needed.
- Download the accountant CSV package for a sample year and review its scope/unknown fields with your accountant. Keep bank/card records, actual payroll and other company expenses alongside it; internal job-profit estimates are not complete tax books.

## v1.11 end-to-end trial with mock customers

Use the normal CRM with clearly named mock customers; no real customer records are needed.

1. Configure invoice company/remittance details; create a mock customer with two named service sites and one shared Bill To.
2. Create individual admin and technician accounts, assign a mock call and verify financial APIs are denied to the technician.
3. Run the technician clock including travel, enter start/end odometers, parts, customer/internal descriptions/recommendations, photos and equipment checks. Exercise Waiting for Parts, then Finish call.
4. On a trusted phone, open a call online first. Disconnect, retain notes/photos and captured time, reconnect with the same user, sync and resolve conflicts. Verify reload, IAP reauthentication, device storage and mobile-browser behavior before relying on offline recovery.
5. Approve hours/mileage/costs, check the 35% whole-percent pricing target, create quote options, revise a quote to invalidate an older approval link, then record acceptance using a fresh preview.
6. Generate an itemized draft invoice, reserve a number, add customer PO and service dates, issue it and verify the frozen Bill To/site/remittance PDF.
7. Prepare a preview email, retry the preparation and verify there is only one outbox message. After mailbox authorization, send only to an address you control; inspect the outbox and Sent Mail. Exercise explicit resend and manually resolve a mocked/provider-uncertain result without blind retries.
8. Receive one check covering two invoices, partially allocate it, confirm unapplied funds, deposit it, and test return/reversal with balances reopening.
9. Test due reminders, a paused invoice and a fully paid invoice; no inappropriate follow-up should be sent. Worker scheduling remains a deployment step.
10. Create a maintenance template, generate due visits twice without duplicates, review renewal attention, upload a system manual and test assigned/unassigned technician access.
11. Submit a customer service request preview, convert it only to a verified same-site job, and test revoked/expired links. External portal deployment is optional and separate from the IAP-protected staff app.
12. Validate a mock Wave opening balance, reject duplicate references/numbers, commit once, and verify it is excluded from new sales in the accountant source export. Reconcile check receipts against reference-only allocations and returns.

The target is one usable CRM for mock runs followed by real work. No public portal, scheduled worker, mailbox connection, cloud build or paid resource is activated by this source update.

## v1.12 overview check

Open Business overview as admin and verify that a technician/unsigned request is denied. Exercise Day/Week/Month/Year/Custom and previous/next dates on desktop and phone. Confirm Monday weeks, local dates, partial-period labeling and the chart data table. Reconcile an issued invoice, partial check, unapplied check, allocation, deposit, returned check and refund against the cards. Confirm overdue/upcoming rows use the displayed as-of date. Receive a PO and match a vendor bill; verify the amount is not counted twice. Review cost/margin coverage before interpreting a period result, and compare the downloaded CSV to the visible chart.

## v1.13 proposal appearance check

Open Proposals and confirm there is no layout selector or command requirement. Export a service repair, a large project with alternatives, a maintenance recommendation and an older simple proposal: each customer PDF should use the same primary layout and reference logo. Verify actual scope, equipment, warranty, terms and option prices; check odd-cent deposits sum to each option total. Inspect every page and confirm no internal cost fields appear. Verify a revised draft advances its revision and an accepted quote remains noneditable. Validate the logo/fonts inside the rebuilt container. Select the confirmed customer folder and filename before saving any finalized real proposal to shared Drive; the CRM does not auto-file it in this release.

- v1.14: Review a mock invoice PDF with the configured remit address and terms. Confirm the emailed attachment matches the new compact branding; previously saved attachments are retained.
