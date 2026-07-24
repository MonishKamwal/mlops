# The always-on serving tier (PLAN.md Phase 1, task 4): the FastAPI/onnxruntime
# container image running on Lambda behind a Function URL. Well inside the
# always-free tier (1M requests + 400k GB-s per month), and true scale-to-zero.
#
# Chicken-and-egg note: Lambda validates image_uri at creation, so the image must
# be pushed to ECR *before* the first apply of this file (manual push in Phase 1;
# CI owns pushes from Phase 2 on).

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api_exec" {
  name               = "quickdraw-api-exec"
  description        = "Execution role for the serving Lambda: CloudWatch logs + prediction-log writes to S3."
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "api_exec_logs" {
  role       = aws_iam_role.api_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Prediction + feedback + capture logging is append-only by construction: PutObject on the
# predictions/, feedback/, and captures/ prefixes and nothing else — the API can't read, list,
# or delete what it wrote, and can't touch anything else in the bucket.
data "aws_iam_policy_document" "api_prediction_logs" {
  statement {
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.logs.arn}/predictions/*",
      "${aws_s3_bucket.logs.arn}/feedback/*",
      "${aws_s3_bucket.logs.arn}/captures/*",
    ]
  }
}

resource "aws_iam_role_policy" "api_prediction_logs" {
  name   = "prediction-logs-put"
  role   = aws_iam_role.api_exec.id
  policy = data.aws_iam_policy_document.api_prediction_logs.json
}

# Created ahead of the function: if Lambda auto-creates this group, its logs
# never expire — retention is the cost guardrail here.
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/quickdraw-api"
  retention_in_days = 14
}

resource "aws_lambda_function" "api" {
  function_name = "quickdraw-api" # matches the gha-app role's quickdraw-* deploy scope
  role          = aws_iam_role.api_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"

  # Must match the pushed image's platform. arm64: local (Apple Silicon) builds
  # produce it natively, GitHub's free arm64 runners cover Phase 2, and it prices
  # ~20% below x86_64 if the account ever leaves the free plan.
  architectures = ["arm64"]

  # Lambda allocates CPU proportionally to memory (1769 MB = 1 vCPU). 1024 MB
  # keeps cold starts (uvicorn boot + ONNX session init) snappy while 400k GB-s
  # of always-free compute still covers ~390k seconds a month at this size.
  memory_size = 1024
  timeout     = 30

  # Image defaults still rule for anything the image can know (MODEL_PATH, PORT,
  # readiness path). PREDICTION_LOG_BUCKET is different: only infra knows the
  # bucket's name, and its *presence* switches logging on — docker run and tests
  # stay AWS-free by simply not setting it.
  environment {
    variables = {
      PREDICTION_LOG_BUCKET = aws_s3_bucket.logs.bucket
    }
  }

  # Phase 2 CI deploys by lambda:UpdateFunctionCode after its quality gate, so
  # the tag recorded here is only the bootstrap image; without this, every apply
  # would roll the function back to it.
  lifecycle {
    ignore_changes = [image_uri]
  }

  depends_on = [aws_cloudwatch_log_group.api]
}

# authorization_type NONE makes the URL public, but invocation still needs an
# explicit resource policy — the Console adds this silently; Terraform must not.
resource "aws_lambda_permission" "api_public_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"

  # CORS lives here, not in the app (PLAN.md §2): doubled CORS headers break
  # browsers, and the Function URL strips/owns them for the Lambda tier.
  cors {
    allow_origins = [
      "https://monishkamwal.github.io",
      "http://localhost:3000", # portfolio dev server (task 5)
    ]
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}
