resource "aws_ecr_repository" "api" {
  name = "quickdraw-api"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# ECR free tier is 500 MB; keeping only the newest images stays inside it.
resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the 3 most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 3
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
