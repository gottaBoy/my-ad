#!/usr/bin/env python3
"""Normalize generated CARLA runtime config references against cooked assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


OLD_REFERENCE = (
    "/Game/Carla/Static/Dynamic/Construction/"
    "Sm_ConstructionDebrie.Sm_ConstructionDebrie"
)
NEW_REFERENCE = (
    "/Game/Carla/Static/Dynamic/Construction/"
    "SM_DebrisContainer.SM_DebrisContainer"
)
CONFIG_NAMES = ("PropParameters.json", "Default.Package.json")
TARGET_ASSET = Path(
    "Content/Carla/Static/Dynamic/Construction/SM_DebrisContainer.uasset"
)


def replace_reference(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return (NEW_REFERENCE, 1) if value == OLD_REFERENCE else (value, 0)
    if isinstance(value, list):
        items = [replace_reference(item) for item in value]
        return [item for item, _ in items], sum(count for _, count in items)
    if isinstance(value, dict):
        items = {key: replace_reference(item) for key, item in value.items()}
        return (
            {key: item for key, (item, _) in items.items()},
            sum(count for _, count in items.values()),
        )
    return value, 0


def normalize(stage_root: Path) -> int:
    target_asset = stage_root / TARGET_ASSET
    if not target_asset.is_file():
        raise RuntimeError(f"replacement cooked asset is missing: {target_asset}")

    total = 0
    changes = []
    config_root = stage_root / "Content/Carla/Config"
    for name in CONFIG_NAMES:
        path = config_root / name
        if not path.is_file():
            raise RuntimeError(f"runtime config is missing: {path}")
        text = path.read_text()
        parsed = json.loads(text)
        updated, count = replace_reference(parsed)
        if count:
            changes.append((path, updated))
        total += count

    for path, updated in changes:
        path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-root", required=True, type=Path)
    args = parser.parse_args()
    count = normalize(args.stage_root)
    print(f"runtime config normalized references={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
