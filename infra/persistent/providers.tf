provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project    = "mlops-quickdraw"
      tier       = "persistent"
      managed-by = "terraform"
    }
  }
}
