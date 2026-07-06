# infra/persistent — the always-on, ~$0 footprint

Everything that must exist for the platform to function between EKS demo runs:
the data/state and logs buckets, the ECR repo, the GitHub OIDC trust that lets
CI deploy without stored AWS keys, and the serving Lambda + Function URL (the
live API the canvas frontend calls). Applied manually from a laptop —
deliberately not from CI — and kept physically separate from `infra/ephemeral/`
(Phase 3) so the weekly create/destroy automation can never touch this tier.

## One-time bootstrap (before first `init`)

1. S3 Console → create the state bucket: suggested name
   `mlops-quickdraw-tfstate-<4 random chars>`, region `us-east-1`,
   **versioning enabled**, everything else at its secure default. Terraform
   cannot create the bucket its own state lives in.
2. Put the real bucket name in `backend.tf` (replace `REPLACE_ME`).

## Usage

```sh
terraform init
terraform plan
terraform apply
terraform apply   # second run must be a no-op (Phase 0 DoD: idempotence)
```

Requires Terraform >= 1.11 (native S3 state locking) and credentials for a
non-root IAM identity.

**Image before function:** Lambda validates `image_uri` at creation, so the
serving image must be in ECR before the apply that first creates
`aws_lambda_function.api`. Phase 1 does this push manually (arm64 build →
`docker push <ecr_repository_url>:latest`); Phase 2 CI takes over, updating
the function code out-of-band — Terraform ignores `image_uri` drift by design.

## Deliberately absent (for now)

- **`gha-infra` role** for the ephemeral EKS workflow — Phase 3.
- **Budgets / billing alarm** — one-time admin, created via Console (Phase 0
  task 3); the billing alarm is deferred until a paid-plan upgrade.

Outputs map to the GitHub repo variables `AWS_REGION` and `GHA_APP_ROLE_ARN`.
