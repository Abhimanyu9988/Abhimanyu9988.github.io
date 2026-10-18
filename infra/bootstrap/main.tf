variable "github_repo" { default = "Abhimanyu9988/Abhimanyu9988.github.io" }
variable "region" { default = "eu-central-1" }
provider "aws" { region = var.region }
data "aws_caller_identity" "me" {}

locals {
  acct      = data.aws_caller_identity.me.account_id
  boundary  = "arn:aws:iam::${local.acct}:policy/hero-pipeline-boundary"
  lab_roles = "arn:aws:iam::${local.acct}:role/hero-lab-*"
  instances = ["t3.medium", "t3.large", "t4g.medium", "t4g.large", "m7g.large", "c7g.large"]
}

resource "aws_s3_bucket" "state" { bucket = "hero-pipeline-tfstate-${local.acct}" }
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_iam_openid_connect_provider" "gh" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.gh.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:environment:aws-lab"]
    }
  }
}

resource "aws_iam_role" "lab" {
  name                 = "hero-pipeline-lab"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 7200
  permissions_boundary = aws_iam_policy.boundary.arn
}

resource "aws_iam_policy" "boundary" {
  name = "hero-pipeline-boundary"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "Regional", Effect = "Allow", Resource = "*",
      Action = ["eks:*", "eks-auth:*", "ec2:*", "ecs:*", "ecr:*", "elasticloadbalancing:*",
      "autoscaling:Describe*", "logs:*", "cloudwatch:*", "sqs:*", "events:*", "ssm:GetParameter*",
      "application-autoscaling:*"],
    Condition = { StringEquals = { "aws:RequestedRegion" = var.region } } },
    { Sid = "GlobalRead", Effect = "Allow", Resource = "*",
    Action = ["pricing:*", "sts:GetCallerIdentity", "iam:Get*", "iam:List*"] },
    { Sid    = "TfState", Effect = "Allow",
      Action = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
    Resource = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"] },
    { Sid = "LabRolesMustCarryBoundary", Effect = "Allow", Resource = local.lab_roles,
      Action = ["iam:CreateRole", "iam:PutRolePermissionsBoundary", "iam:AttachRolePolicy",
      "iam:DetachRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy"],
    Condition = { StringEquals = { "iam:PermissionsBoundary" = local.boundary } } },
    { Sid      = "LabRoleLifecycle", Effect = "Allow",
      Resource = [local.lab_roles, "arn:aws:iam::${local.acct}:instance-profile/hero-lab-*"],
      Action = ["iam:DeleteRole", "iam:TagRole", "iam:UntagRole", "iam:CreateInstanceProfile",
        "iam:DeleteInstanceProfile", "iam:AddRoleToInstanceProfile",
    "iam:RemoveRoleFromInstanceProfile", "iam:TagInstanceProfile"] },
    { Sid = "PassLabRolesOnly", Effect = "Allow", Action = "iam:PassRole", Resource = local.lab_roles,
      Condition = { StringEquals = { "iam:PassedToService" = [
    "eks.amazonaws.com", "ec2.amazonaws.com", "pods.eks.amazonaws.com", "ecs-tasks.amazonaws.com"] } } },
    { Sid    = "EksOidcProvidersForIrsa", Effect = "Allow",
      Action = ["iam:CreateOpenIDConnectProvider", "iam:DeleteOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider", "iam:UntagOpenIDConnectProvider"],
    Resource = "arn:aws:iam::${local.acct}:oidc-provider/oidc.eks.${var.region}.amazonaws.com/*" },
    { Sid = "ServiceLinkedRoles", Effect = "Allow", Action = "iam:CreateServiceLinkedRole", Resource = "*",
      Condition = { StringEquals = { "iam:AWSServiceName" = ["eks.amazonaws.com",
    "spot.amazonaws.com", "elasticloadbalancing.amazonaws.com", "ecs.amazonaws.com",
    "ecs.application-autoscaling.amazonaws.com"] } } },
    { Sid = "NoIndirectProvisioning", Effect = "Deny", Resource = "*",
      Action = ["autoscaling:Create*", "autoscaling:Update*", "autoscaling:Put*",
        "autoscaling:SetDesiredCapacity", "autoscaling:AttachInstances",
        "eks:CreateNodegroup", "eks:UpdateNodegroupConfig", "eks:UpdateNodegroupVersion",
        "ec2:CreateFleet", "ec2:RequestSpotFleet", "ec2:ModifySpotFleetRequest",
    "ec2:ModifyFleet", "ec2:RequestSpotInstances"] },
    { Sid = "ProtectBoundaryAndPausePolicies", Effect = "Deny",
      Action = ["iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:SetDefaultPolicyVersion",
      "iam:DeletePolicy"],
    Resource = [local.boundary, "arn:aws:iam::${local.acct}:policy/hero-pipeline-pause"] },
    { Sid    = "ProtectPipelineRoles", Effect = "Deny",
      Action = ["iam:Update*", "iam:Put*", "iam:Delete*", "iam:Attach*", "iam:Detach*", "iam:Tag*", "iam:Untag*"],
    Resource = "arn:aws:iam::${local.acct}:role/hero-pipeline-*" },
    { Sid = "NoEscalation", Effect = "Deny", Resource = "*",
      Action = ["iam:DeleteRolePermissionsBoundary", "iam:CreateUser", "iam:CreateAccessKey",
    "iam:CreateLoginProfile", "organizations:*", "account:*", "billing:*", "budgets:*"] },
    { Sid      = "AllowedInstanceTypesOnly", Effect = "Deny", Action = "ec2:RunInstances",
      Resource = "arn:aws:ec2:*:*:instance/*",
    Condition = { StringNotEquals = { "ec2:InstanceType" = local.instances } } },
    { Sid = "NoNat", Effect = "Deny", Action = "ec2:CreateNatGateway", Resource = "*" }
  ] })
}

resource "aws_iam_role_policy_attachment" "lab" {
  role       = aws_iam_role.lab.name
  policy_arn = aws_iam_policy.boundary.arn
}

resource "aws_budgets_budget" "cap" {
  name         = "hero-pipeline-alert"
  budget_type  = "COST"
  limit_amount = "10"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = ["abhimanyubajaj98@gmail.com"]
    }
  }
}

resource "aws_iam_policy" "pause" {
  name = "hero-pipeline-pause"
  policy = jsonencode({ Version = "2012-10-17",
  Statement = [{ Effect = "Deny", Action = "*", Resource = "*" }] })
}
resource "aws_iam_role" "budget_action" {
  name = "hero-pipeline-budget-action"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow",
  Principal = { Service = "budgets.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "budget_action" {
  role = aws_iam_role.budget_action.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow",
  Action = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"], Resource = aws_iam_role.lab.arn }] })
}
resource "aws_budgets_budget_action" "pause" {
  budget_name        = aws_budgets_budget.cap.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action.arn
  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 80
  }
  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.pause.arn
      roles      = [aws_iam_role.lab.name]
    }
  }
  subscriber {
    address           = "abhimanyubajaj98@gmail.com"
    subscription_type = "EMAIL"
  }
}

output "role_arn" { value = aws_iam_role.lab.arn }
output "state_bucket" { value = aws_s3_bucket.state.bucket }
output "region" { value = var.region }
