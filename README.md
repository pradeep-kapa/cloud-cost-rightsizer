# cloud-cost-rightsizer

Automated EC2 rightsizing tool that pulls CloudWatch utilization metrics, identifies over-provisioned instances, and generates actionable recommendations with estimated savings. Built from patterns used at Industry Standards to consistently hit 20–40% cloud cost reductions without impacting workload performance.

The tool doesn't make changes automatically — it generates reports and recommendations. You decide what to act on. This is intentional: auto-rightsizing production workloads without human review is how you cause incidents.

---

## How it works

```
CloudWatch Metrics
  (CPU, Memory, Network)
         │
         ▼
    Analyzers
  (per instance type)
         │
         ▼
   Recommenders
  (right-size logic)
         │
         ▼
    Reports
  (CSV + JSON + Slack)
```

1. **Collect** — pulls 14 days of CloudWatch metrics (CPU, memory via CWAgent, network I/O) for all running EC2 instances in scope
2. **Analyze** — calculates p50/p95/p99 utilization per instance, flags over-provisioned resources
3. **Recommend** — maps current instance type to the best-fit smaller type using AWS pricing API
4. **Report** — outputs CSV, JSON, and optional Slack notification with estimated monthly savings

---

## Quick start

### Prerequisites

- Python 3.11+
- AWS credentials with read access to EC2, CloudWatch, and Pricing APIs
- CloudWatch Agent installed on instances (for memory metrics — CPU comes free)

### Install

```bash
git clone https://github.com/pradeep-kapa/cloud-cost-rightsizer.git
cd cloud-cost-rightsizer

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Run

```bash
# Analyze all instances in a region
python -m src.main --region us-east-1

# Scope to specific tags
python -m src.main --region us-east-1 --tag-key Environment --tag-value prod

# Set custom thresholds
python -m src.main \
  --region us-east-1 \
  --cpu-threshold 20 \
  --memory-threshold 30 \
  --lookback-days 21

# Output to a specific directory
python -m src.main --region us-east-1 --output-dir ./reports/2024-11
```

### Output

The tool writes three files to `./reports/` (or your `--output-dir`):

- `recommendations.csv` — spreadsheet-friendly, one row per instance
- `recommendations.json` — structured data for downstream processing
- `summary.txt` — human-readable summary with total estimated savings

Example summary output:
```
====================================
Cloud Cost Rightsizer — Summary
====================================
Region:          us-east-1
Instances scanned:    247
Analysis window:  14 days (p95 utilization)

Over-provisioned:      89 instances (36%)
Already right-sized:  148 instances
Skipped (excluded):    10 instances

Estimated monthly savings: $4,820
Estimated annual savings:  $57,840

Top recommendations by savings:
  i-0a1b2c3d  m5.4xlarge  →  m5.xlarge   $892/mo
  i-0e4f5g6h  r5.2xlarge  →  r5.large    $634/mo
  i-0i7j8k9l  c5.4xlarge  →  c5.xlarge   $512/mo
====================================
```

---

## Configuration

Create `configs/config.yaml` (copy from `configs/config.example.yaml`):

```yaml
analysis:
  lookback_days: 14
  metrics_period_seconds: 3600  # 1-hour granularity

thresholds:
  cpu_max_p95: 20       # Flag if p95 CPU < 20%
  memory_max_p95: 30    # Flag if p95 memory < 30%
  network_max_mbps: 100 # Flag if p95 network < 100 Mbps

exclusions:
  instance_ids:
    - i-xxxxxxxxxxxxxxxxx  # Never recommend changes for these
  tags:
    DoNotRightsize: "true"
  instance_families:
    - t3  # Skip burstable instances — metrics are misleading

reporting:
  formats:
    - csv
    - json
    - summary
  slack:
    enabled: false
    webhook_url: ""        # Set via SLACK_WEBHOOK_URL env var instead
    channel: "#finops"
    mention_on_savings_above: 10000  # @ channel if monthly savings > $10k

pricing:
  # Cache pricing data locally to avoid rate limits on repeated runs
  cache_enabled: true
  cache_ttl_hours: 24
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `AWS_REGION` | Default region (overridden by `--region` flag) |
| `AWS_PROFILE` | AWS CLI profile to use |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook URL |
| `RIGHTSIZER_CONFIG` | Path to config file (default: `configs/config.yaml`) |

### IAM permissions required

Minimum IAM policy for the tool to run:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2ReadAccess",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceTypes",
        "ec2:DescribeRegions"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchReadAccess",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:GetMetricData",
        "cloudwatch:ListMetrics"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PricingReadAccess",
      "Effect": "Allow",
      "Action": [
        "pricing:GetProducts",
        "pricing:DescribeServices"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## Project structure

```
cloud-cost-rightsizer/
├── src/
│   ├── main.py                   # CLI entrypoint
│   ├── analyzers/
│   │   ├── cloudwatch.py         # Pulls and parses CloudWatch metrics
│   │   └── ec2.py                # Fetches instance metadata
│   ├── recommenders/
│   │   ├── rightsizer.py         # Core rightsizing logic
│   │   └── pricing.py            # AWS Pricing API client
│   ├── reporters/
│   │   ├── csv_reporter.py
│   │   ├── json_reporter.py
│   │   └── slack_reporter.py
│   └── utils/
│       ├── config.py             # Config loading and validation
│       ├── aws_session.py        # Boto3 session management
│       └── logger.py
├── configs/
│   ├── config.example.yaml       # Template — copy to config.yaml
│   └── exclusions.example.yaml   # Instance exclusion lists
├── tests/
│   ├── test_rightsizer.py
│   ├── test_cloudwatch.py
│   └── fixtures/                 # Mocked CloudWatch response fixtures
├── scripts/
│   └── run-report.sh             # Wrapper for cron/scheduled runs
├── .github/workflows/
│   └── ci.yml
├── requirements.txt
├── requirements-dev.txt
└── Makefile
```

---

## Rightsizing logic

The recommendation engine works in three passes:

**Pass 1 — Flag over-provisioned instances**
An instance is flagged if *both* CPU p95 and memory p95 are below their configured thresholds over the lookback window. If only one metric is low, it's marked as a "watch" — not a recommendation.

**Pass 2 — Find the right-fit instance type**
For a flagged instance, the engine looks for the smallest instance type in the same family that can still accommodate p99 utilization with a 20% headroom buffer. It won't cross instance families (no m5 → c5 recommendations) unless you explicitly enable cross-family mode.

**Pass 3 — Validate against pricing**
The candidate replacement is validated against the AWS Pricing API to confirm it's actually cheaper in the target region. If the price difference is less than 5%, the recommendation is skipped (not worth the operational overhead).

---

## Running on a schedule

The `scripts/run-report.sh` wrapper is designed for cron or ECS Scheduled Tasks:

```bash
# Weekly report every Monday at 8am
0 8 * * 1 /path/to/cloud-cost-rightsizer/scripts/run-report.sh >> /var/log/rightsizer.log 2>&1
```

---

## Tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=src --cov-report=term-missing
```

---

## License

MIT
