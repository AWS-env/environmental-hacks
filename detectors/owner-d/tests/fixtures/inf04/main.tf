# Synthetic fixture (INF04-05): Terraform is not supported by INF-04 v1.
resource "aws_sqs_queue" "old_jobs" {
  name = "old-jobs"
}
