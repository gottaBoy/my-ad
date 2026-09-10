#!/usr/bin/env python3
"""Evaluate ping and iperf3 evidence against explicit network thresholds."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ping", required=True, type=Path)
    parser.add_argument("--iperf", required=True, type=Path)
    parser.add_argument("--min-throughput-mbps", required=True, type=float)
    parser.add_argument("--max-jitter-ms", required=True, type=float)
    parser.add_argument("--max-loss-pct", required=True, type=float)
    args = parser.parse_args()

    ping_text = args.ping.read_text(encoding="utf-8")
    loss_match = re.search(r"([\d.]+)% packet loss", ping_text)
    jitter_match = re.search(
        r"(?:mdev|stddev)\s*=\s*[\d.]+/[\d.]+/[\d.]+/([\d.]+)\s*ms",
        ping_text,
    )
    if not loss_match or not jitter_match:
        raise SystemExit("FAIL unable to parse ping packet loss or jitter")

    loss_pct = float(loss_match.group(1))
    jitter_ms = float(jitter_match.group(1))

    iperf = json.loads(args.iperf.read_text(encoding="utf-8"))
    if iperf.get("error"):
        raise SystemExit(f"FAIL iperf3 error: {iperf['error']}")

    summary = iperf.get("end", {})
    transfer = summary.get("sum_received") or summary.get("sum") or {}
    bits_per_second = transfer.get("bits_per_second")
    if bits_per_second is None:
        raise SystemExit("FAIL unable to parse iperf3 throughput")
    throughput_mbps = float(bits_per_second) / 1_000_000

    print(f"packet_loss_pct={loss_pct:.3f}")
    print(f"ping_jitter_ms={jitter_ms:.3f}")
    print(f"throughput_mbps={throughput_mbps:.3f}")

    failures = []
    if loss_pct > args.max_loss_pct:
        failures.append(
            f"packet loss {loss_pct:.3f}% exceeds {args.max_loss_pct:.3f}%"
        )
    if jitter_ms > args.max_jitter_ms:
        failures.append(
            f"jitter {jitter_ms:.3f}ms exceeds {args.max_jitter_ms:.3f}ms"
        )
    if throughput_mbps < args.min_throughput_mbps:
        failures.append(
            f"throughput {throughput_mbps:.3f}Mbps is below "
            f"{args.min_throughput_mbps:.3f}Mbps"
        )

    if failures:
        raise SystemExit("FAIL " + "; ".join(failures))

    print("PASS network thresholds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
