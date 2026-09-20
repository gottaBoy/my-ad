# Native Static Worker IPC

```sh
make carla-interchange-worker JOBS=4
```

This builds an ARM64 Program target named `CarlaInterchangeWorker` using the
actual engine `InterchangeWorker` launch module. A separate UE probe starts the
real executable and uses engine `FNetworkServerNode`, `FCommandQueue` and task
commands over loopback TCP. No simulated Worker or Python protocol replacement
is used. It does not use the production `FInterchangeWorkerHandler` launcher.

## Integration And Default Behavior

`CarlaUfbxInterchange` now owns the graph implementation and ufbx session provider.
The probe and Worker use the same implementation. The Worker explicitly loads
this linked module after successful PreInit and before creating parser sessions;
the module registers its factory through the engine-owned modular interface.
Parser instances are destroyed before module shutdown/unregistration.

`prepare_interchange_worker.py` retains before/after sources, patches, deployment
hashes and a receipt under `artifacts/carla/worker-source/`. Preparation is
idempotent and rejects conflicting local generated-source edits. It changes the
actual Worker Build.cs, entry point and implementation only behind
`CARLA_INTERCHANGE_UFBX_STATIC=1`, with Program/Linux/ARM64/no-Engine restrictions.
The default Autodesk dependency and original threaded implementation remain.

In this opt-in static profile:

- PreInit, module loading and Worker.Run failures propagate as nonzero exits.
- Version arguments require four bounded numeric fields and a binary LWC flag;
  then the engine's major/minor/LWC compatibility check applies.
- Each load/fetch/result/messages/completion transaction runs serially on the
  Worker main thread. Three queued success/failure/success tasks keep their IDs
  and diagnostics isolated, including a reload failure that invalidates state.
- Parser or unknown-command errors return ProcessFailed and messages, with no
  successful ResultFile. Expected task failures do not kill the Worker.
- Normal Terminate exits zero; protocol/version/connection/peer failures do not.
  Idle timeout is bounded (0.5-30 seconds, default 10). This also covers clean FIN
  cases where the engine network implementation does not expose an error flag.
- The peer has per-response/exit deadlines and kills/reaps on failure. Forced
  cleanup is never accepted as a normal termination. The runner additionally
  bounds the entire probe to 150 seconds because engine sends can block.

This profile does not implement production progress queries or long-idle
WorkerHandler behavior; unsupported protocol commands return errors. It still
supports only the verified static FBX conversion settings and features described
in `carla-native-parser.md`.

## Evidence

`artifacts/carla/interchange-worker-20260916T052252Z-EbPC7I/stage-report.json`
passed native build/link/run validation:

- 23 protocol checks, five distinct child PIDs, both executables AArch64 ELF.
- Two node graphs and six transform-specific mesh files read by the peer.
- Valid handshake and six mesh requests; queued request isolation; malformed,
  unknown translator, animation and conversion-policy rejection; reload recovery.
- Incompatible and malformed versions return Error plus exit 1 without assets.
- Lost peer and a reserved non-listening port produce exit 1; normal Terminate
  exits 0. The refusal test does not race another service for a freed port.
- No libfbxsdk or unresolved dynamic symbols. Engine parser/Worker source snapshots,
  commands, source hashes, raw child logs, return status and ufbx prerequisite
  are retained and revalidated.

The 31-check serial parser and prior 20-scene/27-payload static gates were also
rerun after moving the provider. This is not full FBX, Editor, Cook, CARLA RPC or
GPU sensor acceptance.
