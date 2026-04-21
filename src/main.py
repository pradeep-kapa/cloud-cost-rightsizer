"""
cloud-cost-rightsizer — entry point

Pulls CloudWatch utilization metrics for EC2 instances, identifies
over-provisioned resources, and generates rightsizing recommendations
with estimated monthly savings.

Usage:
    python -m src.main --region us-east-1
    python -m src.main --region us-east-1 --tag-key Environment --tag-value prod
    python -m src.main --help
"""

import argparse
import logging
import sys
from pathlib import Path

from src.analyzers.ec2 import EC2Analyzer
from src.analyzers.cloudwatch import CloudWatchAnalyzer
from src.recommenders.rightsizer import Rightsizer
from src.reporters.csv_reporter import CSVReporter
from src.reporters.json_reporter import JSONReporter
from src.reporters.slack_reporter import SlackReporter
from src.utils.aws_session import get_session
from src.utils.config import load_config
from src.utils.logger import setup_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EC2 rightsizing — identifies over-provisioned instances and estimates savings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--region",
        required=True,
        help="AWS region to analyze (e.g. us-east-1)",
    )
    parser.add_argument(
        "--tag-key",
        help="Filter instances by this tag key",
    )
    parser.add_argument(
        "--tag-value",
        help="Filter instances by this tag value (requires --tag-key)",
    )
    parser.add_argument(
        "--cpu-threshold",
        type=float,
        default=None,
        help="Override: flag instances with p95 CPU below this percent (default from config)",
    )
    parser.add_argument(
        "--memory-threshold",
        type=float,
        default=None,
        help="Override: flag instances with p95 memory below this percent (default from config)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="Override: number of days of metrics to analyze (default from config)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./reports"),
        help="Directory to write report files (default: ./reports)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/config.yaml"),
        help="Path to config file (default: configs/config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be analyzed without writing reports",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = setup_logger(level=logging.DEBUG if args.debug else logging.INFO)

    # Load config and apply CLI overrides
    config = load_config(args.config)
    if args.cpu_threshold is not None:
        config["thresholds"]["cpu_max_p95"] = args.cpu_threshold
    if args.memory_threshold is not None:
        config["thresholds"]["memory_max_p95"] = args.memory_threshold
    if args.lookback_days is not None:
        config["analysis"]["lookback_days"] = args.lookback_days

    logger.info("Starting cloud-cost-rightsizer")
    logger.info("Region: %s | Lookback: %d days", args.region, config["analysis"]["lookback_days"])

    session = get_session(region=args.region)

    # Build tag filter if provided
    tag_filters = None
    if args.tag_key:
        if not args.tag_value:
            logger.error("--tag-value is required when --tag-key is specified")
            return 1
        tag_filters = {args.tag_key: args.tag_value}

    # Step 1: fetch EC2 instance inventory
    logger.info("Fetching EC2 instance inventory...")
    ec2_analyzer = EC2Analyzer(session=session, config=config)
    instances = ec2_analyzer.get_instances(tag_filters=tag_filters)
    logger.info("Found %d running instances in scope", len(instances))

    if not instances:
        logger.warning("No instances found matching filters. Exiting.")
        return 0

    if args.dry_run:
        logger.info("[dry-run] Would analyze %d instances. Exiting without writing reports.", len(instances))
        for inst in instances[:10]:
            logger.info("  %s  %-15s  %s", inst.instance_id, inst.instance_type, inst.name)
        if len(instances) > 10:
            logger.info("  ... and %d more", len(instances) - 10)
        return 0

    # Step 2: pull CloudWatch metrics
    logger.info("Pulling CloudWatch metrics (this takes a minute for large fleets)...")
    cw_analyzer = CloudWatchAnalyzer(session=session, config=config)
    metrics = cw_analyzer.get_metrics_bulk(instances=instances)
    logger.info("Collected metrics for %d/%d instances", len(metrics), len(instances))

    # Step 3: generate recommendations
    logger.info("Running rightsizing analysis...")
    rightsizer = Rightsizer(session=session, config=config)
    recommendations = rightsizer.analyze(instances=instances, metrics=metrics)

    flagged = [r for r in recommendations if r.action == "rightsize"]
    total_monthly_savings = sum(r.estimated_monthly_savings for r in flagged)

    logger.info(
        "Analysis complete — %d/%d instances flagged for rightsizing",
        len(flagged),
        len(instances),
    )
    logger.info("Estimated monthly savings: $%.2f", total_monthly_savings)

    # Step 4: write reports
    args.output_dir.mkdir(parents=True, exist_ok=True)

    csv_reporter = CSVReporter(output_dir=args.output_dir)
    csv_path = csv_reporter.write(recommendations)
    logger.info("CSV report: %s", csv_path)

    json_reporter = JSONReporter(output_dir=args.output_dir)
    json_path = json_reporter.write(recommendations, region=args.region, config=config)
    logger.info("JSON report: %s", json_path)

    # Print summary to stdout
    _print_summary(recommendations, args.region)

    # Optional: Slack notification
    slack_cfg = config.get("reporting", {}).get("slack", {})
    if slack_cfg.get("enabled") and total_monthly_savings > 0:
        min_savings = slack_cfg.get("mention_on_savings_above", 0)
        reporter = SlackReporter(config=slack_cfg)
        reporter.send(
            recommendations=flagged,
            region=args.region,
            mention=total_monthly_savings >= min_savings,
        )

    return 0


def _print_summary(recommendations, region: str) -> None:
    flagged = [r for r in recommendations if r.action == "rightsize"]
    ok = [r for r in recommendations if r.action == "ok"]
    skipped = [r for r in recommendations if r.action == "skip"]
    total_savings = sum(r.estimated_monthly_savings for r in flagged)

    print("\n" + "=" * 42)
    print("  Cloud Cost Rightsizer — Summary")
    print("=" * 42)
    print(f"  Region:               {region}")
    print(f"  Instances scanned:    {len(recommendations)}")
    print(f"  Over-provisioned:     {len(flagged)}")
    print(f"  Already right-sized:  {len(ok)}")
    print(f"  Skipped (excluded):   {len(skipped)}")
    print(f"\n  Est. monthly savings: ${total_savings:,.2f}")
    print(f"  Est. annual savings:  ${total_savings * 12:,.2f}")

    if flagged:
        top = sorted(flagged, key=lambda r: r.estimated_monthly_savings, reverse=True)[:5]
        print("\n  Top recommendations:")
        for r in top:
            print(
                f"    {r.instance_id:<20} {r.current_type:<14} → {r.recommended_type:<14} "
                f"${r.estimated_monthly_savings:,.0f}/mo"
            )
    print("=" * 42 + "\n")


if __name__ == "__main__":
    sys.exit(main())
