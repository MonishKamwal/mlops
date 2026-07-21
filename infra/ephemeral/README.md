# infra/ephemeral

The **throwaway** Terraform root (Phase 3): a VPC + EKS cluster that gets created,
exercised, evidenced, and destroyed on a schedule — nothing here is meant to live
between runs.

Kept as a **separate root** from `infra/persistent` (separate state key,
`ephemeral/terraform.tfstate`) on purpose: `terraform destroy` here can never touch the
data buckets, ECR, IAM, or the live Lambda. Everything is tagged `tier=ephemeral` so the
failsafe sweeper can find and delete anything that outlives its run.

- `vpc.tf` — dedicated VPC, 2 AZ, **single NAT** (cost), EKS subnet tags.
- `eks.tf` — EKS v21 (K8s 1.33), one managed node group, **2× t3.medium SPOT**, public
  API endpoint (for CI kubectl), cluster-creator admin access entry.
- `outputs.tf` — `cluster_name` / `region` for `aws eks update-kubeconfig`.

## Not applied by hand

`terraform apply` runs from CI (`eks-demo.yml`), which pairs every `apply` with an
`if: always()` `destroy`, plus a scheduled `eks-failsafe.yml` that destroys
unconditionally. **Do not `apply` this locally** without a plan to tear it back down —
a leaked cluster is ~$8/day of credits.

```bash
# safe to run locally (no AWS calls): validates the config
terraform init -backend=false && terraform validate
```
