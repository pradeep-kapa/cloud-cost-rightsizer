"""
config.py — loads and validates the YAML config file.
"""

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default config — used when no config file is present
_DEFAULTS: dict[str, Any] = {
    "analysis": {
        "lookback_days": 14,
        "metrics_period_seconds": 3600,
    },
    "thresholds": {
        "cpu_max_p95":    20.0,
        "memory_max_p95": 30.0,
        "network_max_mbps": 100.0,
    },
    "exclusions": {
        "instance_ids":       [],
        "tags":               {},
        "instance_families":  ["t3", "t3a"],
    },
    "reporting": {
        "formats": ["csv", "json", "summary"],
        "slack": {
            "enabled": False,
            "webhook_url": "",
            "channel": "#finops",
            "mention_on_savings_above": 10000,
        },
    },
    "pricing": {
        "cache_enabled":  True,
        "cache_ttl_hours": 24,
    },
}


def load_config(config_path: Path) -> dict:
    """
    Load config from a YAML file and merge with defaults.
    Missing keys fall back to the defaults — callers get a complete config dict.
    """
    config = _deep_copy(_DEFAULTS)

    if not config_path.exists():
        logger.info("No config file found at %s — using defaults", config_path)
        return config

    try:
        import yaml  # lazy import — only required if a config file exists
    except ImportError:
        logger.warning("PyYAML not installed — using defaults (pip install pyyaml)")
        return config

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        config = _deep_merge(config, raw)
        logger.debug("Loaded config from %s", config_path)
    except Exception as exc:
        logger.error("Failed to load config from %s: %s — using defaults", config_path, exc)

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override values win."""
    result = _deep_copy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _deep_copy(obj: Any) -> Any:
    """Simple deep copy for plain dicts/lists/scalars."""
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_copy(v) for v in obj]
    return obj
