# Synthetic fixture for OBS-10: every ingested log is archived to S3 for audit.
resource "datadog_logs_archive" "audit" {
  name  = "audit-archive"
  query = "*"
  s3_archive {
    bucket     = "example-log-archive"
    path       = "/datadog"
    account_id = "123456789012"
    role_name  = "DatadogArchiveRole"
  }
}
