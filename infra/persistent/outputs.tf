output "aws_region" {
  description = "Set as the AWS_REGION repo variable."
  value       = var.aws_region
}

output "gha_app_role_arn" {
  description = "Set as the GHA_APP_ROLE_ARN repo variable."
  value       = aws_iam_role.gha_app.arn
}

output "data_bucket" {
  description = "DVC remote + MLflow state bucket."
  value       = aws_s3_bucket.data.bucket
}

output "logs_bucket" {
  description = "Prediction-log bucket (JSONL, 180-day expiry)."
  value       = aws_s3_bucket.logs.bucket
}

output "ecr_repository_url" {
  description = "Container image repository for the serving image."
  value       = aws_ecr_repository.api.repository_url
}

output "api_function_url" {
  description = "Public HTTPS endpoint of the serving Lambda (the canvas frontend calls this)."
  value       = aws_lambda_function_url.api.function_url
}
