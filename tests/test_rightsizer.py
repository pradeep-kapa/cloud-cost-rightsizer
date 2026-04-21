"""
test_rightsizer.py — unit tests for the core rightsizing logic.

Uses unittest.mock to avoid hitting real AWS APIs.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.analyzers.cloudwatch import InstanceMetrics
from src.analyzers.ec2 import EC2Instance
from src.recommenders.rightsizer import Rightsizer

# ── Fixtures ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "analysis": {"lookback_days": 14, "metrics_period_seconds": 3600},
    "thresholds": {"cpu_max_p95": 20.0, "memory_max_p95": 30.0},
    "exclusions": {"instance_ids": [], "tags": {}, "instance_families": []},
    "pricing": {"cache_enabled": False},
}


def make_instance(instance_id="i-abc123", instance_type="m5.2xlarge") -> EC2Instance:
    return EC2Instance(
        instance_id=instance_id,
        instance_type=instance_type,
        state="running",
        name=f"test-{instance_id}",
        region="us-east-1",
    )


def make_metrics(
    instance_id="i-abc123",
    cpu_p95=10.0,
    memory_p95=15.0,
    datapoints=168,  # 7 days at 1h
) -> InstanceMetrics:
    return InstanceMetrics(
        instance_id=instance_id,
        cpu_p95=cpu_p95,
        cpu_p99=cpu_p95 + 5,
        memory_p95=memory_p95,
        memory_available=True,
        datapoints_collected=datapoints,
    )


def make_rightsizer(config=None, current_price=0.384, recommended_price=0.192):
    """Create a Rightsizer with mocked pricing."""
    config = config or DEFAULT_CONFIG
    mock_session = MagicMock()

    with patch("src.recommenders.rightsizer.PricingClient") as mock_pricing_cls:
        mock_pricing = MagicMock()
        mock_pricing.get_hourly_price.side_effect = lambda t: (
            current_price if "2xlarge" in t else recommended_price
        )
        mock_pricing_cls.return_value = mock_pricing
        rs = Rightsizer(session=mock_session, config=config)
        rs._pricing = mock_pricing
    return rs


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestRightsizer:

    def test_recommends_rightsize_when_both_metrics_low(self):
        rs = make_rightsizer()
        inst = make_instance(instance_type="m5.2xlarge")
        metrics = make_metrics(cpu_p95=8.0, memory_p95=12.0)

        recs = rs.analyze([inst], {inst.instance_id: metrics})

        assert len(recs) == 1
        rec = recs[0]
        assert rec.action == "rightsize"
        assert rec.recommended_type == "m5.xlarge"
        assert rec.estimated_monthly_savings > 0

    def test_no_recommendation_when_cpu_high(self):
        rs = make_rightsizer()
        inst = make_instance(instance_type="m5.2xlarge")
        metrics = make_metrics(cpu_p95=55.0, memory_p95=12.0)

        recs = rs.analyze([inst], {inst.instance_id: metrics})

        assert recs[0].action == "ok"
        assert recs[0].recommended_type is None

    def test_no_recommendation_when_memory_high(self):
        rs = make_rightsizer()
        inst = make_instance(instance_type="m5.2xlarge")
        metrics = make_metrics(cpu_p95=8.0, memory_p95=75.0)

        recs = rs.analyze([inst], {inst.instance_id: metrics})

        assert recs[0].action == "ok"

    def test_insufficient_data_when_too_few_datapoints(self):
        rs = make_rightsizer()
        inst = make_instance(instance_type="m5.2xlarge")
        metrics = make_metrics(cpu_p95=5.0, memory_p95=5.0, datapoints=10)

        recs = rs.analyze([inst], {inst.instance_id: metrics})

        assert recs[0].action == "insufficient-data"

    def test_skip_when_savings_below_threshold(self):
        # Current and recommended prices nearly identical
        rs = make_rightsizer(current_price=0.200, recommended_price=0.195)
        inst = make_instance(instance_type="m5.2xlarge")
        metrics = make_metrics(cpu_p95=5.0, memory_p95=5.0)

        recs = rs.analyze([inst], {inst.instance_id: metrics})

        assert recs[0].action == "skip"

    def test_already_at_smallest_type_returns_ok(self):
        rs = make_rightsizer()
        inst = make_instance(instance_type="m5.large")  # smallest in m5 family
        metrics = make_metrics(cpu_p95=5.0, memory_p95=5.0)

        recs = rs.analyze([inst], {inst.instance_id: metrics})

        assert recs[0].action == "ok"
        assert "smallest" in recs[0].reason.lower()

    def test_unknown_instance_family_skips_gracefully(self):
        rs = make_rightsizer()
        inst = make_instance(instance_type="x2iedn.xlarge")  # not in INSTANCE_SIZE_ORDER
        metrics = make_metrics(cpu_p95=5.0, memory_p95=5.0)

        recs = rs.analyze([inst], {inst.instance_id: metrics})

        # Should not crash — should return ok or skip
        assert recs[0].action in ("ok", "skip")

    def test_no_metrics_returns_insufficient_data(self):
        rs = make_rightsizer()
        inst = make_instance(instance_type="m5.2xlarge")

        recs = rs.analyze([inst], {})  # empty metrics dict

        assert recs[0].action == "insufficient-data"

    def test_savings_calculation_is_correct(self):
        # m5.2xlarge = $0.384/hr, m5.xlarge = $0.192/hr → $0.192/hr savings
        # Monthly: 0.192 * 730 = $140.16
        rs = make_rightsizer(current_price=0.384, recommended_price=0.192)
        inst = make_instance(instance_type="m5.2xlarge")
        metrics = make_metrics(cpu_p95=5.0, memory_p95=5.0)

        recs = rs.analyze([inst], {inst.instance_id: metrics})

        rec = recs[0]
        assert rec.action == "rightsize"
        assert abs(rec.estimated_monthly_savings - 140.16) < 0.01

    def test_multiple_instances_all_analyzed(self):
        rs = make_rightsizer()
        instances = [
            make_instance(f"i-{i:06d}", "m5.2xlarge") for i in range(10)
        ]
        metrics = {
            inst.instance_id: make_metrics(inst.instance_id, cpu_p95=5.0, memory_p95=5.0)
            for inst in instances
        }

        recs = rs.analyze(instances, metrics)

        assert len(recs) == 10
        assert all(r.action == "rightsize" for r in recs)
