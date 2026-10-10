"""AWS Lambda `owner-d-telemetry-analyzer`: read-only CloudWatch metrics for Owner D checks (INF-01, OBS-06,
INF-04).

Collects CloudWatch metrics in the client's project (optionally through a read-only role), normalizes them
per check (registry.py), evaluates the contract v1 detectors and publishes detector.result.v1 events to
the findings-hub bus. INF-01 reads CPUUtilization Average/Maximum per EC2 instance or ECS service; Lambda
functions are listed but publish no CPU utilization, so they stay unevaluated with a limitation.
INF-04 reads AWS/Lambda Invocations (Sum) for the Lambda functions listed in `resources` (idle functions
cannot be discovered through ListMetrics); without any listed function it reads nothing and is skipped.

Event:
    {"repository_id": "github:o/r", "commit_sha": "<40 hex>", "scan_id": "optional",
     "role_arn": "arn:aws:iam::<client>:role/owner-d-telemetry-readonly", "external_id": "optional",
     "checks": ["INF-01"],                                          # default: every registered metrics check
     "resources": [{"type": "ec2", "id": "i-0abc12345678def00", "provisioned_capacity": 2, "capacity_unit": "vcpu"},
                   {"type": "ecs", "cluster": "web", "service": "api"},
                   {"type": "lambda", "name": "orders"}],
     "discover": {"types": ["ec2", "ecs", "lambda"], "max_resources": 50, "recently_active": true},
     "window": {"lookback_days": 15, "start": "<ISO>", "end": "<ISO>", "period_seconds": 3600},
     "list_metrics": {"namespace": "OwnerD/Demo", "max_pages": 5},   # metrics source (OBS-06)
     "invocations": {"lookback_days": 30, "period_seconds": 3600},  # invocation_metrics source (INF-04)
     "settings": {"INF-01": {"min_window_days": 14, "min_sample_count": 100,
                             "average_utilization_threshold": 0.1, "peak_utilization_threshold": 0.5},
                  "INF-04": {"min_idle_days": 14}},
     "scope_per_payload": 50, "dry_run": false}
    {"probe": ["cpu_metrics"], ...}  -> collect only, return counts, publish nothing
"""
from __future__ import annotations

from owner_d.aws import common, metrics

ANALYZER = "owner-d-telemetry-analyzer"
SOURCE = "owner-d.telemetry-analyzer"
COLLECTORS = {"cpu_metrics": metrics.collect_cpu_metrics, "metrics": metrics.collect_list_metrics,
              "invocation_metrics": metrics.collect_invocation_metrics}


def lambda_handler(event, context=None):
    return common.run_analyzer(event, context, analyzer=ANALYZER, source=SOURCE, collectors=COLLECTORS)
