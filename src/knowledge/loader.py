"""Knowledge base loader — load domain rules from YAML files.

Rules are loaded once and cached in-process. Reload by calling clear_cache().
"""

from pathlib import Path
from typing import Any
import yaml

_RULES_DIR = Path(__file__).parent / "rules"
_cache: dict[str, Any] = {}


def _load(filename: str) -> dict:
    if filename not in _cache:
        path = _RULES_DIR / filename
        with open(path, encoding="utf-8") as f:
            _cache[filename] = yaml.safe_load(f) or {}
    return _cache[filename]


def clear_cache() -> None:
    _cache.clear()


def get_financial_thresholds(sector: str | None = None) -> dict:
    """Return financial thresholds for a sector (falls back to defaults)."""
    data = _load("financial_thresholds.yaml")
    defaults = data.get("defaults", {})
    if sector:
        sector_key = _normalize_sector(sector)
        sector_overrides = data.get("sectors", {}).get(sector_key, {})
        # Deep-merge: sector overrides only specified sub-keys
        merged = {}
        for metric, default_vals in defaults.items():
            if isinstance(default_vals, dict):
                merged[metric] = {**default_vals, **sector_overrides.get(metric, {})}
            else:
                merged[metric] = sector_overrides.get(metric, default_vals)
        return merged
    return defaults


def get_reasonableness_bounds() -> dict:
    return _load("reasonableness_bounds.yaml")


def _normalize_sector(sector: str) -> str:
    """Map free-text sector to YAML key."""
    sector_lower = sector.lower()
    mapping = {
        "sản xuất": "manufacturing",
        "manufacturing": "manufacturing",
        "bán lẻ": "retail",
        "thương mại": "retail",
        "retail": "retail",
        "xây dựng": "construction",
        "construction": "construction",
        "bất động sản": "real_estate",
        "real estate": "real_estate",
        "dịch vụ": "services",
        "services": "services",
    }
    for keyword, key in mapping.items():
        if keyword in sector_lower:
            return key
    return "defaults"
