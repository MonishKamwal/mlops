# S3 bucket names are globally unique; the suffix keeps the obvious names usable.
resource "random_id" "bucket_suffix" {
  byte_length = 2
}

# DVC data + MLflow tracking DB and artifacts. Versioning doubles as the undo
# button for the single-writer MLflow SQLite state (PLAN.md §7).
resource "aws_s3_bucket" "data" {
  bucket = "mlops-quickdraw-data-${random_id.bucket_suffix.hex}"
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Prediction logs (JSONL) written by the serving tier, read by drift reports.
resource "aws_s3_bucket" "logs" {
  bucket = "mlops-quickdraw-logs-${random_id.bucket_suffix.hex}"
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "expire-prediction-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = 180
    }
  }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
