# v1.15 workflow update

## Changes
- Single-page service call intake: existing/new customer, existing/new service address, problem, priority and optional technician/schedule.
- Automatic job numbering; adding to an existing job is an optional collapsed section.
- Customer list links directly to new calls and sites/billing.
- Separate Team & access, Customer sites & billing, Office review and Exports views.
- Pricing defaults no longer crowd everyday call intake.
- Added missing customer/job read routes used by Operations, with permission checks.
- Lost-response retries reuse the same request, preventing duplicate calls after a successful server commit.
- Desktop and mobile layouts; unchanged technician workspace and customer-facing PDF layouts.

## Validation — October 26, 2026
99 Python/API tests passed; one pre-existing test-client deprecation warning.
4 JavaScript regression tests passed; changed JavaScript syntax checks passed.
Actual local Chromium checks passed: customer/site/call creation, technician assignment,
existing customer with new site, committed request with dropped response and retry,
customer action links, separate team/sites/exports views, desktop and 390px mobile,
and no page JavaScript errors or failing API responses.
Tests used a local SQLite database and sample records, not live customer data.

## Deployment status and limits
Prepared locally; not deployed. Current live version remains v1.14.
Review the interactive HTML preview before an app-image upgrade. The preview uses
sample data in memory and does not access the CRM or send messages.
Retain the existing remote service/infra configuration and Terraform state. Build only
the new app folder, then change the image digest in the existing deployment configuration.
Do not rerun owner bootstrap. Verify the new revision, permissions, call creation and file
uploads in the actual Google Cloud deployment before business use. PostgreSQL and IAP
verification, email transport, backups and end-to-end production readiness are not
established by these local tests. This is a focused office workflow update, not a redesign
of every workspace.
