variable "aws_region" {
  description = "Region for all persistent resources. The whole project lives in one region."
  type        = string
  default     = "us-east-1"
}

variable "github_repo" {
  description = "GitHub repository (owner/name) allowed to assume the CI roles via OIDC."
  type        = string
  default     = "MonishKamwal/mlops"
}
