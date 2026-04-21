"""
logger.py — configures structured logging for the application.
"""

import logging
import sys


def setup_logger(level: int = logging.INFO) -> logging.Logger:
    """
    Configure root logger with a consistent format.
    Returns the root logger for convenience.
    """
    log_format = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt=date_format,
        stream=sys.stdout,
        force=True,
    )

    # Quieten noisy third-party loggers
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    return logging.getLogger()
