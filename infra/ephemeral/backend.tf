# Ephemeral state lives in the SAME hand-made bucket as persistent, but under a
# separate key — so `terraform destroy` here (weekly, and via the failsafe) physically
# cannot touch persistent state, data, or the live API. Backend config is resolved
# before variables, so bucket/region must be literals. use_lockfile = native S3 state
# locking (no DynamoDB), requires Terraform >= 1.11.
terraform {
  backend "s3" {
    bucket       = "mlops-quickdraw-tfstate-k7f2"
    key          = "ephemeral/terraform.tfstate"
    region       = "us-east-2"
    use_lockfile = true
  }
}
