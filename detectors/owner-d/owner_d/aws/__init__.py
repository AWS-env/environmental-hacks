"""AWS Lambda entry points for the Owner D read-only telemetry route (docs/ARCHITECTURE_FLOWS.md section 3).

telemetry_handler  owner-d-telemetry-analyzer  CloudWatch metrics (ListMetrics / GetMetricData)
log_handler        owner-d-log-analyzer        CloudWatch Logs (DescribeLogGroups / Logs Insights)
trace_handler      owner-d-trace-analyzer      AWS X-Ray (GetTraceSummaries / BatchGetTraces)

Detectors stay pure functions over contract v1 payloads. These handlers collect raw telemetry (optionally
through a read-only role), pass it to per-check normalizers (see registry.py), evaluate the check, and
publish detector.result.v1 events to the findings-hub bus.
"""
