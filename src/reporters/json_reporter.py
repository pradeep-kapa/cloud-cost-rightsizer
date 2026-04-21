"""
json_reporter.py — writes recommendations to a structured JSON file.

Useful for downstream processing, dashboards, or feeding into
other automation tools.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class JSONReporter:
    def __init__(self, output_dir: Path):
        self._output_dir = output_dir

    def write(self, recommendations: list, region: str, config: dict) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = self._output_dir / f"recommendations-{timestamp}.json"

        flagged = [r for r in recommendations if r.action == "rightsize"]
        total_monthly = sum(r.estimated_monthly_savings for r in flagged)

        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "region": region,
            "analysis": {
                "lookback_days": config["analysis"]["lookback_days"],
                "thresholds": config.get("thresholds", {}),
            },
            "summary": {
                "instances_scanned":       len(recommendations),
                "instances_flagged":       len(flagged),
                "instances_ok":            len([r for r in recommendations if r.action == "ok"]),
                "instances_skipped":       len([r for r in recommendations if r.action == "skip"]),
                "instances_insufficient_data": len([r for r in recommendations if r.action == "insufficient-data"]),
                "estimated_monthly_savings_usd": round(total_monthly, 2),
                "estimated_annual_savings_usd":  round(total_monthly * 12, 2),
            },
            "recommendations": [
                {
                    "instance_id":                   r.instance_id,
                    "instance_name":                 r.instance_name,
                    "action":                        r.action,
                    "current_type":                  r.current_type,
                    "recommended_type":              r.recommended_type,
                    "cpu_p95_pct":                   round(r.cpu_p95, 2) if r.cpu_p95 is not None else None,
                    "memory_p95_pct":                round(r.memory_p95, 2) if r.memory_p95 is not None else None,
                    "memory_data_available":         r.memory_data_available,
                    "current_monthly_cost_usd":      round(r.current_monthly_cost, 2),
                    "recommended_monthly_cost_usd":  round(r.recommended_monthly_cost, 2),
                    "estimated_monthly_savings_usd": round(r.estimated_monthly_savings, 2),
                    "estimated_annual_savings_usd":  round(r.estimated_monthly_savings * 12, 2),
                    "reason":                        r.reason,
                }
                for r in sorted(recommendations, key=lambda x: x.estimated_monthly_savings, reverse=True)
            ],
        }

        path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
        logger.info("JSON report written: %s", path)
        return path
