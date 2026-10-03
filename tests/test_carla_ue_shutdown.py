import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
PROBE = SCRIPTS / "probe-ue-shutdown-crash.sh"
GDB_SCRIPT = SCRIPTS / "dump-ue-shutdown.gdb"
CAPTURE = SCRIPTS / "gdb_ue_shutdown_capture.py"
ANALYZER = SCRIPTS / "analyze_ue_shutdown_capture.py"
MAKEFILE = ROOT / "Makefile"


def run_analyzer(run_dir):
    result = subprocess.run(
        [sys.executable, str(ANALYZER), "--run-dir", str(run_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads((Path(run_dir) / "shutdown-analysis.json").read_text())
    return result, report


def write_capture(run_dir, document):
    (Path(run_dir) / "shutdown-capture.json").write_text(json.dumps(document))


def resolved_breakpoints(**overrides):
    records = []
    for role, required in (
        ("check", True),
        ("remove", False),
        ("register", False),
        ("reset", False),
    ):
        record = {
            "role": role,
            "spec": role,
            "required": required,
            "resolved": True,
            "hits": 1,
            "hit_count": 1,
        }
        record.update(overrides.get(role, {}))
        records.append(record)
    return records


class ShutdownProbeContractTest(unittest.TestCase):
    """The shutdown capture has to be self-checking.

    The defect it exists for is a failed `check` during engine teardown. A capture that
    silently loses its instrumentation would look exactly like a clean shutdown, so the
    script, the debugger script and the analyzer are each tested for the property that makes
    its result mean something.
    """

    @classmethod
    def setUpClass(cls):
        cls.probe = PROBE.read_text(encoding="utf-8")
        cls.gdb = GDB_SCRIPT.read_text(encoding="utf-8")
        cls.capture = CAPTURE.read_text(encoding="utf-8")
        cls.analyzer = ANALYZER.read_text(encoding="utf-8")
        cls.makefile = MAKEFILE.read_text(encoding="utf-8")

    def test_probe_is_syntax_valid(self):
        subprocess.run(["bash", "-n", str(PROBE)], check=True)

    def test_shutdown_signal_is_passed_to_the_inferior(self):
        # Without `pass`, gdb swallows SIGTERM and the exit sequence under study never runs.
        self.assertRegex(self.gdb, r"handle SIGTERM nostop noprint pass")

    def test_handled_check_traps_do_not_stop_the_capture(self):
        # A debugger makes IsDebuggerPresent() true, so a failed check ends in PLATFORM_BREAK
        # instead of the fatal path; if that stopped gdb the run would never reach teardown.
        self.assertRegex(self.gdb, r"handle SIGTRAP nostop noprint nopass")

    def test_required_breakpoint_is_the_noinline_check_hook(self):
        # FORCENOINLINE in Core/Private/Misc/AssertionMacros.cpp, so it exists for every
        # failed check and cannot be inlined away.
        self.assertIn('"FDebug::CheckVerifyFailedImpl2"', self.capture)

    def test_failing_id_is_read_from_the_abi_not_only_from_dwarf(self):
        self.assertIn("read_register", self.capture)
        self.assertIn('_register(frame, "x1")', self.capture)
        self.assertIn('_register(frame, "x0")', self.capture)

    def test_handler_map_is_read_from_its_own_global(self):
        self.assertIn("GPUMessage::GSystem.MessageHandlers", self.capture)

    def test_map_destruction_is_watched_and_ordered(self):
        # The failed check proves the table is gone but not who removed it, so the capture
        # watches the write and stamps one order across every kind of observation.
        self.assertIn("BP_WATCHPOINT", self.capture)
        self.assertIn("Pairs.Elements.Data.ArrayNum", self.capture)
        self.assertIn("self.sequence", self.capture)
        self.assertIn('"order": self.current_order', self.capture)

    def test_roles_in_shared_libraries_stay_pending(self):
        # The exit marker lives in libc, which is not mapped when the breakpoints are created.
        # Deleting it as "unresolved" dropped that evidence once already, so the policy that
        # keeps such roles pending is part of the contract.
        self.assertIn("PENDING_ROLES", self.capture)
        self.assertIn('resolved_at_creation', self.capture)

    def test_release_phase_is_reported(self):
        self.assertIn("release_phases", self.analyzer)
        self.assertIn("inside-exit-handlers", self.analyzer)

    def test_inferior_is_discovered_by_proc_children_and_verified_by_exe(self):
        self.assertIn("/task/*/children", self.probe)
        self.assertIn("readlink -f", self.probe)
        # `pgrep -f` matched the probe's own command line in the first hand-run attempt and
        # killed PID 1, so no command may use it. The comment recording that lesson is fine.
        for line in self.probe.splitlines():
            if "pgrep" in line:
                self.assertTrue(
                    line.lstrip().startswith("#"),
                    f"pgrep used in a command: {line.strip()}",
                )
        self.assertIn('(( candidate > 1 ))', self.probe)

    def test_ptrace_capability_is_checked_before_the_run(self):
        self.assertIn("CapEff", self.probe)
        self.assertIn("CAP_SYS_PTRACE", self.probe)

    def test_probe_reports_instrumentation_and_discovery_provenance(self):
        for field in (
            "- Analysis status:",
            "- Inferior discovery:",
            "- Signals observed:",
            "- Shutdown:",
        ):
            self.assertIn(field, self.probe)

    def test_makefile_target_grants_ptrace_without_the_unsupported_flag(self):
        target = self.makefile.split("carla-ue-shutdown-crash:", 1)[1].split("\n\n", 1)[0]
        self.assertIn("--cap-add SYS_PTRACE", target)
        # `docker compose run` has no --security-opt in compose v5; passing it made every
        # debugger target exit before the container started.
        self.assertNotIn("--security-opt", self.makefile)


class ShutdownAnalysisTest(unittest.TestCase):
    def analyze(self, document, server_log="", gdb_log=""):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            write_capture(run_dir, document)
            if server_log:
                (run_dir / "server.log").write_text(server_log)
            if gdb_log:
                (run_dir / "gdb-messages.log").write_text(gdb_log)
            return run_analyzer(run_dir)

    def test_captures_the_target_assertion(self):
        result, report = self.analyze(
            {
                "breakpoints": resolved_breakpoints(),
                "check_events": [
                    {
                        "seq": 0,
                        "expr": "MessageHandlers.Contains(MessageId)",
                        "file": "Runtime/RenderCore/Private/GPUMessaging.cpp",
                        "line": 68,
                        "target": True,
                        "line_matches_source": True,
                        "handles": {"failing_message_id": 0},
                        "backtrace": "#0 ...",
                    }
                ],
                "socket_events": [
                    {"seq": 0, "role": "register", "next_message_id": 0},
                    {"seq": 1, "role": "remove", "message_id": 0},
                ],
                "stop_events": [],
                "exit_code": 143,
            },
            server_log="LogExit: Preparing to exit\n",
        )
        self.assertEqual(report["status"], "CAPTURED_TARGET_ASSERT")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(report["shutdown_log_marker"])
        self.assertTrue(report["ledger_consistent"])

    def test_unresolved_instrumentation_invalidates_the_run(self):
        # A missing hook cannot be told from a clean shutdown, so it must never be reported
        # as "no assertion happened".
        result, report = self.analyze(
            {"breakpoints": resolved_breakpoints(check={"resolved": False}), "check_events": []}
        )
        self.assertEqual(report["status"], "INVALID_INSTRUMENTATION")
        self.assertEqual(result.returncode, 6)

    def test_missing_capture_is_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            result, report = run_analyzer(Path(directory))
        self.assertEqual(report["status"], "INVALID_INSTRUMENTATION")
        self.assertEqual(result.returncode, 6)

    def test_absent_assertion_without_shutdown_evidence_is_inconclusive(self):
        _, report = self.analyze({"breakpoints": resolved_breakpoints(), "check_events": []})
        self.assertEqual(report["status"], "INCONCLUSIVE")
        self.assertIn("no shutdown evidence", report["reason"])

    def test_absent_assertion_with_shutdown_evidence_is_a_negative_result(self):
        _, report = self.analyze(
            {
                "breakpoints": resolved_breakpoints(),
                "check_events": [],
                "exit_code": 0,
            },
            server_log="LogExit: Exiting\n",
        )
        self.assertEqual(report["status"], "NO_TARGET_ASSERT")

    def test_exit_status_falls_back_to_the_debugger_transcript(self):
        # gdb reports exit_code None for some signal terminations, and an unknown exit status
        # would silently weaken the shutdown classification.
        _, report = self.analyze(
            {"breakpoints": resolved_breakpoints(), "check_events": [], "exit_code": None},
            gdb_log="[Inferior 1 (process 42) exited with code 0139]\n",
        )
        self.assertEqual(report["exit"]["effective_code"], 139)
        self.assertEqual(report["exit"]["source"], "gdb-transcript")

    def test_ledger_reports_ids_removed_without_a_registration(self):
        _, report = self.analyze(
            {
                "breakpoints": resolved_breakpoints(),
                "check_events": [],
                "socket_events": [
                    {"seq": 0, "role": "register", "next_message_id": 0},
                    {"seq": 1, "role": "remove", "message_id": 0},
                    {"seq": 2, "role": "remove", "message_id": 7},
                    {"seq": 3, "role": "remove", "message_id": 7},
                ],
            },
            server_log="LogExit: Exiting\n",
        )
        self.assertEqual(report["ledger"]["removed_without_register"], [7])
        self.assertEqual(report["ledger"]["removed_more_than_once"], [7])

    def watch_breakpoints(self, observations=0):
        records = resolved_breakpoints()
        records.append(
            {
                "role": "map-write",
                "spec": "GPUMessage::GSystem.MessageHandlers.Pairs.Elements.Data.ArrayNum",
                "required": False,
                "resolved": bool(observations),
                "hits": observations,
            }
        )
        return records

    def target_event(self, order):
        return {
            "seq": 0,
            "order": order,
            "expr": "MessageHandlers.Contains(MessageId)",
            "file": "Runtime/RenderCore/Private/GPUMessaging.cpp",
            "line": 68,
            "target": True,
            "line_matches_source": True,
            "handles": {"failing_message_id": 0},
        }

    def test_map_destroyed_before_the_failed_check_is_the_finding(self):
        _, report = self.analyze(
            {
                "breakpoints": self.watch_breakpoints(observations=2),
                "check_events": [self.target_event(order=8)],
                "watch_events": [
                    {"seq": 0, "order": 2, "value": 1, "handlers": {"handlers": 1}, "stack": []},
                    {"seq": 1, "order": 5, "value": 0, "handlers": {"handlers": 0}, "stack": []},
                ],
            },
            server_log="LogExit: Preparing to exit\n",
        )
        self.assertEqual(report["status"], "CAPTURED_TARGET_ASSERT")
        self.assertTrue(report["map_cleared_before_check"])
        self.assertEqual(report["map"]["cleared_order"], 5)
        self.assertEqual(report["map_write_events"], 2)
        self.assertNotIn(
            "the map write watchpoint did not fire before the failed check, so the ordering of "
            "the destruction and the failure is unmeasured",
            report["notes"],
        )

    def test_map_write_after_the_failed_check_is_not_the_finding(self):
        _, report = self.analyze(
            {
                "breakpoints": self.watch_breakpoints(observations=1),
                "check_events": [self.target_event(order=5)],
                "watch_events": [
                    {"seq": 0, "order": 9, "value": 0, "handlers": {"handlers": 0}, "stack": []}
                ],
            },
            server_log="LogExit: Exiting\n",
        )
        self.assertFalse(report["map_cleared_before_check"])
        self.assertTrue(any("fired after the failed check" in note for note in report["notes"]))

    def test_silent_watchpoint_leaves_the_ordering_unmeasured(self):
        # A watchpoint is armed on an address, so "it never fired" has to stay a gap rather
        # than become a conclusion about the teardown order.
        _, report = self.analyze(
            {
                "breakpoints": self.watch_breakpoints(),
                "check_events": [self.target_event(order=8)],
                "watch_events": [],
            },
            server_log="LogExit: Exiting\n",
        )
        self.assertIsNone(report["map_cleared_before_check"])
        self.assertTrue(
            any("did not fire before the failed check" in note for note in report["notes"])
        )
        self.assertTrue(any("never fired" in note for note in report["notes"]))


if __name__ == "__main__":
    unittest.main()
