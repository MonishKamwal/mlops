data "aws_caller_identity" "current" {}

# AWS validates GitHub's OIDC tokens against trusted root CAs and ignores the
# thumbprint, but the API still requires a value.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "gha_app_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "gha_app" {
  name               = "gha-app"
  description        = "GitHub Actions app tier: DVC/MLflow state on S3, image push to ECR, Lambda code updates."
  assume_role_policy = data.aws_iam_policy_document.gha_app_trust.json
}

data "aws_iam_policy_document" "gha_app_permissions" {
  statement {
    sid = "DataAndLogsBuckets"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [
      aws_s3_bucket.data.arn,
      "${aws_s3_bucket.data.arn}/*",
      aws_s3_bucket.logs.arn,
      "${aws_s3_bucket.logs.arn}/*",
    ]
  }

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPushPull"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:DescribeImages",
    ]
    resources = [aws_ecr_repository.api.arn]
  }

  # The function itself arrives in Phase 1; scoping by name pattern now keeps
  # this policy stable when it does.
  statement {
    sid = "LambdaDeploy"
    actions = [
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:UpdateFunctionCode",
    ]
    resources = [
      "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:quickdraw-*",
    ]
  }
}

resource "aws_iam_role_policy" "gha_app" {
  name   = "gha-app-permissions"
  role   = aws_iam_role.gha_app.id
  policy = data.aws_iam_policy_document.gha_app_permissions.json
}

# --- Ephemeral EKS lifecycle role (Phase 3) -----------------------------------------
# Kept SEPARATE from gha-app so the broad EKS/EC2/IAM power the Terraform ephemeral root
# needs never lands on the app-deploy role. Used by eks-demo.yml and eks-failsafe.yml.
# Same repo-scoped OIDC trust; the human approval gate is the `eks-demo` GitHub Environment.

data "aws_iam_policy_document" "gha_eks_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "gha_eks" {
  name               = "gha-eks"
  description        = "GitHub Actions ephemeral EKS lifecycle: terraform apply/destroy of infra/ephemeral (VPC + EKS)."
  assume_role_policy = data.aws_iam_policy_document.gha_eks_trust.json
}

data "aws_iam_policy_document" "gha_eks_permissions" {
  # The VPC/EKS modules create and destroy networking, the cluster, node groups, and the
  # control-plane log group. Broad within this service set but bounded to ONE region —
  # persistent holds no EC2/EKS resources there, so there is nothing else to collide with.
  statement {
    sid = "EksClusterLifecycleInRegion"
    actions = [
      "ec2:*",
      "eks:*",
      "elasticloadbalancing:*",
      "autoscaling:*",
      "logs:*",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # IAM is global, so it's bounded by resource instead: this role can only manage roles
  # NAMED for the ephemeral cluster (the module names them `quickdraw-ephemeral-cluster-*`
  # and `quickdraw-ephemeral-*`). It cannot touch gha-app, gha-eks, or any other role —
  # which contains the privilege-escalation surface of "can create IAM roles".
  statement {
    sid = "ManageEphemeralClusterRoles"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:GetRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PassRole",
    ]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/quickdraw-ephemeral*"]
  }

  # iam:GetRole is read-only, and several steps need it on roles OUTSIDE the ephemeral
  # prefix: aws_iam_session_context reads gha-eks itself, and EKS CreateNodegroup validates
  # the AWSServiceRoleForAmazonEKSNodegroup service-linked role. Grant GetRole broadly (it
  # only reveals role metadata) while every *mutating* IAM action stays scoped to
  # role/quickdraw-ephemeral* above.
  statement {
    sid       = "ReadRoleMetadata"
    actions   = ["iam:GetRole"]
    resources = ["*"]
  }

  # The managed node group resolves the EKS-optimized AMI version from AWS's public SSM
  # parameters (/aws/service/eks/optimized-ami/...) — read-only, and only that path.
  statement {
    sid       = "ReadEksOptimizedAmiSsm"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:${var.aws_region}::parameter/aws/service/eks/*"]
  }

  # The cluster's IRSA OIDC provider (per-cluster, name not predictable) + the
  # service-linked roles EKS/EC2/autoscaling create — the latter is inherently
  # constrained to AWS service principals.
  statement {
    sid = "ManageOidcAndServiceLinkedRoles"
    actions = [
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:GetOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider",
      "iam:ListOpenIDConnectProviders",
      "iam:CreateServiceLinkedRole",
    ]
    resources = ["*"]
  }

  # Terraform state + native lock for the ephemeral root (hand-made tfstate bucket).
  statement {
    sid = "EphemeralTerraformState"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["arn:aws:s3:::mlops-quickdraw-tfstate-k7f2/ephemeral/*"]
  }

  statement {
    sid       = "EphemeralTerraformStateList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::mlops-quickdraw-tfstate-k7f2"]
  }

  # Read the digest the Lambda tier is serving, to deploy the identical image to EKS.
  statement {
    sid       = "ReadLiveImageDigest"
    actions   = ["lambda:GetFunction"]
    resources = ["arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:quickdraw-*"]
  }
}

resource "aws_iam_role_policy" "gha_eks" {
  name   = "gha-eks-permissions"
  role   = aws_iam_role.gha_eks.id
  policy = data.aws_iam_policy_document.gha_eks_permissions.json
}
