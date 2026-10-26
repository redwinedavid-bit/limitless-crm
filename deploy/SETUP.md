# Limitless CRM — automatic release setup

Status: deployment files prepared and locally checked; NOT connected or activated.
No GitHub repository, Cloud Build trigger, service account or live revision was created by this preparation.
Existing target: limitless-crm-512612 / us-central1 / lms-crm-test.

## Intended workflow

Work on `main` or feature branches. Review the update and merge it into `release` when ready.
A push to `release` runs Python and JavaScript checks, builds/pushes a uniquely tagged image,
resolves its digest, creates a revision without traffic, verifies readiness and retained runtime
configuration, then sends traffic to that revision. Normal edits do not deploy.
Only trusted maintainers should have write access. Use branch protection/review rules where
supported by your GitHub plan. Release one update at a time; wait for the preceding Cloud Build
run to finish before merging another release. This pipeline detects some concurrent changes,
but does not implement a distributed deployment lock or guarantee ordering of simultaneous builds.

The source repository is private. No database records, uploads, OAuth client JSON, service-account
keys, passwords, Terraform state or local deployment variable files belong in it.
The repository root must contain `app/`, `deploy/` and `cloudbuild.yaml`, not an additional parent folder.

## One-time connection steps

1. Connect the GitHub integration in ChatGPT. Create a private repository named
   `limitless-crm` under the confirmed account or organization. Commit this vetted source
   tree, with the archive's root directory as repository root. Do not upload the entire Cloud
   Shell home directory. Create `main` and `release` branches before enabling the trigger.
2. In authenticated Cloud Shell, use the prepared source directory and run:

   ```bash
   bash deploy/setup-build-account.sh
   ```

   This creates `lms-crm-build` if absent and grants Cloud Run Developer on the existing
   service, Artifact Registry Writer on the existing image repository, Service Account User
   on the runtime identity, and project Logs Writer. It creates no downloadable key and
   grants no database, Secret Manager or uploaded-file access directly to the build account.
   Deployment rights can run code as the runtime identity, so repository write access is trusted access.
3. Update the EXISTING remote Terraform service configuration so it stops managing the image:

   ```bash
   python3 deploy/terraform-image-ownership.py "$HOME/limitless-release-lGIxCa/limitless-crm-google-private/service/main.tf.json"
   ~/lms-tools/terraform -chdir="$HOME/limitless-release-lGIxCa/limitless-crm-google-private/service" validate
   ~/lms-tools/terraform -chdir="$HOME/limitless-release-lGIxCa/limitless-crm-google-private/service" plan -input=false
   ```

   Review the plan. Do not apply unexpected replacements or changes. The edit only adds
   `template[0].containers[0].image` to lifecycle ignore_changes and backs up the file.
   Keep the existing service state and all local customization. Do not replace it with the
   initial deployment templates in this repository. Infrastructure remains managed separately;
   Cloud Build owns only app image releases and traffic promotion. Bootstrap job image stays unchanged.
4. In Google Cloud → Cloud Build → Repositories, select project `limitless-crm-512612`,
   region `us-central1`, and a second-generation GitHub connection. Authorize the Cloud Build
   GitHub app for ONLY the private CRM repository, then link that repository. This Google
   Cloud authorization is separate from connecting GitHub to ChatGPT.
5. Copy the connected repository's full resource name (not its GitHub URL), then run:

   ```bash
   bash deploy/create-trigger.sh 'projects/limitless-crm-512612/locations/us-central1/connections/CONNECTION/repositories/REPOSITORY'
   ```

   Substitute the actual connection/repository segments returned by Cloud Build. The script
   creates `lms-crm-release` once. If that name already exists, inspect it instead of creating a duplicate.
   The trigger selects `cloudbuild.yaml`, branch `^release$`, and the dedicated build identity.
6. Run the trigger once against the reviewed `release` commit. Inspect the build logs.
   First deployment needs live verification: login with David, Adrian and Danny; check their
   respective permissions; create a mock service call and confirm uploads and the invoice preview.
   The pipeline checks readiness, not authenticated CRM business workflows.

If repository connection fails with an IAM error, inspect the exact error before adding roles.
Do not grant broad project Owner or Editor to the build account to bypass an error.

## Rollback

Each release logs its previous ready revision. Inspect Cloud Run's revision history and select
an appropriate known-good revision, then shift traffic back to it. Disable the release trigger
while investigating. An image rollback does not undo database changes or data edits, and older
code may not support a later schema. Assess compatibility before rollback; do not restore an
old database automatically. Existing stored files and data are not recreated by this pipeline.

## Checks performed locally

- Build YAML parsed; Python/JS checks precede the Docker build.
- Shell scripts pass Bash syntax checks.
- Five release tests passed: successful promotion; wrong branch/image rejection; not-ready
  revision; changed environment/IAP configuration; concurrent new revision detection.
- Terraform JSON edit preserves unrelated fields, makes a backup and is rerunnable.
  Terraform validate against the real installed provider remains a Cloud Shell setup step.
- App suite previously passed 99 Python tests and 4 JavaScript tests; actual local desktop/mobile
  workflow checks passed. CI adds its own clean-environment run and includes httpx in dev requirements.
- No actual Cloud Build run, IAM setup, repository creation or deployment has been performed yet.

## Official references

- https://docs.cloud.google.com/build/docs/deploying-builds/deploy-cloud-run
- https://docs.cloud.google.com/build/docs/automating-builds/github/build-repos-from-github?generation=2nd-gen
- https://docs.cloud.google.com/sdk/gcloud/reference/builds/triggers/create/github
- https://docs.cloud.google.com/sdk/gcloud/reference/run/services/update
- https://developer.hashicorp.com/terraform/language/syntax/json
