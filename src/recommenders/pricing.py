"""
pricing.py — AWS Pricing API client with local disk cache.

The Pricing API is in us-east-1 only (global endpoint), so we always
hit that region regardless of which region we're analyzing.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class PricingClient:
    """Fetches on-demand EC2 pricing with a local cache to avoid repeated API calls."""

    # AWS Pricing API is only available in us-east-1
    _PRICING_REGION = "us-east-1"

    def __init__(self, session: boto3.Session, config: dict):
        # Always use us-east-1 for Pricing API regardless of analysis region
        self._pricing = boto3.client("pricing", region_name=self._PRICING_REGION)
        self._region = session.region_name
        self._cache_config = config.get("pricing", {})
        self._cache: dict[str, float] = {}
        self._cache_path = Path(".pricing_cache.json")
        self._cache_ttl = self._cache_config.get("cache_ttl_hours", 24) * 3600
        self._load_cache()

    def get_hourly_price(self, instance_type: str) -> Optional[float]:
        """Return the on-demand hourly price for an instance type in the current region."""
        cache_key = f"{self._region}:{instance_type}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        price = self._fetch_price(instance_type)
        if price is not None:
            self._cache[cache_key] = price
            self._save_cache()

        return price

    def _fetch_price(self, instance_type: str) -> Optional[float]:
        """Query the AWS Pricing API for on-demand pricing."""
        try:
            response = self._pricing.get_products(
                ServiceCode="AmazonEC2",
                Filters=[
                    {"Type": "TERM_MATCH", "Field": "instanceType",    "Value": instance_type},
                    {"Type": "TERM_MATCH", "Field": "location",        "Value": self._region_to_location()},
                    {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                    {"Type": "TERM_MATCH", "Field": "tenancy",         "Value": "Shared"},
                    {"Type": "TERM_MATCH", "Field": "preInstalledSw",  "Value": "NA"},
                    {"Type": "TERM_MATCH", "Field": "capacitystatus",  "Value": "Used"},
                ],
                MaxResults=10,
            )
        except ClientError as exc:
            logger.warning("Pricing API call failed for %s: %s", instance_type, exc)
            return None

        for price_item_str in response.get("PriceList", []):
            try:
                price_item = json.loads(price_item_str)
                on_demand = price_item.get("terms", {}).get("OnDemand", {})
                for term in on_demand.values():
                    for dimension in term.get("priceDimensions", {}).values():
                        price_str = dimension.get("pricePerUnit", {}).get("USD", "0")
                        price = float(price_str)
                        if price > 0:
                            return price
            except (json.JSONDecodeError, ValueError, KeyError) as exc:
                logger.debug("Failed to parse price item: %s", exc)
                continue

        logger.debug("No price found for %s in %s", instance_type, self._region)
        return None

    def _region_to_location(self) -> str:
        """Convert an AWS region code to the Pricing API location string."""
        region_map = {
            "us-east-1":      "US East (N. Virginia)",
            "us-east-2":      "US East (Ohio)",
            "us-west-1":      "US West (N. California)",
            "us-west-2":      "US West (Oregon)",
            "eu-west-1":      "Europe (Ireland)",
            "eu-west-2":      "Europe (London)",
            "eu-central-1":   "Europe (Frankfurt)",
            "ap-southeast-1": "Asia Pacific (Singapore)",
            "ap-southeast-2": "Asia Pacific (Sydney)",
            "ap-northeast-1": "Asia Pacific (Tokyo)",
        }
        return region_map.get(self._region, "US East (N. Virginia)")

    def _load_cache(self):
        """Load price cache from disk if it exists and hasn't expired."""
        if not self._cache_config.get("cache_enabled", True):
            return
        if not self._cache_path.exists():
            return

        try:
            data = json.loads(self._cache_path.read_text())
            # Check cache age
            if time.time() - data.get("_timestamp", 0) < self._cache_ttl:
                self._cache = {k: v for k, v in data.items() if k != "_timestamp"}
                logger.debug("Loaded %d cached prices", len(self._cache))
            else:
                logger.debug("Price cache expired — will refresh")
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Could not load price cache: %s", exc)

    def _save_cache(self):
        """Persist the price cache to disk."""
        if not self._cache_config.get("cache_enabled", True):
            return
        try:
            data = {**self._cache, "_timestamp": time.time()}
            self._cache_path.write_text(json.dumps(data, indent=2))
        except OSError as exc:
            logger.debug("Could not save price cache: %s", exc)
