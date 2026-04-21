#!/usr/bin/env bash
# run-report.sh — wrapper for scheduled / cron runs
#
# Usage:
#   ./scripts/run-report.sh
#   REGION=eu-west-1 ./scripts/run-report.sh
#
# Designed to run from cron, ECS Scheduled Tasks, or GitHub Actions.
# Writes reports to $OUTPUT_DIR and logs to stdout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REGION="${REGION:-us-east-1}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/reports}"
CONFIG="${CONFIG:-${REPO_ROOT}/configs/config.yaml}"
VENV="${REPO_ROOT}/.venv"

# Activate virtualenv if present
if [ -f "${VENV}/bin/activate" ]; then
  # shellcheck source=/dev/null
  source "${VENV}/bin/activate"
fi

echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] Starting cloud-cost-rightsizer"
echo "  Region:     ${REGION}"
echo "  Output dir: ${OUTPUT_DIR}"
echo "  Config:     ${CONFIG}"

mkdir -p "${OUTPUT_DIR}"

python -m src.main \
  --region "${REGION}" \
  --output-dir "${OUTPUT_DIR}" \
  --config "${CONFIG}" \
  "$@"

echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] Done"
