resource "aws_cloudwatch_log_group" "bedrock" {
  name              = "/aws/bedrock/invocations"
  retention_in_days = 30
}

resource "aws_bedrock_model_invocation_logging_configuration" "this" {
  logging_config {
    text_data_delivery_enabled = true
    cloudwatch_config {
      log_group_name = aws_cloudwatch_log_group.bedrock.name
      role_arn       = var.bedrock_logging_role_arn
    }
  }
}
