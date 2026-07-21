# tier=ephemeral is the load-bearing tag: the failsafe sweeper (Phase 3 task 4) finds
# and deletes anything carrying it, so a leaked cluster can't outlive its run. Every
# resource in this root inherits these tags.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project    = "mlops-quickdraw"
      tier       = "ephemeral"
      managed-by = "terraform"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}
