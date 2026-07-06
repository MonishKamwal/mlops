variable "aws_region" {
  description = "Region for all persistent resources. The whole project lives in one region."
  type        = string
  default     = "us-east-2"
}

variable "github_repo" {
  description = "GitHub repository (owner/name) allowed to assume the CI roles via OIDC."
  type        = string
  default     = "MonishKamwal/mlops"
}

variable "api_image_tag" {
  description = "ECR tag the serving Lambda is created from. Bootstrap only: Phase 2 CI updates the function code out-of-band and Terraform ignores image_uri drift."
  type        = string
  default     = "latest"
}
