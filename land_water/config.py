from __future__ import annotations

import json
from pathlib import Path

import yaml


def load_config(path: Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def save_config(path: Path, config: dict) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)


def config_text(config: dict, format: str) -> str:
    if format == "json":
        return json.dumps(config, ensure_ascii=False, indent=2)
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
