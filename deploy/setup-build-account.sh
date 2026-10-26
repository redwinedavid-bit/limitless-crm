#!/usr/bin/env bash
# Run once in authenticated Cloud Shell after reviewing. Rerunnable IAM grants.
set -euo pipefail
project=limitless-crm-512612
region=us-central1
service=lms-crm-test
build_sa="lms-crm-build@$project.iam.gserviceaccount.com"
runtime_sa="lms-test-runtime@$project.iam.gserviceaccount.com"
operator="$(gcloud config get-value account)"
[[ -n "$operator" && "$operator" != '(unset)' ]] || { echo 'Sign into Cloud Shell first.'; exit 1; }
# Verify existing targets before changing anything.
gcloud run services describe "$service" --project="$project" --region="$region" --format='value(metadata.name)'
gcloud artifacts repositories describe lms-test-images --project="$project" --location="$region" --format='value(name)'
gcloud iam service-accounts describe "$runtime_sa" --project="$project" --format='value(email)'
existing="$(gcloud iam service-accounts list --project="$project" --filter="email=$build_sa" --format='value(email)')"
if [[ -z "$existing" ]]; then
  gcloud iam service-accounts create lms-crm-build --project="$project" --display-name='Limitless CRM build and release'
fi
gcloud run services add-iam-policy-binding "$service" --project="$project" --region="$region" --member="serviceAccount:$build_sa" --role=roles/run.developer --condition=None --quiet
gcloud artifacts repositories add-iam-policy-binding lms-test-images --project="$project" --location="$region" --member="serviceAccount:$build_sa" --role=roles/artifactregistry.writer --condition=None --quiet
gcloud iam service-accounts add-iam-policy-binding "$runtime_sa" --project="$project" --member="serviceAccount:$build_sa" --role=roles/iam.serviceAccountUser --condition=None --quiet
gcloud projects add-iam-policy-binding "$project" --member="serviceAccount:$build_sa" --role=roles/logging.logWriter --condition=None --quiet
# Allows the current operator to select this identity for the trigger; no key is created.
gcloud iam service-accounts add-iam-policy-binding "$build_sa" --project="$project" --member="user:$operator" --role=roles/iam.serviceAccountUser --condition=None --quiet
printf 'Build identity ready: %s\n' "$build_sa"
