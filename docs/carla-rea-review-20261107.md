# DGX Spark ARM64 CARLA: REA Review

Review date: 2026-11-07, Asia/Singapore.

This records the earlier review pass. A later same-day REA investigation added
live TLS fault evidence and a preload comparison ending in device loss; see
`docs/carla-vulkan-bridge-rea-20261107.md` for that subsequent work.

## Conclusion

Native ARM64 bring-up has progressed well beyond compilation: there are cooked
packages, real RPC/tick evidence, and a Town01 software-rendered sensor soak.
The requested GB10/Town10 GPU product is still unaccepted. The latest local
GPU investigation artifacts are dated 2026-10-07, not today.

Today's work inspected sources and historical artifacts, checked the current
GPU and binary identities, ran local tests, and used REA for static inspection.
It did not launch CARLA, reproduce a GPU crash, rebuild UE, apply the shutdown
proposal, change drivers, or change existing product/diagnostic code.

## Measured Progress

| Layer | Evidence | Boundary |
| --- | --- | --- |
| Current host | `nvidia-smi`: GB10 / 580.173.02 | Device discovery, not container Vulkan or CARLA acceptance |
| ARM64 toolchain, LibCarla, Python API, UE/Game | Existing build artifacts and native AArch64 staged executable | Existing builds, not a fresh full rebuild today |
| Rendering full Cook | Audit 9.41: 44,142 cooked packages, 330 maps, 87 streaming sublevels, 32 sensor materials; 9.94 records the SM6 cache recipe | Lavapipe-assisted build, not GB10 runtime |
| Town01 software rendering | `runtime-sensors-20260925T064316Z-7IHCrE`: 6,000 samples, all 12 endpoint checks PASS, no endpoint errors | Lavapipe, historical stage, known UE diagnostics; not Town10 or GPU performance |
| Town10 without rendering | `town10-nullrhi-rpc-20261003T043541Z-Sr2HXf`: endpoint PASS, 20 ticks | Shutdown crashed, stop 139; no RGB/Vulkan acceptance |
| Town10 on GB10 | `town10-gb10-rpc-20261005T143823Z-BBzto1`: startup FAIL, shader-compiler SIGSEGV, stop 139 | No reliable product world/sensors/soak acceptance |
| Latest GPU investigation | `gb10-device-fault-20261007T141134Z-EhSbfe`: graphics 618/618 returned, compute 4/3 returned | Fault attribution and task context, not a root-cause fix |
| ROS/Autoware | No end-to-end acceptance established in this review | Remains downstream of simulator acceptance |

The Town01 sensor evidence is in
`artifacts/carla/runtime-sensors-20260925T064316Z-7IHCrE/endpoint/runtime-checks-ayq0rrhj/runtime-result.json`.
The other run directories above are under `artifacts/carla/`.

## REA Execution

REA means Reverse Engineer Anything here. Repository progress was checked with
normal source tools; the closed-source NVIDIA library was the reverse-engineering
target.

- Used version-pinned `npx -y rea-agents@6.8.0`, without running setup or changing
  agent configuration.
- `doctor --client codex --json` reports an unsupported Node version
  (24.21.0; this REA release requires a newer supported patch), no Codex MCP
  registration, and no configured deep native analysis engine. This is not a
  healthy full REA installation.
- Despite that readiness result, the narrow `inspect-with-objdump`,
  `inspect-elf-notes`, and `capture-process` CLI operations completed. These
  observations do not establish general compatibility on this Node version.
- The GDB CLI route did not produce evidence: one request lacked the required
  TTY, and the corrected request's multi-action Python command was rejected by
  its output-route contract. No live process was attached or executed.
- The successful process capture ran only `/usr/bin/objdump` on the selected
  library, with a bounded address range. It did not execute NVIDIA/CARLA code.

Retained evidence under `artifacts/rea-review-20261107/`:

| File | REA Evidence ID |
| --- | --- |
| `driver-file-headers.json` | `ev_4a0bf0c6a38a72d9e6d254f10e53b8ba3def31074146449e946ce4c7e70b5da5` |
| `driver-disassembly.capture.json` | `ev_3d4e1bc202d1d31a956861d4135e0667a4bfa0a40791fa527bc986263d4f69f6` |
| `driver-disassembly.scenario.json` | Reusable input for the second observation |

The capture exited 0, retained untruncated raw output, and recorded the selected
driver file unchanged before/after. Overall settlement is `unverifiable` because
ownership of two unrelated processes could not be read; REA left those processes
untouched. This does not invalidate the recorded static output, but must not be
reported as complete process-environment coverage.

## New Finding: A TLS Access at the Recorded Crash Offset

Current on-disk library:

```text
/usr/lib/aarch64-linux-gnu/libnvidia-glvkspirv.so.580.173.02
SHA256 b63396177492fc33c9081a03445d6bb26ec5cbe6c321c6544f13e812f94d284e
BuildID 464cd5827d2ebff8a8f5074f6c27cffd5f5f4a6d
```

The 2026-10-05 server log records an access to address `0x8` at library offset
`0x34d610`. The current same-version library has this instruction sequence:

```asm
0x34d5e4: mrs x3, tpidr_el0
0x34d604: ldr x6, [x0, #1432]
0x34d608: add x0, x0, #0x598
0x34d60c: blr x6
0x34d610: str x5, [x3, x0]
```

`readelf -rW` independently reports:

```text
0000000000846598  R_AARCH64_TLSDESC  8
00000000008465a8  R_AARCH64_TLSDESC  0
0000000000846588  R_AARCH64_TLSDESC  10
```

**Observation:** the current binary's instruction at the recorded crash offset
is a store following a TLS descriptor resolver call, using a thread-pointer
value and resolver result. The first descriptor has addend 8.

**Inference:** driver/loader dynamic TLS resolution and per-thread TLS state
deserve priority over another broad shader/cache parameter sweep. A missing or
corrupted TLS base is a candidate consistent with a small fault address, not a
demonstrated root cause.

**Unknown:** historical captures do not provide the fault-time `x0/x3/x6`,
descriptor contents, or readable `tpidr_el0`. Exact historical loaded-library
identity must also be bound to a digest/build-id before treating today's static
bytes as verified historical runtime bytes. Neither a glibc defect nor a driver
defect, UE corruption, or an application-side workaround is established.

## Evidence Boundaries

1. The latest fault marker and GDB stop agree on LWP 116, task pointer
   `0xe16bdc010710`, tag `0x40000010`, and `GraphTask`. This is strong correlation.
   However, the three successful `ERhiThread` compute calls use different shaders
   from the failing fourth call. They are not a controlled thread-only experiment.
2. The 2026-10-06 worker replay uses `main_00000290_c1eca148` from an older
   capture, whereas the latest fault uses `main_0000142c_a6b37050`.
   Its PASS cannot substitute for an exact replay of the latest fault, and its
   result explicitly says `ue_exact_replay=false`.
3. The latest archived `driver-entry-analysis.json` lacks the task-context
   fields now supported by the analyzer. Re-running the current analyzer
   read-only against that run returns the expected LWP/task association.
   The raw marker and GDB context support the conclusion; the archived derived
   JSON alone does not contain all of it.
4. The vendor bundle's 37 FAIL / 2 PASS includes device-creation/environment
   failures, not 37 proven identical compiler crashes. Validation was 2/10
   PASS and disabled runs 0/29 PASS; this is not a reliable workaround.
5. The shutdown lifecycle defect is independent of startup compilation.
   Its guard remains an unapplied proposal, not a verified fix.

## Current Regression and Reproducibility

Today's `make test-local`: **840 tests, 56 skipped, 1 error**. The single error
is `test_arm64_rtti_helper_abi_contract`, which hardcodes `docker exec
carla-build-session`; Docker reports that container as `exited`. This is an
environment-coupled test failure, not evidence of an RTTI ABI regression.
All shell scripts separately pass `bash -n`. The historical 840/0/56 result
must not be presented as today's result.

Current nested repositories:

- CARLA `f6cbc59`, tracked working tree clean.
- UE `5502950e1`, one existing tracked change in `VulkanPipeline.cpp`.
- Formal staged executable SHA256 `a53f37d73cf97c87c70e020b4e4327eed4e1d2b8ea333a3e31cb694ac32bf64e`.
- Diagnostic executable SHA256 `c9defcb3ee951ef3e7da09230f030921f8c1cfc99cea0a073633b9c72d99a3e8`.

The root repository also has existing uncommitted diagnostics and documentation.
Do not mix the diagnostic binary with the formal-stage provenance or describe
the current whole workspace as frozen/clean. Older execution-plan and audit
sections still contain superseded build/runtime blockers; date and gate scope
must accompany any progress summary.

## Next Experiments

1. Bind the actually loaded library and loader to file SHA256/build-id, then
   capture fault registers `x0..x6`, PC, signal address, mappings, and bounded
   TLS-descriptor words. At this instruction, fault-time `x3` is especially
   useful even when GDB cannot expose `tpidr_el0` directly. Use read-only
   debugger reads, not calls into the inferior.
2. If the loaded instruction/relocation matches, compare TLS resolver target,
   result and per-thread state against a successful call. Distinguish a bad
   resolver result from register corruption or an invalid TLS allocation.
3. Use the same shader/layout/cache/device inputs for any thread/load-order
   experiment. Change only one factor at a time; preserve prior calls and
   object lifetimes as far as measured. Do not change task labels to simulate
   an RHI thread or repeat already-negative global-lock experiments.
4. Only after a causally supported change, repeat undebugged GB10 Town10 world,
   synchronous ticks, valid/aligned/changing RGB and LiDAR, and sustained runs.
   Validate shutdown independently. Preserve the no-driver-change constraint.

The practical milestone is a stable failing TLS/context reproduction or a
validated workaround, not another increase in diagnostic test count.
