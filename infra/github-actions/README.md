# CI/CD pipeline configuration

The workflows live in [.github/workflows](../../.github/workflows):

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` | pull requests; called by staging deploy | lint, typecheck, tests, container builds |
| `deploy-staging.yml` | push to `main` | gate → build/push → migrate staging → deploy → smoke |
| `deploy-production.yml` | manual dispatch + approval | promote a staging-verified tag; migrations only via explicit input |

## Workload Identity Federation (one-time)

GitHub Actions authenticates to Google Cloud without stored keys:

```bash
PROJECT_ID=<project>
REPO="dhruvb2028/Al-Voice-Receptionist-Platform"

gcloud iam workload-identity-pools create github \
  --project="$PROJECT_ID" --location=global \
  --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github-oidc \
  --project="$PROJECT_ID" --location=global \
  --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '$REPO'"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

gcloud iam service-accounts add-iam-policy-binding \
  "receptionist-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${REPO}"
```

Then set the repository variable `GCP_WORKLOAD_IDENTITY_PROVIDER` to:

```
projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-oidc
```

## Deployment order per environment

migrate database → deploy services → smoke-check `/healthz` (rollback on failure). Migrations must stay backward-compatible with the running revision (expand → migrate → contract).
