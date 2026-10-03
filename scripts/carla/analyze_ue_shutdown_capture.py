"""Validate and summarize a UE shutdown capture produced by probe-ue-shutdown-crash.sh.

The point of this step is to refuse to read a clean result out of a broken measurement. A
capture where the required breakpoint never resolved, or where the shutdown signal evidently
never arrived, cannot support the claim "no failed check happened", so it is reported as
inconclusive instead. Statuses and their exit codes:

    CAPTURED_TARGET_ASSERT   0  the GPUMessaging.cpp check failed and a stack was captured
    NO_TARGET_ASSERT         4  instrumentation armed and shutdown observed, no such check
    INCONCLUSIVE             5  shutdown was not observed, so a missing assertion means nothing
    INVALID_INSTRUMENTATION  6  the required breakpoint did not resolve, or no capture exists
"""

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
SHUTDOWN_PATTERN = re.compile(r"LogExit: (Preparing to exit|Exiting)")
EXIT_CODES = {
    "CAPTURED_TARGET_ASSERT": 0,
    "NO_TARGET_ASSERT": 4,
    "INCONCLUSIVE": 5,
    "INVALID_INSTRUMENTATION": 6,
}


def load_json(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        return {"_malformed": str(exc)}


def read_text(path):
    try:
        return path.read_text(errors="replace")
    except FileNotFoundError:
        return ""


GDB_EXIT_PATTERN = re.compile(
    r"Inferior \d+ \(process \d+\) (?:exited with code (\d+)|terminated by signal (\w+))"
)


def exit_evidence(capture_code, capture_signal, gdb_log):
    """The exit status comes from gdb's event when it has one, and from the batch transcript
    when it does not: gdb reports `exit_code` as None for several signal terminations, and an
    unknown exit status silently weakens the shutdown classification."""
    evidence = {
        "code": capture_code,
        "signal": capture_signal,
        "source": "gdb-event" if capture_code is not None else None,
        "effective_code": capture_code,
        "effective_signal": capture_signal,
        "gdb_line": None,
    }
    match = GDB_EXIT_PATTERN.search(gdb_log)
    if match:
        evidence["gdb_line"] = match.group(0)
        if match.group(1) is not None and evidence["effective_code"] is None:
            evidence["effective_code"] = int(match.group(1))
            evidence["source"] = "gdb-transcript"
        elif match.group(2) is not None and evidence["effective_signal"] is None:
            evidence["effective_signal"] = match.group(2)
            evidence["source"] = "gdb-transcript"
    return evidence


def frame_functions(event, limit=6):
    return [
        frame.get("function") or f"<unknown@{frame.get('address')}>"
        for frame in (event.get("stack") or [])[:limit]
    ]


def watch_summary(watch_events):
    """Which write emptied the handler map, and with what stack.

    The failed check proves the table is gone but not who removed it, so this is the part of
    the capture that answers the question the probe exists for. It deliberately reports the
    cleared write separately from the earlier ones: the map is also written when the handler is
    added, and mixing the two would read like two destructions.
    """
    events = []
    for event in watch_events:
        events.append(
            {
                "seq": event.get("seq"),
                "order": event.get("order"),
                "value": event.get("value"),
                "handlers": (event.get("handlers") or {}).get("handlers"),
                "top_frames": frame_functions(event),
            }
        )
    cleared = [
        event
        for event in watch_events
        if event.get("value") == 0 and (event.get("handlers") or {}).get("handlers") == 0
    ]
    return {
        "events": events,
        "cleared_order": cleared[-1].get("order") if cleared else None,
        "cleared_by": frame_functions(cleared[-1], limit=8) if cleared else None,
        "cleared_backtrace": cleared[-1].get("backtrace") if cleared else None,
    }


def build_timeline(capture):
    """One ordered view across every kind of observation.

    Each list carries its own `seq`, which cannot order a map write against a failed check, so
    the shared `order` is what makes the sequence readable as one story: registration, the
    writes to the map, the sockets being reset, the failed check.
    """
    events = []
    for role, entries in (
        ("check", capture.get("check_events", [])),
        ("watch", capture.get("watch_events", [])),
        ("socket", capture.get("socket_events", [])),
    ):
        for entry in entries:
            if role == "check":
                detail = f"{entry.get('expr')} at {Path(str(entry.get('file'))).name}:{entry.get('line')}"
            elif role == "watch":
                detail = f"value={entry.get('value')}"
            else:
                message = entry.get("abi_message_id")
                if message is None:
                    message = entry.get("abi_this_message_id")
                detail = f"id={message}"
            events.append(
                {
                    "order": entry.get("order"),
                    "kind": role,
                    "role": entry.get("role", role),
                    "map_handlers": (entry.get("handlers") or {}).get("handlers"),
                    "detail": detail,
                }
            )
    return sorted(events, key=lambda event: event["order"] if event["order"] is not None else -1)


def release_phases(socket_events):
    """Attribute each release to a teardown phase.

    A release that runs before `exit()` cannot be a static destructor, because those only run
    from inside it. That distinction is what the lifetime-based alternative fix would have to be
    built on, and it is the part of the release picture this capture can actually establish: the
    caller of `FRenderResource::ReleaseResource` itself is unreachable here.
    """
    exit_order = next(
        (event.get("order") for event in socket_events if event.get("role") == "exit"), None
    )
    phases = []
    for event in socket_events:
        role = event.get("role") or ""
        if not role.startswith("release-"):
            continue
        order = event.get("order")
        if exit_order is None or not isinstance(order, int):
            phase = "unknown"
        elif order < exit_order:
            phase = "before-exit"
        else:
            phase = "inside-exit-handlers"
        phases.append(
            {
                "role": role,
                "order": order,
                "map_handlers": (event.get("handlers") or {}).get("handlers"),
                "phase": phase,
                "caller": event.get("caller"),
            }
        )
    return {"exit_order": exit_order, "releases": phases}


def ledger_summary(socket_events):
    registered = [
        event["next_message_id"]
        for event in socket_events
        if event.get("role") == "register" and isinstance(event.get("next_message_id"), int)
    ]
    removed = [
        event["message_id"]
        for event in socket_events
        if event.get("role") == "remove" and isinstance(event.get("message_id"), int)
    ]
    resets = [
        event["this_message_id"]
        for event in socket_events
        if event.get("role") == "reset" and isinstance(event.get("this_message_id"), int)
    ]
    return {
        "registered_ids": registered,
        "removed_ids": removed,
        "reset_socket_ids": resets,
        "removed_without_register": sorted({value for value in removed if value not in registered}),
        "removed_more_than_once": sorted(
            {value for value in removed if removed.count(value) > 1}
        ),
    }


def analyze(run_dir):
    capture_path = run_dir / "shutdown-capture.json"
    capture = load_json(capture_path)
    # gdb's own messages go to gdb-messages.log because the script turns on logging redirect,
    # which keeps the inferior's engine log in server.log where LogExit can be read.
    gdb_log = read_text(run_dir / "gdb-messages.log") or read_text(run_dir / "gdb.log")
    server_log = read_text(run_dir / "server.log")

    report = {
        "schema_version": SCHEMA_VERSION,
        "run_dir": str(run_dir),
        "status": None,
        "reason": None,
        "instrumentation": [],
        "required_breakpoints_resolved": False,
        "target_found": False,
        "check_events": 0,
        "other_failed_checks": [],
        "target_event": None,
        "socket_events": 0,
        "ledger": None,
        "ledger_consistent": None,
        "watchpoint": None,
        "map_write_events": 0,
        "map": None,
        "map_cleared_before_check": None,
        "release_phases": None,
        "timeline": [],
        "shutdown_log_marker": bool(SHUTDOWN_PATTERN.search(server_log)),
        "gdb_reported_signal": None,
        "exit": None,
        "stop_events": [],
        "notes": [],
    }

    if capture is None or "_malformed" in (capture or {}):
        report["status"] = "INVALID_INSTRUMENTATION"
        report["reason"] = (
            "shutdown-capture.json is missing or malformed"
            if capture is None
            else f"shutdown-capture.json is malformed: {capture['_malformed']}"
        )
        return report

    report["instrumentation"] = [
        {
            "role": record.get("role"),
            "spec": record.get("spec"),
            "required": record.get("required"),
            "resolved": bool(record.get("resolved")),
            "observations": record.get("hits"),
        }
        for record in capture.get("breakpoints", [])
    ]
    required = [item for item in report["instrumentation"] if item["required"]]
    unresolved = [item for item in required if not item["resolved"]]
    report["required_breakpoints_resolved"] = bool(required) and not unresolved
    report["notes"].extend(
        f"{item['role']} breakpoint unresolved: {item['spec']}" for item in unresolved
    )
    report["exit"] = exit_evidence(capture.get("exit_code"), capture.get("exit_signal"), gdb_log)
    report["stop_events"] = capture.get("stop_events", [])

    check_events = capture.get("check_events", [])
    report["check_events"] = len(check_events)
    report["other_failed_checks"] = sorted(
        {f"{event.get('file')}:{event.get('line')} {event.get('expr')}" for event in check_events if not event.get("target")}
    )
    target = next((event for event in check_events if event.get("target")), None)
    report["target_found"] = target is not None

    watch_events = capture.get("watch_events", [])
    report["watchpoint"] = next(
        (
            {
                "spec": item["spec"],
                "observations": item["observations"],
                "resolution": "fired" if item["observations"] else "unknown",
            }
            for item in report["instrumentation"]
            if item["role"] == "map-write"
        ),
        None,
    )
    report["map_write_events"] = len(watch_events)
    report["map"] = watch_summary(watch_events)
    report["release_phases"] = release_phases(capture.get("socket_events", []))
    if report["watchpoint"] and not report["watchpoint"]["observations"]:
        report["notes"].append(
            "the map write watchpoint never fired, so this run says nothing about which "
            "teardown path emptied the handler map"
        )

    socket_events = capture.get("socket_events", [])
    report["socket_events"] = len(socket_events)
    report["ledger"] = ledger_summary(socket_events)

    report["timeline"] = build_timeline(capture)

    if not report["required_breakpoints_resolved"]:
        report["status"] = "INVALID_INSTRUMENTATION"
        report["reason"] = "the required check breakpoint did not resolve"
        return report

    if target is not None:
        report["target_event"] = {
            "seq": target.get("seq"),
            "order": target.get("order"),
            "expr": target.get("expr"),
            "file": target.get("file"),
            "line": target.get("line"),
            "line_matches_source": target.get("line_matches_source"),
            "spec": target.get("spec"),
            "stack": target.get("stack"),
            "handles": target.get("handles"),
            "backtrace": target.get("backtrace"),
            "backtrace_full": target.get("backtrace_full"),
        }
        removed = report["ledger"]["removed_ids"]
        handles = target.get("handles") or {}
        failing = handles.get("failing_message_id")
        if isinstance(failing, int) and removed:
            report["ledger_consistent"] = removed[-1] == failing
            if not report["ledger_consistent"]:
                report["notes"].append(
                    f"the id read from the assertion frame ({failing}) is not the last "
                    f"RemoveHandler id observed ({removed[-1]})"
                )
        cleared_order = (report["map"] or {}).get("cleared_order")
        check_order = target.get("order")
        if isinstance(cleared_order, int) and isinstance(check_order, int):
            # This is the finding stated as the ordering that decides it: the map was destroyed
            # before the check that then failed, so the check is a consequence, not the cause.
            report["map_cleared_before_check"] = cleared_order < check_order
            if not report["map_cleared_before_check"]:
                report["notes"].append(
                    "the map write watchpoint fired after the failed check, so this capture does "
                    "not show the destruction preceding the failure"
                )
        else:
            report["notes"].append(
                "the map write watchpoint did not fire before the failed check, so the "
                "ordering of the destruction and the failure is unmeasured"
            )
        report["status"] = "CAPTURED_TARGET_ASSERT"
        return report

    observed_shutdown = bool(
        report["shutdown_log_marker"]
        or report["exit"]["effective_code"] is not None
        or report["stop_events"]
        or re.search(r"\bLogExit:", server_log)
    )
    if not observed_shutdown:
        report["status"] = "INCONCLUSIVE"
        report["reason"] = (
            "no shutdown evidence: the log has no LogExit marker, the inferior exit status is "
            "unknown and no signal was recorded, so an absent assertion is not evidence"
        )
        return report

    report["status"] = "NO_TARGET_ASSERT"
    report["reason"] = "instrumentation armed and shutdown observed, but the GPUMessaging check did not fail"
    return report


def summarize(report):
    lines = [
        f"status: {report['status']}",
        f"reason: {report['reason']}",
        "instrumentation: "
        + ", ".join(
            f"{item['role']}={item['spec']}(resolved={item['resolved']},observations={item['observations']})"
            for item in report["instrumentation"]
        ),
        f"failed checks observed: {report['check_events']} (target={report['target_found']})",
        f"socket events: {report['socket_events']}",
        "timeline:",
    ]
    lines.extend(
        f"  order={str(event['order']):>2} {event['role']:<16} "
        f"map_handlers={event['map_handlers']} {event['detail']}"
        for event in report["timeline"]
    )
    lines.extend(
        [
            f"shutdown log marker: {report['shutdown_log_marker']}",
            f"exit: {json.dumps(report['exit'], sort_keys=True)}",
            f"stop signals: {[event.get('signal') for event in report['stop_events']]}",
        ]
    )
    if report["ledger"]:
        lines.append(f"ledger: {json.dumps(report['ledger'], sort_keys=True)}")
    if report["release_phases"]:
        lines.append(f"exit marker order: {report['release_phases']['exit_order']}")
        for release in report["release_phases"]["releases"]:
            lines.append(
                f"  release {release['role']} order={release['order']} "
                f"map_handlers={release['map_handlers']} phase={release['phase']} "
                f"caller={release['caller']}"
            )
    if report["watchpoint"]:
        lines.append(
            "map write watchpoint: "
            f"{report['watchpoint']['spec']} "
            f"({report['watchpoint']['resolution']}, "
            f"observations={report['watchpoint']['observations']})"
        )
        for event in report["map"]["events"]:
            lines.append(
                f"  write seq={event['seq']} order={event['order']} value={event['value']} "
                f"handlers={event['handlers']} top={event['top_frames'][:4]}"
            )
        if report["map"]["cleared_by"]:
            lines.append(f"  map cleared by: {report['map']['cleared_by'][:5]}")
        lines.append(f"map cleared before the failed check: {report['map_cleared_before_check']}")
    if report["target_event"]:
        target = report["target_event"]
        lines.append(f"target: {target['file']}:{target['line']} {target['expr']}")
        lines.append(f"target handles: {json.dumps(target['handles'], sort_keys=True)}")
        lines.append(f"ledger consistent: {report['ledger_consistent']}")
        if target.get("backtrace"):
            lines.append("backtrace:")
            lines.extend(target["backtrace"].rstrip("\n").splitlines()[:24])
    for note in report["notes"]:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", default=None)
    arguments = parser.parse_args()

    run_dir = Path(arguments.run_dir)
    report = analyze(run_dir)
    output = Path(arguments.output) if arguments.output else run_dir / "shutdown-analysis.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(summarize(report))
    return EXIT_CODES.get(report["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
