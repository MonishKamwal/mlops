# AWS provider 6 is required by the EKS module v21 (>= 6.52); it matches the
# persistent root's `~> 6.0`. The EKS module also pulls in the tls/time providers
# transitively — Terraform installs those from the module's own requirements, so they
# don't need declaring here.
terraform {
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
