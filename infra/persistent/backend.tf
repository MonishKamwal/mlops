# Backend config is resolved before variables, so bucket/region must be literals.
# The bucket is the one hand-created in the S3 Console (versioning on) — Terraform
# cannot create the bucket its own state lives in. use_lockfile (native S3 state
# locking, no DynamoDB table) requires Terraform >= 1.11.
terraform {
  backend "s3" {
    bucket       = "REPLACE_ME" # hand-made tfstate bucket, e.g. mlops-quickdraw-tfstate-xxxx
    key          = "persistent/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}
