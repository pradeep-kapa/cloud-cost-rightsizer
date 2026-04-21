"""
cloudwatch.py — pulls EC2 utilization metrics from CloudWatch.

Fetches CPU, memory (via CloudWatch Agent), and network I/O for a list
of EC2 instances over a configurable lookback window. Returns p50/p95/p99
statistics per instance.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass
class InstanceMetrics:
    instance_id: str
    cpu_p50: Optional[float] = None
    cpu_p95: Optional[float] = None
    cpu_p99: Optional[float] = None
    memory_p50: Optional[float] = None
    memory_p95: Optional[float] = None
    memory_p99: Optional[float] = None
    network_in_mbps_p95: Optional[float] = None
    network_out_mbps_p95: Optional[float] = None
    datapoints_collected: int = 0
    memory_available: bool = False  # False if CW Agent not installed


class CloudWatchAnalyzer:
    """Pulls and parses EC2 utilization metrics from CloudWatch."""

    # CloudWatch limits: max 500 metrics per GetMetricData call
    _BATCH_SIZE = 100

    def __init__(self, session: boto3.Session, config: dict):
        self._cw = session.client("cloudwatch")
        self._lookback_days = config["analysis"]["lookback_days"]
        self._period = config["analysis"]["metrics_period_seconds"]

    def get_metrics_bulk(self, instances) -> dict[str, InstanceMetrics]:
        """
        Fetch metrics for all instances in batches.
        Returns a dict of instance_id → InstanceMetrics.
        """
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=self._lookback_days)

        results: dict[str, InstanceMetrics] = {}

        # Process in batches to stay under CloudWatch API limits
        for i in range(0, len(instances), self._batch_size):
            batch = instances[i : i + self._batch_size]
            logger.debug("Fetching metrics for batch %d/%d", i // self._batch_size + 1,
                         (len(instances) + self._batch_size - 1) // self._batch_size)

            batch_results = self._fetch_batch(batch, start_time, end_time)
            results.update(batch_results)

        logger.info("Collected metrics for %d/%d instances", len(results), len(instances))
        return results

    @property
    def _batch_size(self):
        # Each instance needs ~5 metric queries; stay well under the 500 limit
        return min(self._BATCH_SIZE, 90)

    def _fetch_batch(self, instances, start_time: datetime, end_time: datetime) -> dict:
        metric_queries = []
        for inst in instances:
            iid = inst.instance_id
            metric_queries.extend(self._build_queries(iid))

        try:
            response = self._cw.get_metric_data(
                MetricDataQueries=metric_queries,
                StartTime=start_time,
                EndTime=end_time,
                ScanBy="TimestampDescending",
            )
        except ClientError as exc:
            logger.error("CloudWatch GetMetricData failed: %s", exc)
            return {}

        return self._parse_response(response, instances)

    def _build_queries(self, instance_id: str) -> list[dict]:
        """Build the CloudWatch metric query objects for one instance."""
        iid_safe = instance_id.replace("-", "_")

        queries = [
            # CPU utilization — built-in, no agent needed
            {
                "Id": f"{iid_safe}_cpu",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/EC2",
                        "MetricName": "CPUUtilization",
                        "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                    },
                    "Period": self._period,
                    "Stat": "p99",
                },
                "Label": f"{instance_id} CPU p99",
                "ReturnData": True,
            },
            {
                "Id": f"{iid_safe}_cpu_p95",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/EC2",
                        "MetricName": "CPUUtilization",
                        "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                    },
                    "Period": self._period,
                    "Stat": "p95",
                },
                "Label": f"{instance_id} CPU p95",
                "ReturnData": True,
            },
            # Network I/O — built-in
            {
                "Id": f"{iid_safe}_netin",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/EC2",
                        "MetricName": "NetworkIn",
                        "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                    },
                    "Period": self._period,
                    "Stat": "p95",
                },
                "Label": f"{instance_id} NetworkIn p95",
                "ReturnData": True,
            },
            # Memory — requires CloudWatch Agent with mem_used_percent metric
            {
                "Id": f"{iid_safe}_mem",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "CWAgent",
                        "MetricName": "mem_used_percent",
                        "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                    },
                    "Period": self._period,
                    "Stat": "p95",
                },
                "Label": f"{instance_id} Memory p95",
                "ReturnData": True,
            },
        ]

        return queries

    def _parse_response(self, response: dict, instances) -> dict[str, InstanceMetrics]:
        """Parse GetMetricData response into InstanceMetrics objects."""
        # Index results by label prefix (instance_id)
        data_by_id: dict[str, dict] = {}
        for result in response.get("MetricDataResults", []):
            label = result.get("Label", "")
            values = result.get("Values", [])
            if not values:
                continue

            # Label format: "{instance_id} {metric_name}"
            parts = label.split(" ", 1)
            if len(parts) != 2:
                continue
            iid, metric_name = parts

            if iid not in data_by_id:
                data_by_id[iid] = {}
            data_by_id[iid][metric_name] = values

        metrics: dict[str, InstanceMetrics] = {}
        for inst in instances:
            iid = inst.instance_id
            idata = data_by_id.get(iid, {})

            cpu_p95_vals = idata.get(f"CPU p95", [])
            cpu_p99_vals = idata.get(f"CPU p99", [])
            mem_vals = idata.get(f"Memory p95", [])
            netin_vals = idata.get(f"NetworkIn p95", [])

            # Convert bytes/period to Mbps
            netin_mbps = None
            if netin_vals:
                netin_mbps = (max(netin_vals) * 8) / (self._period * 1_000_000)

            metrics[iid] = InstanceMetrics(
                instance_id=iid,
                cpu_p95=max(cpu_p95_vals) if cpu_p95_vals else None,
                cpu_p99=max(cpu_p99_vals) if cpu_p99_vals else None,
                memory_p95=max(mem_vals) if mem_vals else None,
                memory_available=bool(mem_vals),
                network_in_mbps_p95=netin_mbps,
                datapoints_collected=len(cpu_p95_vals),
            )

        return metrics
