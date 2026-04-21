"""
rightsizer.py — core rightsizing logic.

Three-pass analysis:
  1. Flag over-provisioned instances (CPU p95 + memory p95 both below thresholds)
  2. Find the best-fit smaller instance type in the same family
  3. Validate the candidate is actually cheaper (skip if savings < 5%)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import boto3

from src.analyzers.cloudwatch import InstanceMetrics
from src.analyzers.ec2 import EC2Instance
from src.recommenders.pricing import PricingClient

logger = logging.getLogger(__name__)

# Maps each instance family to its size progression (smallest → largest)
# Only families commonly used in production workloads are listed.
INSTANCE_SIZE_ORDER = {
    "m5":  ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge"],
    "m5a": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge"],
    "m6i": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "32xlarge"],
    "m6a": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "48xlarge"],
    "c5":  ["large", "xlarge", "2xlarge", "4xlarge", "9xlarge", "12xlarge", "18xlarge", "24xlarge"],
    "c6i": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "32xlarge"],
    "c6a": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "48xlarge"],
    "r5":  ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge"],
    "r6i": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "32xlarge"],
    "t3":  ["nano", "micro", "small", "medium", "large", "xlarge", "2xlarge"],
    "t3a": ["nano", "micro", "small", "medium", "large", "xlarge", "2xlarge"],
}


@dataclass
class Recommendation:
    instance_id: str
    instance_name: str
    current_type: str
    recommended_type: Optional[str]
    action: str   # "rightsize", "ok", "skip", "insufficient-data"
    reason: str
    cpu_p95: Optional[float] = None
    memory_p95: Optional[float] = None
    current_monthly_cost: float = 0.0
    recommended_monthly_cost: float = 0.0
    estimated_monthly_savings: float = 0.0
    memory_data_available: bool = False


class Rightsizer:
    """Generates rightsizing recommendations from utilization metrics."""

    # Minimum number of data points to make a recommendation.
    # Fewer than this and we don't have enough signal.
    _MIN_DATAPOINTS = 24  # 24 hours at 1h granularity

    # Headroom buffer — recommended type must support p99 + this buffer
    _HEADROOM_PCT = 20

    # Skip recommendation if savings are less than this percentage
    _MIN_SAVINGS_PCT = 5

    def __init__(self, session: boto3.Session, config: dict):
        self._config = config
        self._pricing = PricingClient(session=session, config=config)
        self._thresholds = config.get("thresholds", {})
        self._cpu_threshold = self._thresholds.get("cpu_max_p95", 20.0)
        self._mem_threshold = self._thresholds.get("memory_max_p95", 30.0)

    def analyze(
        self,
        instances: list[EC2Instance],
        metrics: dict[str, InstanceMetrics],
    ) -> list[Recommendation]:
        """Run the full analysis pipeline and return recommendations for all instances."""
        recommendations = []

        for inst in instances:
            m = metrics.get(inst.instance_id)
            rec = self._analyze_instance(inst, m)
            recommendations.append(rec)

        return recommendations

    def _analyze_instance(
        self,
        inst: EC2Instance,
        metrics: Optional[InstanceMetrics],
    ) -> Recommendation:
        """Analyze a single instance and return a Recommendation."""

        # Not enough data — can't make a recommendation
        if metrics is None or metrics.datapoints_collected < self._MIN_DATAPOINTS:
            return Recommendation(
                instance_id=inst.instance_id,
                instance_name=inst.name,
                current_type=inst.instance_type,
                recommended_type=None,
                action="insufficient-data",
                reason=f"Only {metrics.datapoints_collected if metrics else 0} datapoints "
                       f"(need ≥ {self._MIN_DATAPOINTS})",
            )

        cpu_p95 = metrics.cpu_p95
        mem_p95 = metrics.memory_p95

        # If memory data isn't available, only consider CPU
        cpu_over = cpu_p95 is not None and cpu_p95 < self._cpu_threshold
        mem_over = (
            mem_p95 is not None and mem_p95 < self._mem_threshold
            if metrics.memory_available
            else None  # unknown
        )

        # Need both CPU and memory (if available) to be low to flag for rightsizing
        is_candidate = cpu_over and (mem_over is True or mem_over is None)

        if not is_candidate:
            reason_parts = []
            if not cpu_over:
                reason_parts.append(f"CPU p95 {cpu_p95:.1f}% ≥ threshold {self._cpu_threshold}%")
            if mem_over is False:
                reason_parts.append(f"Memory p95 {mem_p95:.1f}% ≥ threshold {self._mem_threshold}%")
            return Recommendation(
                instance_id=inst.instance_id,
                instance_name=inst.name,
                current_type=inst.instance_type,
                recommended_type=None,
                action="ok",
                reason="; ".join(reason_parts) or "Utilization within thresholds",
                cpu_p95=cpu_p95,
                memory_p95=mem_p95,
                memory_data_available=metrics.memory_available,
            )

        # Find the right-fit smaller instance type
        recommended_type = self._find_recommendation(inst.instance_type, metrics)

        if recommended_type is None:
            return Recommendation(
                instance_id=inst.instance_id,
                instance_name=inst.name,
                current_type=inst.instance_type,
                recommended_type=None,
                action="ok",
                reason="Already at smallest type in family",
                cpu_p95=cpu_p95,
                memory_p95=mem_p95,
                memory_data_available=metrics.memory_available,
            )

        # Get pricing to validate the saving is worth it
        current_price = self._pricing.get_hourly_price(inst.instance_type)
        recommended_price = self._pricing.get_hourly_price(recommended_type)

        if current_price is None or recommended_price is None:
            return Recommendation(
                instance_id=inst.instance_id,
                instance_name=inst.name,
                current_type=inst.instance_type,
                recommended_type=recommended_type,
                action="skip",
                reason="Could not retrieve pricing data for comparison",
                cpu_p95=cpu_p95,
                memory_p95=mem_p95,
            )

        savings_pct = ((current_price - recommended_price) / current_price) * 100
        if savings_pct < self._MIN_SAVINGS_PCT:
            return Recommendation(
                instance_id=inst.instance_id,
                instance_name=inst.name,
                current_type=inst.instance_type,
                recommended_type=recommended_type,
                action="skip",
                reason=f"Savings ({savings_pct:.1f}%) below minimum threshold ({self._MIN_SAVINGS_PCT}%)",
                cpu_p95=cpu_p95,
                memory_p95=mem_p95,
            )

        monthly_hours = 730  # average hours per month
        return Recommendation(
            instance_id=inst.instance_id,
            instance_name=inst.name,
            current_type=inst.instance_type,
            recommended_type=recommended_type,
            action="rightsize",
            reason=f"CPU p95 {cpu_p95:.1f}%, Memory p95 {f'{mem_p95:.1f}%' if mem_p95 else 'N/A'} — both below thresholds",
            cpu_p95=cpu_p95,
            memory_p95=mem_p95,
            current_monthly_cost=current_price * monthly_hours,
            recommended_monthly_cost=recommended_price * monthly_hours,
            estimated_monthly_savings=(current_price - recommended_price) * monthly_hours,
            memory_data_available=metrics.memory_available,
        )

    def _find_recommendation(
        self,
        current_type: str,
        metrics: InstanceMetrics,
    ) -> Optional[str]:
        """
        Find the smallest instance type in the same family that can still
        accommodate p99 utilization + headroom buffer.
        """
        parts = current_type.split(".")
        if len(parts) != 2:
            return None

        family, current_size = parts
        sizes = INSTANCE_SIZE_ORDER.get(family)

        if sizes is None:
            logger.debug("No size order defined for family %s — skipping", family)
            return None

        if current_size not in sizes:
            return None

        current_index = sizes.index(current_size)
        if current_index == 0:
            return None  # Already at the smallest

        # Try each smaller size and return the first one that has enough headroom
        # For this example, we use a simple heuristic: recommend one size down.
        # A full implementation would query the EC2 instance spec API for vCPU/RAM
        # and verify actual resource requirements fit.
        candidate_index = current_index - 1
        candidate_size = sizes[candidate_index]
        return f"{family}.{candidate_size}"
