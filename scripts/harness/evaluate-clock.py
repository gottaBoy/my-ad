#!/usr/bin/env python3
"""Evaluate chrony synchronization state and offset against explicit thresholds."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracking", required=True, type=Path)
    parser.add_argument("--max-offset-ms", required=True, type=float)
    args = parser.parse_args()

    text = args.tracking.read_text(encoding="utf-8")
    offset_match = re.search(
        r"Last offset\s*:\s*([+-]?[\d.eE+-]+)\s+seconds",
        text,
    )
    stratum_match = re.search(r"Stratum\s*:\s*(\d+)", text)
    reference_match = re.search(r"Reference ID\s*:\s*([0-9A-Fa-f]+)", text)
    leap_match = re.search(r"Leap status\s*:\s*(.+)", text)
    if not offset_match or not stratum_match or not reference_match or not leap_match:
        raise SystemExit(
            "FAIL unable to parse chrony reference, stratum, Last offset, or Leap status"
        )

    stratum = int(stratum_match.group(1))
    reference_id = reference_match.group(1).upper()
    leap_status = leap_match.group(1).strip()
    if leap_status.lower() != "normal":
        raise SystemExit(f"FAIL chrony is not synchronized: Leap status={leap_status}")
    if stratum <= 0 or reference_id == "00000000":
        raise SystemExit(
            f"FAIL chrony has no valid time source: reference={reference_id} "
            f"stratum={stratum}"
        )

    offset_ms = abs(float(offset_match.group(1))) * 1000
    print(f"reference_id={reference_id}")
    print(f"stratum={stratum}")
    print(f"leap_status={leap_status}")
    print(f"absolute_offset_ms={offset_ms:.6f}")
    if offset_ms > args.max_offset_ms:
        raise SystemExit(
            f"FAIL clock offset {offset_ms:.6f}ms exceeds "
            f"{args.max_offset_ms:.6f}ms"
        )

    print("PASS clock threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
