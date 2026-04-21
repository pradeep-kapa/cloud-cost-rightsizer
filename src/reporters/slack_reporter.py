"""
slack_reporter.py — sends a rightsizing summary to a Slack channel.

Uses the Slack Incoming Webhooks API. Configure the webhook URL via
SLACK_WEBHOOK_URL environment variable — never hardcode it.
"""

import json
import logging
import os
import urllib.request
import urllib.error
from typing import Optional

logger = logging.getLogger(__name__)


class SlackReporter:
    def __init__(self, config: dict):
        # Prefer env var over config file for the webhook URL
        self._webhook_url = os.environ.get("SLACK_WEBHOOK_URL") or config.get("webhook_url", "")
        self._channel = config.get("channel", "#finops")

    def send(self, recommendations: list, region: str, mention: bool = False) -> bool:
        """
        Send a summary to Slack. Returns True if successful.
        recommendations should be pre-filtered to action == "rightsize" only.
        """
        if not self._webhook_url:
            logger.warning("SLACK_WEBHOOK_URL not set — skipping Slack notification")
            return False

        total_savings = sum(r.estimated_monthly_savings for r in recommendations)
        top_recs = sorted(recommendations, key=lambda r: r.estimated_monthly_savings, reverse=True)[:5]

        header = f"<!channel> " if mention else ""
        header += f"*Cloud Cost Rightsizer — {region}*"

        fields = [
            {
                "type": "mrkdwn",
                "text": f"*Instances flagged*\n{len(recommendations)}",
            },
            {
                "type": "mrkdwn",
                "text": f"*Est. monthly savings*\n${total_savings:,.2f}",
            },
            {
                "type": "mrkdwn",
                "text": f"*Est. annual savings*\n${total_savings * 12:,.2f}",
            },
        ]

        rec_lines = "\n".join(
            f"• `{r.instance_id}` ({r.instance_name})  "
            f"`{r.current_type}` → `{r.recommended_type}`  "
            f"*${r.estimated_monthly_savings:,.0f}/mo*"
            for r in top_recs
        )
        if len(recommendations) > 5:
            rec_lines += f"\n_...and {len(recommendations) - 5} more_"

        payload = {
            "channel": self._channel,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "Cloud Cost Rightsizer"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": header}},
                {"type": "section", "fields": fields},
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Top recommendations:*\n{rec_lines}",
                    },
                },
            ],
        }

        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        """POST the payload to the Slack webhook."""
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("Slack notification sent")
                    return True
                logger.warning("Slack returned status %d", resp.status)
                return False
        except urllib.error.URLError as exc:
            logger.error("Failed to send Slack notification: %s", exc)
            return False
