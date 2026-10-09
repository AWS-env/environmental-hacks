# Synthetic fixture (INF10-05): Terraform is not supported by INF-10 v1.
resource "aws_s3_bucket" "logs" {
  bucket = "app-logs"
}
