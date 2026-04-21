"""
aws_session.py — creates a configured boto3 session.

Centralises session creation so the rest of the codebase doesn't
need to worry about profile selection or region defaults.
"""

import logging
import os

import boto3
from botocore.exceptions import NoCredentialsError, NoRegionError

logger = logging.getLogger(__name__)


def get_session(region: str) -> boto3.Session:
    """
    Create a boto3 session for the given region.

    Credential resolution order (standard boto3 chain):
      1. Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
      2. AWS profile (AWS_PROFILE env var or ~/.aws/credentials)
      3. IAM instance role / ECS task role / EKS IRSA
    """
    profile = os.environ.get("AWS_PROFILE")

    try:
        session = boto3.Session(region_name=region, profile_name=profile)
        # Validate credentials early so we fail fast with a clear error
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        logger.debug(
            "Using AWS identity: account=%s, arn=%s",
            identity["Account"],
            identity["Arn"],
        )
        return session

    except NoCredentialsError:
        raise SystemExit(
            "No AWS credentials found.\n"
            "Configure credentials via environment variables, AWS profile, or IAM role.\n"
            "See: https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html"
        )
    except NoRegionError:
        raise SystemExit(f"Invalid or unavailable region: {region}")
