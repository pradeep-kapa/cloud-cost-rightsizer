"""
test_cloudwatch.py — unit tests for the CloudWatch analyzer.

All AWS API calls are mocked — these tests run without credentials.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.analyzers.cloudwatch import CloudWatchAnalyzer
from src.analyzers.ec2 import EC2Instance

DEFAULT_CONFIG = {
    "analysis": {"lookback_days": 14, "metrics_period_seconds": 3600},
    "thresholds": {},
}


def make_instance(instance_id: str, instance_type: str = "m5.xlarge") -> EC2Instance:
    return EC2Instance(
        instance_id=instance_id,
        instance_type=instance_type,
        state="running",
        name=instance_id,
        region="us-east-1",
    )


def make_cw_response(instance_id: str, cpu_p95: float = 15.0, mem_p95: float = 20.0) -> dict:
    """Build a mock GetMetricData response."""
    return {
        "MetricDataResults": [
            {
                "Id":     f"{instance_id.replace('-', '_')}_cpu_p95",
                "Label":  f"{instance_id} CPU p95",
                "Values": [cpu_p95, cpu_p95 - 2, cpu_p95 + 1],
                "StatusCode": "Complete",
            },
            {
                "Id":     f"{instance_id.replace('-', '_')}_cpu",
                "Label":  f"{instance_id} CPU p99",
                "Values": [cpu_p95 + 5],
                "StatusCode": "Complete",
            },
            {
                "Id":     f"{instance_id.replace('-', '_')}_mem",
                "Label":  f"{instance_id} Memory p95",
                "Values": [mem_p95],
                "StatusCode": "Complete",
            },
            {
                "Id":     f"{instance_id.replace('-', '_')}_netin",
                "Label":  f"{instance_id} NetworkIn p95",
                "Values": [50_000_000],  # 50 MB/period
                "StatusCode": "Complete",
            },
        ]
    }


class TestCloudWatchAnalyzer:

    def _make_analyzer(self, mock_response: dict) -> CloudWatchAnalyzer:
        session = MagicMock()
        mock_cw = MagicMock()
        mock_cw.get_metric_data.return_value = mock_response
        session.client.return_value = mock_cw
        analyzer = CloudWatchAnalyzer(session=session, config=DEFAULT_CONFIG)
        analyzer._cw = mock_cw
        return analyzer

    def test_returns_metrics_for_instance(self):
        inst = make_instance("i-abc123")
        response = make_cw_response("i-abc123", cpu_p95=12.0, mem_p95=18.0)
        analyzer = self._make_analyzer(response)

        result = analyzer.get_metrics_bulk([inst])

        assert "i-abc123" in result
        m = result["i-abc123"]
        assert m.instance_id == "i-abc123"
        assert m.cpu_p95 == 12.0
        assert m.memory_p95 == 18.0
        assert m.memory_available is True

    def test_handles_missing_memory_metrics(self):
        """Instances without CloudWatch Agent return no memory data."""
        inst = make_instance("i-nomem")
        response = {
            "MetricDataResults": [
                {
                    "Label":  "i-nomem CPU p95",
                    "Values": [8.0],
                    "StatusCode": "Complete",
                },
                {
                    "Label":  "i-nomem CPU p99",
                    "Values": [12.0],
                    "StatusCode": "Complete",
                },
                # No memory entry — CWAgent not installed
            ]
        }
        analyzer = self._make_analyzer(response)

        result = analyzer.get_metrics_bulk([inst])

        assert "i-nomem" in result
        assert result["i-nomem"].memory_available is False
        assert result["i-nomem"].memory_p95 is None

    def test_handles_empty_cloudwatch_response(self):
        """Instances with no data (new or stopped briefly) handled gracefully."""
        inst = make_instance("i-nodata")
        response = {"MetricDataResults": []}
        analyzer = self._make_analyzer(response)

        result = analyzer.get_metrics_bulk([inst])

        # Instance should still appear in results, just with no data
        assert "i-nodata" in result
        assert result["i-nodata"].datapoints_collected == 0

    def test_multiple_instances_all_parsed(self):
        instances = [make_instance(f"i-{i:06d}") for i in range(5)]
        # Build a combined response for all instances
        all_results = []
        for inst in instances:
            all_results.extend(
                make_cw_response(inst.instance_id, cpu_p95=float(10 + instances.index(inst)))["MetricDataResults"]
            )
        response = {"MetricDataResults": all_results}
        analyzer = self._make_analyzer(response)

        result = analyzer.get_metrics_bulk(instances)

        assert len(result) == 5
        for inst in instances:
            assert inst.instance_id in result

    def test_cloudwatch_api_error_returns_empty(self):
        from botocore.exceptions import ClientError

        inst = make_instance("i-apierror")
        session = MagicMock()
        mock_cw = MagicMock()
        mock_cw.get_metric_data.side_effect = ClientError(
            {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}}, "GetMetricData"
        )
        session.client.return_value = mock_cw
        analyzer = CloudWatchAnalyzer(session=session, config=DEFAULT_CONFIG)
        analyzer._cw = mock_cw

        result = analyzer.get_metrics_bulk([inst])

        assert result == {}
