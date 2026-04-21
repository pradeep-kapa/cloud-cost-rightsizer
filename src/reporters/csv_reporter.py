"""
csv_reporter.py — writes recommendations to a CSV file.

Produces a spreadsheet-friendly output with one row per instance,
sorted by estimated monthly savings descending.
"""

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class CSVReporter:
    _FIELDNAMES = [
        "instance_id",
        "instance_name",
        "action",
        "current_type",
        "recommended_type",
        "cpu_p95_pct",
        "memory_p95_pct",
        "memory_data_available",
        "current_monthly_cost_usd",
        "recommended_monthly_cost_usd",
        "estimated_monthly_savings_usd",
        "estimated_annual_savings_usd",
        "reason",
    ]

    def __init__(self, output_dir: Path):
        self._output_dir = output_dir

    def write(self, recommendations: list) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = self._output_dir / f"recommendations-{timestamp}.csv"

        sorted_recs = sorted(
            recommendations,
            key=lambda r: r.estimated_monthly_savings,
            reverse=True,
        )

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self._FIELDNAMES)
            writer.writeheader()
            for rec in sorted_recs:
                writer.writerow({
                    "instance_id":                     rec.instance_id,
                    "instance_name":                   rec.instance_name,
                    "action":                          rec.action,
                    "current_type":                    rec.current_type,
                    "recommended_type":                rec.recommended_type or "",
                    "cpu_p95_pct":                     f"{rec.cpu_p95:.1f}" if rec.cpu_p95 is not None else "",
                    "memory_p95_pct":                  f"{rec.memory_p95:.1f}" if rec.memory_p95 is not None else "",
                    "memory_data_available":           str(rec.memory_data_available),
                    "current_monthly_cost_usd":        f"{rec.current_monthly_cost:.2f}",
                    "recommended_monthly_cost_usd":    f"{rec.recommended_monthly_cost:.2f}",
                    "estimated_monthly_savings_usd":   f"{rec.estimated_monthly_savings:.2f}",
                    "estimated_annual_savings_usd":    f"{rec.estimated_monthly_savings * 12:.2f}",
                    "reason":                          rec.reason,
                })

        logger.info("CSV report written: %s", path)
        return path
