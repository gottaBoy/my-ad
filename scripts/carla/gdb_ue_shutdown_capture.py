"""Capture the failing check behind the UE shutdown crash in RenderCore/GPUMessaging.cpp.

The engine-side defect is a failed `check(MessageHandlers.Contains(MessageId))` inside
`GPUMessage::FSystem::RemoveHandler` during shutdown. In the wild the failure is followed by
`Signal 11` and exit 139, but the fatal path is entered *after* the failed check
(`LogCore: Engine exit requested (reason: EngineExAssertion failed: ...)`), so the log alone
cannot say who called `RemoveHandler` with a stale id. This module records exactly that.

Two properties of the engine decide how it is instrumented, and both were read out of the
source rather than guessed:

1. `check()` expands to `FDebug::CheckVerifyFailedImpl2(#expr, __FILE__, __LINE__, TEXT(""))`
   (AssertionMacros.h:237). That function is declared `FORCENOINLINE`
   (AssertionMacros.cpp:624) and its address resolves in the staged binary, so a breakpoint
   on it is a trustworthy hook for *every* failed check in the process. Whether it resolved
   is recorded, because "no assertion observed" is worthless if the instrument was absent.
2. Under a debugger `FPlatformMisc::IsDebuggerPresent()` is true, so
   `CheckVerifyFailedImpl2` logs the message, skips `AssertFailedImplV` and returns true;
   the caller then runs `PLATFORM_BREAK()`. The observed exit path therefore cannot be
   reproduced verbatim while gdb is attached, which the capture reports as a boundary.

Every candidate breakpoint is tried by minimal name first and then by full signature, and the
spec that actually resolved is recorded per role, so an unresolved fallback shows up as a
missing fact instead of a silent gap.
"""

import json
import os
import re
import traceback
from pathlib import Path

import gdb

SCHEMA_VERSION = 1
SCOPE = (
    "gdb capture of the first failed check in GPUMessaging.cpp during the NullRHI shutdown "
    "sequence; no engine patch, no rendering, no product acceptance"
)
TARGET_EXPR = "MessageHandlers.Contains(MessageId)"
TARGET_EXPR_NORMALIZED = re.sub(r"\s+", "", TARGET_EXPR)
TARGET_FILE = "GPUMessaging.cpp"
TARGET_SOURCE_LINE = 68

MAX_EVENTS = 64
MAX_STOP_EVENTS = 16
MAX_FRAMES = 48
MAX_SCAN_FRAMES = 24

# Reading `MessageId` through DWARF was measured wrong on this build: the value reported for the
# `RemoveHandler` argument equalled the caller's `FSocket*` truncated to 32 bits, which is the
# signature of reading a register that no longer holds the argument. The ABI registers are read
# as well, and the handler map is read directly from its owning global, because the id is the
# whole question and a plausible-looking misread would hide the answer.
MAP_EXPRESSION = os.environ.get(
    "CARLA_UE_SHUTDOWN_MAP_EXPRESSION", "GPUMessage::GSystem.MessageHandlers"
)

# Watching the map's own element count is the only way to name whatever empties it: the failed
# `check` proves the table is gone, but not which teardown path removed it. A write watchpoint
# on the sparse array's element count catches that write with a stack, and it needs no symbol
# that may have been inlined away. Both the initial `Add` and the destruction are recorded, so
# the ordering is visible rather than assumed.
WATCH_ROLE = "map-write"
WATCH_EXPRESSION = f"{MAP_EXPRESSION}.Pairs.Elements.Data.ArrayNum"
MAX_WATCH_EVENTS = 8
WATCH_BACKTRACE_FRAMES = 14

# (role, required, candidate specs in order). The check hook is required: without it the run
# cannot distinguish "no failed check" from "no instrumentation". The other three only add
# context, and `GPUMessage::FSocket::~FSocket` is deliberately absent because the implicit
# destructor has no symbol in the staged binary.
BREAKPOINT_ROLES = (
    (
        "check",
        True,
        (
            "FDebug::CheckVerifyFailedImpl2",
            "FDebug::CheckVerifyFailedImpl2(char const*, char const*, int, char16_t const*, ...)",
        ),
    ),
    (
        "remove",
        False,
        (
            "GPUMessage::FSystem::RemoveHandler",
            "GPUMessage::FSystem::RemoveHandler(TRDGHandle<GPUMessage::FSocket, unsigned int>)",
        ),
    ),
    (
        "register",
        False,
        (
            "GPUMessage::FSystem::RegisterHandler",
            "GPUMessage::FSystem::RegisterHandler(TSharedPtr<GPUMessage::FHandler, (ESPMode)1> const&)",
        ),
    ),
    (
        "reset",
        False,
        ("GPUMessage::FSocket::Reset", "GPUMessage::FSocket::Reset()"),
    ),
    # The release paths are here to settle one question the crash capture leaves open: whether
    # GPUMessage::FSystem::ReleaseRHI ran before the socket that fails to unregister, which
    # decides between "released first and the map died anyway" and "never released at all".
    # Both are optional: a spec that does not resolve is reported, not assumed to be silent.
    (
        "release-gsystem",
        False,
        ("GPUMessage::FSystem::ReleaseRHI", "GPUMessage::FSystem::ReleaseRHI()"),
    ),
    (
        "release-nanite",
        False,
        (
            "Nanite::FGlobalResources::ReleaseRHI",
            "Nanite::FGlobalResources::ReleaseRHI()",
        ),
    ),
    # The exit marker splits the shutdown into "before the atexit handlers run" and "inside
    # them", which is what decides whether a release is an explicit call during RHI shutdown or
    # a static destructor. It replaces a hot breakpoint on FRenderResource::ReleaseResource,
    # whose caller gdb cannot unwind past in this build.
    (
        "exit",
        False,
        ("exit",),
    ),
)

# A role whose symbol lives in a shared library cannot resolve before the program starts: libc
# is not mapped yet when the breakpoints are created. Requiring immediate resolution is the right
# rule for the binary's own symbols, but applying it to these would delete a breakpoint that
# would have bound at startup -- which is exactly what happened to the exit marker on the first
# attempt. They are created pending instead, and whether they bound is read back after the run.
PENDING_ROLES = {"exit"}


def _capture_dir():
    return os.environ.get("CARLA_UE_SHUTDOWN_CAPTURE_DIR") or os.getcwd()


def _cstring(frame, name):
    """Read a `const ANSICHAR*` argument; None when the value is not a readable string."""
    try:
        value = frame.read_var(name)
    except Exception:
        return None
    try:
        if int(value) == 0:
            return None
        text = value.string()
    except Exception:
        return None
    return text.split("\0", 1)[0]


def _int_of(value):
    try:
        return int(value)
    except Exception:
        return None


def _handle_index(value):
    """`FMessageId` is `TRDGHandle<FSocket, uint32>`, whose payload field is `Index`."""
    if value is None:
        return None
    try:
        if value.type.code == gdb.TYPE_CODE_PTR:
            value = value.dereference()
    except Exception:
        return None
    for field in ("Index", "Value"):
        try:
            return int(value[field])
        except Exception:
            continue
    return _int_of(value)


def _type_name(value):
    try:
        return str(value.type)
    except Exception:
        return None


def _register(frame, name):
    try:
        return int(frame.read_register(name))
    except Exception:
        return None


def _symbolize(address):
    """Name the code an address belongs to, which is how a return address becomes a caller."""
    if not address:
        return None
    try:
        text = gdb.execute(f"info symbol {address}", to_string=True)
    except Exception:
        return None
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line or None


def _map_state():
    """Read the handler map's own bookkeeping from the global that owns it.

    `TMap` keeps its elements in `Pairs.Elements`, a sparse array, so the slot count, the free
    count and the allocation bit count together say whether the map still holds the registration
    that failed to unregister. Element keys are deliberately not enumerated: that needs pointer
    arithmetic over three nested container layouts, and an empty-versus-populated answer is the
    part that decides between "already torn down" and "wrong id".
    """
    state = {"expression": MAP_EXPRESSION}
    try:
        value = gdb.parse_and_eval(MAP_EXPRESSION)
        elements = value["Pairs"]["Elements"]
        state["data_slots"] = _int_of(elements["Data"]["ArrayNum"])
        state["num_free_indices"] = _int_of(elements["NumFreeIndices"])
        state["allocation_bits"] = _int_of(elements["AllocationFlags"]["NumBits"])
        if state["data_slots"] is not None and state["num_free_indices"] is not None:
            state["handlers"] = state["data_slots"] - state["num_free_indices"]
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
    return state


def _select(frame):
    try:
        frame.select()
        return True
    except Exception:
        return False


class Capture:
    def __init__(self):
        self.path = Path(_capture_dir(), "shutdown-capture.json")
        self.started = False
        self.breakpoints = []
        self.check_events = []
        self.socket_events = []
        self.watch_events = []
        self.stop_events = []
        self.errors = []
        self.truncated = False
        # One counter for every kind of observation. Each list has its own `seq`, which cannot
        # order a map write against a failed check, and the order of those two is the whole
        # question: a destroyed map explains the failed check, while the reverse does not.
        self.sequence = 0
        self.current_order = 0
        self.inferior_pid = None
        self.exit_code = None
        self.exit_signal = None
        self.finished = False
        self._write_failures = 0

    # -- bookkeeping ------------------------------------------------------------------
    def record_error(self, where, exc):
        self.errors.append({"where": where, "error": f"{type(exc).__name__}: {exc}"})

    def note(self, where, exc):
        self.record_error(where, exc)
        self.write()

    def write(self):
        """Written after every observation: gdb can be SIGKILLed by our own watchdog, and
        losing the backtrace because cleanup was late would be the worst outcome."""
        document = self.document()
        try:
            temporary = Path(str(self.path) + ".tmp")
            temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
            os.replace(temporary, self.path)
        except Exception:
            self._write_failures += 1

    def finish(self):
        """Final pass after the run: re-read what the breakpoints actually did."""
        _refresh(self)
        self.finished = True
        self.write()

    def document(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": SCOPE,
            "target": {
                "expr": TARGET_EXPR,
                "file": TARGET_FILE,
                "source_line": TARGET_SOURCE_LINE,
            },
            "inferior_pid": self.inferior_pid,
            "exit_code": self.exit_code,
            "exit_signal": self.exit_signal,
            "capture_complete": self.finished,
            "breakpoints": self.breakpoints,
            "check_events": self.check_events,
            "socket_events": self.socket_events,
            "watch_events": self.watch_events,
            "stop_events": self.stop_events,
            "truncated": self.truncated,
            "capture_errors": self.errors,
            "write_failures": self._write_failures,
        }

    # -- observation ------------------------------------------------------------------
    def stack(self):
        frames = []
        frame = gdb.newest_frame()
        depth = 0
        while frame is not None and depth < MAX_FRAMES:
            entry = {
                "index": depth,
                "function": None,
                "address": None,
                "file": None,
                "line": None,
            }
            try:
                entry["function"] = frame.name()
            except Exception:
                pass
            try:
                entry["address"] = hex(int(frame.pc()))
            except Exception:
                pass
            try:
                sal = frame.find_sal()
                if sal is not None and sal.symtab is not None:
                    entry["file"] = sal.symtab.filename
                    entry["line"] = int(sal.line)
            except Exception:
                pass
            frames.append(entry)
            try:
                frame = frame.older()
            except Exception:
                break
            depth += 1
        return frames

    def scan_handles(self, frames):
        """Walk ancestors looking for the id and socket that the failed check is about.

        Reading a local by name is used instead of evaluating `MessageHandlers.Contains(...)`
        because an inferior call during shutdown is itself a shutdown re-entrancy risk, and
        because the TMap internals of this engine version are not a stable interface.
        """
        original = gdb.selected_frame()
        found = {}
        try:
            frame = gdb.newest_frame()
            index = 0
            while frame is not None and index < MAX_SCAN_FRAMES:
                entry = {}
                try:
                    message_id = _handle_index(frame.read_var("MessageId"))
                    if message_id is not None:
                        entry["message_id"] = message_id
                except Exception:
                    pass
                try:
                    this = frame.read_var("this")
                    entry["this_address"] = hex(int(this))
                    entry["this_type"] = _type_name(this)
                    try:
                        entry["this_message_id"] = _handle_index(this.dereference()["MessageId"])
                    except Exception:
                        pass
                except Exception:
                    pass
                if entry:
                    entry["index"] = index
                    entry["function"] = frames[index]["function"] if index < len(frames) else None
                    found.setdefault("frames", []).append(entry)
                    if "message_id" in entry and "failing_message_id" not in found:
                        found["failing_message_id"] = entry["message_id"]
                        found["failing_message_id_frame"] = index
                frame = frame.older()
                index += 1
        except Exception as exc:
            self.record_error("scan", exc)
        finally:
            try:
                original.select()
            except Exception:
                pass
        return found

    def observe_check(self, spec):
        frame = gdb.newest_frame()
        expr = _cstring(frame, "Expr")
        source_file = _cstring(frame, "File")
        line = None
        try:
            line = _int_of(frame.read_var("Line"))
        except Exception:
            pass
        event = {
            "seq": len(self.check_events),
            "order": self.current_order,
            "spec": spec,
            "expr": expr,
            "file": source_file,
            "line": line,
            "target": bool(
                expr
                and source_file
                and TARGET_FILE in source_file
                and re.sub(r"\s+", "", expr) == TARGET_EXPR_NORMALIZED
            ),
            "line_matches_source": line == TARGET_SOURCE_LINE,
        }
        if len(self.check_events) >= MAX_EVENTS:
            self.truncated = True
            return event
        if event["target"]:
            frames = self.stack()
            event["stack"] = frames
            event["handles"] = self.scan_handles(frames)
            event["handlers"] = _map_state()
            try:
                gdb.execute("set print frame-arguments scalars")
                event["backtrace"] = gdb.execute(
                    "bt -frame-info location-and-address", to_string=True
                )
            except Exception as exc:
                self.record_error("backtrace", exc)
            try:
                event["backtrace_full"] = gdb.execute("bt -full 3", to_string=True)
            except Exception as exc:
                self.record_error("backtrace_full", exc)
        self.check_events.append(event)
        return event

    def observe_socket(self, role, spec):
        frame = gdb.newest_frame()
        event = {"seq": len(self.socket_events), "role": role, "spec": spec}
        event["order"] = self.current_order
        # The map's size at this moment makes the timeline self-describing: it shows the table
        # going 1 -> 0 once and staying empty, with no separate cross-referencing required.
        event["handlers"] = _map_state()
        # ABI registers first: `this` is x0 and the first argument is x1, which is the truth the
        # debugger cannot misplace. The DWARF reads are kept alongside so a disagreement between
        # the two is visible in the capture rather than resolved silently.
        event["abi_x0"] = _register(frame, "x0")
        event["abi_x1"] = _register(frame, "x1")
        # x30 holds the return address the callee was entered with. Reading it is the fallback
        # for the frames gdb's unwinder cannot reach, and it is recorded for the release events
        # because their caller is exactly what is missing there.
        event["abi_x30"] = _register(frame, "x30")
        if role.startswith("release-"):
            event["caller"] = _symbolize(event["abi_x30"])
        if role == "remove" and event["abi_x1"] is not None:
            event["abi_message_id"] = event["abi_x1"] & 0xFFFFFFFF
        if role == "reset" and event["abi_x0"] is not None:
            try:
                event["abi_this_message_id"] = _int_of(
                    gdb.parse_and_eval(
                        "((GPUMessage::FSocket*){} )->MessageId.Index".format(event["abi_x0"])
                    )
                )
            except Exception as exc:
                event["abi_this_message_id_error"] = f"{type(exc).__name__}: {exc}"
        try:
            event["message_id"] = _handle_index(frame.read_var("MessageId"))
        except Exception:
            pass
        try:
            this = frame.read_var("this")
            event["this_address"] = hex(int(this))
            event["this_type"] = _type_name(this)
            try:
                event["this_message_id"] = _handle_index(this.dereference()["MessageId"])
            except Exception:
                pass
            try:
                # RegisterHandler assigns `NextMessageId` and the id it is about to take is
                # the one the ledger needs.
                event["next_message_id"] = _int_of(this.dereference()["NextMessageId"])
            except Exception:
                pass
        except Exception:
            pass
        if len(self.socket_events) < MAX_EVENTS:
            self.socket_events.append(event)
        else:
            self.truncated = True
        if role.startswith("release-"):
            # Who calls ReleaseRHI decides whether the two releases belong to the same teardown
            # phase, which is the fact a lifetime-based fix would have to be built on.
            frames = self.stack()
            event["stack"] = frames[:WATCH_BACKTRACE_FRAMES]
            try:
                event["backtrace"] = gdb.execute(
                    f"bt -frame-info location-and-address {WATCH_BACKTRACE_FRAMES}", to_string=True
                )
            except Exception as exc:
                self.record_error("release-backtrace", exc)
        return event

    def observe_watch(self, spec):
        """Record a write to the map's element count together with the stack that wrote it.

        The stack is the whole point: it names the teardown path that empties the table, which
        the failed `check` alone cannot do.
        """
        event = {"seq": len(self.watch_events), "spec": spec, "expression": WATCH_EXPRESSION}
        event["order"] = self.current_order
        try:
            event["value"] = _int_of(gdb.parse_and_eval(WATCH_EXPRESSION))
        except Exception as exc:
            event["value_error"] = f"{type(exc).__name__}: {exc}"
        event["handlers"] = _map_state()
        frames = self.stack()
        event["stack"] = frames[:WATCH_BACKTRACE_FRAMES]
        try:
            event["backtrace"] = gdb.execute(
                f"bt -frame-info location-and-address {WATCH_BACKTRACE_FRAMES}", to_string=True
            )
        except Exception as exc:
            self.record_error("watch-backtrace", exc)
        if len(self.watch_events) < MAX_WATCH_EVENTS:
            self.watch_events.append(event)
        else:
            self.truncated = True
        return event

    def observe(self, role, spec):
        self.sequence += 1
        self.current_order = self.sequence
        if self.inferior_pid is None:
            try:
                self.inferior_pid = gdb.selected_inferior().pid
            except Exception:
                pass
        if role == "check":
            self.observe_check(spec)
        elif role == WATCH_ROLE:
            self.observe_watch(spec)
        else:
            self.observe_socket(role, spec)
        self.write()


class Hook(gdb.Breakpoint):
    def __init__(self, capture, role, spec, kind=gdb.BP_BREAKPOINT):
        super().__init__(spec, type=kind, wp_class=gdb.WP_WRITE)
        self.capture = capture
        self.role = role
        self.spec = spec
        self.kind = kind
        self.hits = 0
        # The hooks never stop the inferior: everything needed is read here, and leaving the
        # run in gdb's hands would replace the shutdown we are studying with a
        # debugger-controlled one.
        self.silent = True

    def resolved(self):
        """gdb 12.1 exposes `pending` but not `locations`; a breakpoint created after the
        executable and its symbols are loaded is either bound now or never.

        A watchpoint is different: it is armed on an address rather than bound to code, so
        there is no binding to read back and its resolution is only proven when it fires.
        """
        if self.kind != gdb.BP_BREAKPOINT:
            return None
        try:
            return not self.pending
        except Exception:
            return False

    def stop(self):
        # The counter kept here is the one recorded, not gdb's `hit_count`: on this gdb 12.1
        # `hit_count` read 0 for breakpoints that demonstrably fired (four roles, five
        # observations), so recording it would understate the instrumentation.
        self.hits += 1
        try:
            self.capture.observe(self.role, self.spec)
        except Exception as exc:
            self.capture.note(f"observe:{self.role}", exc)
        return False


def _install(capture):
    for role, required, candidates in BREAKPOINT_ROLES:
        record = {"role": role, "required": required, "hits": 0, "resolved": False}
        hook = None
        for spec in candidates:
            attempt = None
            try:
                attempt = Hook(capture, role, spec)
            except Exception as exc:
                capture.record_error(f"breakpoint:{spec}", exc)
                continue
            # The executable and its symbol file are already loaded when this runs, so a spec
            # that does not resolve here is a spec that will never fire. Dropping it and
            # moving to the next candidate is what makes the fallback real rather than
            # cosmetic: an unresolved breakpoint left in place would look identical to an
            # argument that never happens.
            if attempt.resolved():
                hook = attempt
                record["spec"] = spec
                record["attempts"] = candidates.index(spec) + 1
                record["resolved"] = True
                break
            if role in PENDING_ROLES:
                # Kept on purpose: it binds when the library loads, and the post-run refresh is
                # what reports whether that ever happened.
                hook = attempt
                record["spec"] = spec
                record["attempts"] = candidates.index(spec) + 1
                record["resolved"] = None
                record["resolved_at_creation"] = False
                capture.errors.append(
                    {
                        "where": f"breakpoint:{spec}",
                        "error": "pending at creation; kept because its symbol is in a shared "
                        "library",
                    }
                )
                break
            capture.errors.append(
                {"where": f"breakpoint:{spec}", "error": "did not resolve; trying next candidate"}
            )
            try:
                attempt.delete()
            except Exception as exc:
                capture.record_error(f"breakpoint-delete:{spec}", exc)
        if hook is None:
            record["spec"] = None
            capture.breakpoints.append(record)
            capture.errors.append(
                {"where": f"breakpoint:{role}", "error": "no candidate spec could be resolved"}
            )
            continue
        hook.record = record
        _HOOKS.append(hook)
        capture.breakpoints.append(record)
    _install_watch(capture)


def _install_watch(capture):
    """Arm a write watchpoint on the map's element count.

    Optional by design: it is a hardware watchpoint on an address, so there is no binding to
    verify up front and a failure to arm must not be mistaken for a map that was never
    emptied. Whether it fired is recorded, and the analyzer refuses to claim anything about
    the destroyer when it did not.
    """
    record = {"role": WATCH_ROLE, "required": False, "spec": WATCH_EXPRESSION, "hits": 0}
    try:
        hook = Hook(capture, WATCH_ROLE, WATCH_EXPRESSION, kind=gdb.BP_WATCHPOINT)
    except Exception as exc:
        record["resolved"] = None
        capture.record_error(f"watchpoint:{WATCH_EXPRESSION}", exc)
        capture.breakpoints.append(record)
        return
    record["resolved"] = None
    hook.record = record
    _HOOKS.append(hook)
    capture.breakpoints.append(record)


def _refresh(capture):
    """Re-read resolution and hit counts after the run. A breakpoint created before `run`
    may only bind once the executable and its symbol file are loaded, so this second read is
    what turns "a breakpoint was requested" into "the breakpoint was armed, and fired N
    times". Without it an unresolved hook and an unhit hook look identical."""
    for hook in _HOOKS:
        if not hasattr(hook, "record"):
            continue
        hook.record["hits"] = hook.hits
        resolved = hook.resolved()
        # None means "only a fired watchpoint proves it was armed"; keep the recorded
        # resolution unknown in that case rather than inventing a pass.
        hook.record["resolved"] = bool(hook.hits) if resolved is None else resolved


_HOOKS = []
state = None


def start():
    global state
    state = Capture()
    _install(state)
    state.started = True

    def on_stop(event):
        try:
            if not isinstance(event, gdb.SignalEvent):
                return
            if len(state.stop_events) >= MAX_STOP_EVENTS:
                return
            entry = {"signal": event.stop_signal}
            try:
                frame = gdb.newest_frame()
                entry["function"] = frame.name()
                entry["address"] = hex(int(frame.pc()))
            except Exception:
                pass
            state.stop_events.append(entry)
            state.write()
        except Exception as exc:
            state.note("stop-event", exc)

    def on_exited(event):
        try:
            state.exit_code = event.exit_code
            try:
                state.exit_signal = getattr(event, "exit_signal", None)
            except Exception:
                pass
            try:
                state.inferior_pid = event.inferior.pid
            except Exception:
                pass
        except Exception as exc:
            state.note("exited-event", exc)
        state.write()

    gdb.events.stop.connect(on_stop)
    gdb.events.exited.connect(on_exited)
    state.write()


def finish():
    """Module-level convenience so either `state.finish()` or `finish()` finalizes."""
    if state is not None:
        state.finish()
