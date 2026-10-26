#!/usr/bin/env bash
# First link the private GitHub repository in Cloud Build > Repositories.
# Supply the exact full repository resource returned by that connection.
set -euo pipefail
repo_resource="${1:?Usage: bash deploy/create-trigger.sh projects/PROJECT/locations/REGION/connections/CONNECTION/repositories/REPOSITORY}"
[[ "$repo_resource" =~ ^projects/limitless-crm-512612/locations/us-central1/connections/[^/]+/repositories/[^/]+$ ]] || { echo 'Expected a connected repository in limitless-crm-512612 / us-central1.'; exit 1; }
gcloud builds triggers create github \
  --project=limitless-crm-512612 \
  --region=us-central1 \
  --name=lms-crm-release \
  --repository="$repo_resource" \
  --branch-pattern='^release$' \
  --build-config=cloudbuild.yaml \
  --service-account=projects/limitless-crm-512612/serviceAccounts/lms-crm-build@limitless-crm-512612.iam.gserviceaccount.com \
  --description='Test, build and release the existing Limitless CRM from the release branch'
