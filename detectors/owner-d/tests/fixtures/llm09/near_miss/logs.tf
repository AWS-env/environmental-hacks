# TODO: aws_bedrock_model_invocation_logging_configuration once the team agrees on retention
resource "aws_cloudwatch_log_group" "app" {
  name              = "/app/assistant"
  retention_in_days = 14
}
