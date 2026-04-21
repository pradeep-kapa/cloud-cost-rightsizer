"""
ec2.py — fetches EC2 instance inventory and metadata.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass
class EC2Instance:
    instance_id: str
    instance_type: str
    state: str
    name: str = ""
    region: str = ""
    az: str = ""
    platform: str = "linux"
    tags: dict = field(default_factory=dict)
    launch_time: Optional[str] = None


class EC2Analyzer:
    """Fetches and filters the EC2 instance inventory."""

    def __init__(self, session: boto3.Session, config: dict):
        self._ec2 = session.client("ec2")
        self._config = config
        self._region = session.region_name

    def get_instances(self, tag_filters: Optional[dict] = None) -> list[EC2Instance]:
        """
        Return all running EC2 instances, optionally filtered by tags.
        Applies exclusion rules from config automatically.
        """
        filters = [{"Name": "instance-state-name", "Values": ["running"]}]

        if tag_filters:
            for key, value in tag_filters.items():
                filters.append({"Name": f"tag:{key}", "Values": [value]})

        instances = []
        paginator = self._ec2.get_paginator("describe_instances")

        try:
            for page in paginator.paginate(Filters=filters):
                for reservation in page["Reservations"]:
                    for inst in reservation["Instances"]:
                        ec2inst = self._parse_instance(inst)
                        if self._should_include(ec2inst):
                            instances.append(ec2inst)
        except ClientError as exc:
            logger.error("Failed to describe EC2 instances: %s", exc)
            raise

        logger.debug("Found %d instances in scope after exclusions", len(instances))
        return instances

    def _parse_instance(self, raw: dict) -> EC2Instance:
        tags = {t["Key"]: t["Value"] for t in raw.get("Tags", [])}
        return EC2Instance(
            instance_id=raw["InstanceId"],
            instance_type=raw["InstanceType"],
            state=raw["State"]["Name"],
            name=tags.get("Name", raw["InstanceId"]),
            region=self._region,
            az=raw.get("Placement", {}).get("AvailabilityZone", ""),
            platform="windows" if raw.get("Platform") == "windows" else "linux",
            tags=tags,
            launch_time=str(raw.get("LaunchTime", "")),
        )

    def _should_include(self, inst: EC2Instance) -> bool:
        """Return False if this instance should be excluded from analysis."""
        exclusions = self._config.get("exclusions", {})

        # Explicit instance ID exclusions
        if inst.instance_id in exclusions.get("instance_ids", []):
            logger.debug("Excluding %s: in explicit exclusion list", inst.instance_id)
            return False

        # Tag-based exclusions (e.g. DoNotRightsize: "true")
        for tag_key, tag_value in exclusions.get("tags", {}).items():
            if inst.tags.get(tag_key) == tag_value:
                logger.debug("Excluding %s: tag %s=%s", inst.instance_id, tag_key, tag_value)
                return False

        # Instance family exclusions (e.g. skip t3 burstable — metrics mislead)
        excluded_families = exclusions.get("instance_families", [])
        inst_family = inst.instance_type.split(".")[0]
        if inst_family in excluded_families:
            logger.debug("Excluding %s: instance family %s excluded", inst.instance_id, inst_family)
            return False

        return True
