"""utils/config.py — Load settings.yaml"""
import os
import yaml
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_config: dict | None = None


def get_config() -> dict:
    global _config
    if _config is None:
        with open(_ROOT / "config" / "settings.yaml") as f:
            _config = yaml.safe_load(f)
    return _config


def get(key_path: str, default=None):
    """Get a nested config value using dot notation. e.g. get('agent.model')"""
    parts = key_path.split(".")
    val = get_config()
    for p in parts:
        if not isinstance(val, dict):
            return default
        val = val.get(p, default)
    return val
