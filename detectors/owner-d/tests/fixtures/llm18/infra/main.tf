# Synthetic fixture for LLM-18: the agent runs in us-east-1, its knowledge base in us-west-2.
variable "aws_region" {
  type    = string
  default = "us-east-1"
}

provider "aws" {
  region = var.aws_region
}

provider "aws" {
  alias  = "west"
  region = "us-west-2"
}

resource "aws_lambda_function" "agent" {
  function_name = "support-agent"
  role          = aws_iam_role.agent.arn
  handler       = "app.handler"
  runtime       = "python3.12"

  environment {
    variables = {
      BEDROCK_REGION = "us-west-2"
      LOG_LEVEL      = "INFO"
    }
  }
}

resource "aws_opensearchserverless_collection" "kb" {
  provider = aws.west
  name     = "support-kb"
  type     = "VECTORSEARCH"
}

resource "aws_opensearchserverless_collection" "audit" {
  name = "support-audit"
  type = "SEARCH"
}
