from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class HarnessEvaluatorTest(unittest.TestCase):
    def test_network_evaluator_accepts_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ping = root / "ping.txt"
            iperf = root / "iperf.json"
            ping.write_text(
                "10 packets transmitted, 10 received, 0% packet loss\n"
                "rtt min/avg/max/mdev = 0.100/0.200/0.300/0.040 ms\n",
                encoding="utf-8",
            )
            iperf.write_text(
                json.dumps(
                    {
                        "end": {
                            "sum_received": {
                                "bits_per_second": 1_000_000_000,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts/harness/evaluate-network.py"),
                    "--ping",
                    str(ping),
                    "--iperf",
                    str(iperf),
                    "--min-throughput-mbps",
                    "500",
                    "--max-jitter-ms",
                    "5",
                    "--max-loss-pct",
                    "0.1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PASS network thresholds", result.stdout)

    def test_clock_evaluator_rejects_large_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tracking = Path(temp) / "tracking.txt"
            tracking.write_text(
                "Reference ID    : A9FEA9FE (169.254.169.254)\n"
                "Stratum         : 4\n"
                "Last offset     : +0.010000000 seconds\n"
                "Leap status     : Normal\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts/harness/evaluate-clock.py"),
                    "--tracking",
                    str(tracking),
                    "--max-offset-ms",
                    "5",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL clock offset", result.stderr)

    def test_clock_evaluator_accepts_synchronized_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tracking = Path(temp) / "tracking.txt"
            tracking.write_text(
                "Reference ID    : A9FEA9FE (169.254.169.254)\n"
                "Stratum         : 4\n"
                "Last offset     : -0.000210000 seconds\n"
                "Leap status     : Normal\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts/harness/evaluate-clock.py"),
                    "--tracking",
                    str(tracking),
                    "--max-offset-ms",
                    "5",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PASS clock threshold", result.stdout)

    def test_clock_evaluator_rejects_unsynchronized_chrony(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tracking = Path(temp) / "tracking.txt"
            tracking.write_text(
                "Reference ID    : 00000000 ()\n"
                "Stratum         : 0\n"
                "Last offset     : +0.000000000 seconds\n"
                "Leap status     : Not synchronised\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts/harness/evaluate-clock.py"),
                    "--tracking",
                    str(tracking),
                    "--max-offset-ms",
                    "5",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL chrony is not synchronized", result.stderr)


if __name__ == "__main__":
    unittest.main()
