set pagination off
set confirm off
set breakpoint pending on
set disable-randomization off
set print frame-arguments scalars

# The failing check calls PLATFORM_BREAK() when a debugger is attached
# (AssertionMacros.cpp: IsDebuggerPresent() -> return true -> PLATFORM_BREAK), so SIGTRAP is
# expected and must not be treated as the fault. The first-现场 evidence comes from the
# breakpoint on FDebug::CheckVerifyFailedImpl2 instead, which is FORCENOINLINE and therefore
# fires for every failed check.
handle SIGTRAP nostop noprint nopass
# The probe delivers shutdown with SIGTERM to the inferior; gdb must hand it to UE's own
# handler rather than swallowing it. This is why the signal is sent by pid and not through
# the debugger.
handle SIGTERM nostop noprint pass
# A fatal signal still stops gdb: it is recorded and reported rather than passed on.
handle SIGSEGV stop print nopass
handle SIGABRT stop print nopass

python
import os
import sys
# gdb's own messages are written to their own file so that server.log holds the engine log
# alone; the analyzer reads LogExit from one and the inferior exit status from the other.
gdb.execute("set logging file {}".format(os.environ["CARLA_UE_SHUTDOWN_GDB_LOG"]))
gdb.execute("set logging overwrite on")
gdb.execute("set logging redirect on")
gdb.execute("set logging enabled on")
sys.path.insert(0, os.environ["CARLA_GDB_SCRIPTS"])
import gdb_ue_shutdown_capture
gdb_ue_shutdown_capture.start()
end
