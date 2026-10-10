"""AWS Lambda `owner-d-telemetry-analyzer`: read-only CloudWatch metrics for Owner D checks (INF-01, OBS-06, LLM-17).

Collects CloudWatch metrics in the client's project (optionally through a read-only role), normalizes them
per check (registry.py), evaluates the contract v1 detectors and publishes detector.result.v1 events to
the findings-hub bus. INF-01 reads CPUUtilization Average/Maximum per EC2 instance or ECS service; Lambda
functions are listed but publish no CPU utilization, so they stay unevaluated with a limitation. LLM-17 reads
the utilization of the fixed agent/inference capacity listed in `agent_capacity` (nothing without it).

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
     "agent_capacity": [{"type": "ecs", "cluster": "agents", "service": "worker", "provisioned_capacity": 6,
                         "capacity_unit": "task", "autoscaling": false},
                        {"type": "lambda", "name": "agent-fn", "qualifier": "live"},
                        {"type": "custom", "name": "agent-pool", "namespace": "OwnerD/Demo",
                         "metric_name": "AgentWorkerUtilization", "dimensions": {"path": "waste"}}],  # LLM-17
     "settings": {"INF-01": {"min_window_days": 14, "min_sample_count": 100,
                             "average_utilization_threshold": 0.1, "peak_utilization_threshold": 0.5}},
     "scope_per_payload": 50, "dry_run": false}
    {"probe": ["cpu_metrics"], ...}  -> collect only, return counts, publish nothing
"""
from __future__ import annotations

from owner_d.aws import common, metrics

ANALYZER = "owner-d-telemetry-analyzer"
SOURCE = "owner-d.telemetry-analyzer"
COLLECTORS = {"cpu_metrics": metrics.collect_cpu_metrics, "metrics": metrics.collect_list_metrics,
              "capacity_metrics": metrics.collect_capacity_metrics}


def lambda_handler(event, context=None):
    return common.run_analyzer(event, context, analyzer=ANALYZER, source=SOURCE, collectors=COLLECTORS)
