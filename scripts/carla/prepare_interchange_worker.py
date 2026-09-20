#!/usr/bin/env python3
"""Replay explicit static Worker changes; no default SDK/thread-pool removal."""
import argparse
import difflib
import json
from pathlib import Path
import tempfile
from prepare_interchange_parser import sha, RULE_INSERT

WORKER = Path("Engine/Source/Programs/InterchangeWorker")
BEGIN = "#if !CARLA_INTERCHANGE_UFBX_STATIC\n"
END = "\n#endif // !CARLA_INTERCHANGE_UFBX_STATIC\n"


def replace_once(text, before, after):
    if after in text:
        return text
    if text.count(before) != 1:
        raise ValueError("conflicting Worker source anchor: " + before[:80])
    return text.replace(before, after, 1)


def prepare(ue, artifact_root):
    ue, artifact_root = ue.resolve(), artifact_root.resolve()
    if artifact_root.is_relative_to(ue):
        raise ValueError("artifacts must be outside UE")
    edits = {}
    for relative in ("InterchangeWorker.Build.cs", "Private/InterchangeWorker.cpp", "Private/InterchangeWorkerImpl.cpp"):
        path = WORKER / relative
        if (ue / path).is_symlink():
            raise ValueError("source symlink: " + str(path))
        before = (ue / path).read_bytes()
        text = before.decode("utf-8")
        if relative.endswith(".Build.cs"):
            marker = "\t\tAddEngineThirdPartyPrivateStaticDependencies(Target,"
            insert = RULE_INSERT.replace("\t\t\t", "\t\t")
            text = replace_once(text, marker, insert + marker)
        elif relative.endswith("Impl.cpp"):
            if not (text.startswith(BEGIN) and text.endswith(END)):
                if "CARLA_INTERCHANGE_UFBX_STATIC" in text:
                    raise ValueError("partial WorkerImpl guard")
                text = BEGIN + text + END
        else:
            text = replace_once(text, '\tUE_SET_LOG_VERBOSITY(LogInterchangeWorker, Verbose);', '''\tUE_SET_LOG_VERBOSITY(LogInterchangeWorker, Verbose);
#if CARLA_INTERCHANGE_UFBX_STATIC
    if (!FModuleManager::Get().LoadModule(TEXT("CarlaUfbxInterchange"))) return EXIT_FAILURE;
#endif''')
            text = replace_once(text, '\tFString WorkerVersionError;', '''\tFString WorkerVersionError;
#if CARLA_INTERCHANGE_UFBX_STATIC
    TArray<FString> Parts;
    InterchangeDispatcherVersion.ParseIntoArray(Parts, TEXT("."), false);
    bool ValidVersion = Parts.Num() == 4;
    for (int32 Index = 0; Index < Parts.Num(); ++Index)
    {
        ValidVersion &= !Parts[Index].IsEmpty() && Parts[Index].Len() <= 10;
        for (TCHAR C : Parts[Index]) ValidVersion &= C >= TCHAR(48) && C <= TCHAR(57);
        const uint64 Number = FCString::Strtoui64(*Parts[Index], nullptr, 10);
        ValidVersion &= Number <= (Index == 3 ? 1u : uint64(MAX_int32));
    }
    if (!ValidVersion) WorkerVersionError = TEXT("Malformed dispatcher version");
#endif''')
            text = replace_once(text, '\t\tWorker.Run(WorkerVersionError);', '''#if CARLA_INTERCHANGE_UFBX_STATIC
        if (!Worker.Run(WorkerVersionError)) return EXIT_FAILURE;
#else
\t\tWorker.Run(WorkerVersionError);
#endif''')
            text = replace_once(text, '\tGEngineLoop.PreInit(ArgC, ArgV);', '''#if CARLA_INTERCHANGE_UFBX_STATIC
    const int32 InitResult = GEngineLoop.PreInit(ArgC, ArgV);
    if (InitResult != 0) return InitResult;
#else
\tGEngineLoop.PreInit(ArgC, ArgV);
#endif''')
        edits[path] = (before, text.encode("utf-8"))
    artifact_root.mkdir(parents=True, exist_ok=True)
    receipt_file = artifact_root / "worker-deployment.json"
    receipt = json.loads(receipt_file.read_text()) if receipt_file.exists() else {}
    path = WORKER / "Private/InterchangeWorkerStatic.cpp"
    target = ue / path
    if target.is_symlink():
        raise ValueError("generated source symlink")
    before = target.read_bytes() if target.exists() else None
    after = (Path(__file__).parent / "ue-parser/InterchangeWorkerStatic.cpp").read_bytes()
    if before is not None and before != after and receipt.get(str(path)) != sha(before):
        raise ValueError("local Worker source changes would be overwritten")
    edits[path] = (before, after)
    run = Path(tempfile.mkdtemp(prefix="worker-prepare-", dir=artifact_root))
    run.chmod(0o755)
    patch = []
    for relative, (before, after) in edits.items():
        for label, content in (("before", before), ("after", after)):
            if content is None: continue
            output = run / label / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        patch.extend(difflib.unified_diff((before or b"").decode().splitlines(True), after.decode().splitlines(True),
            fromfile="a/" + str(relative) if before is not None else "/dev/null", tofile="b/" + str(relative)))
    (run / "changes.patch").write_text("".join(patch))
    for relative, (before, after) in edits.items():
        current = (ue / relative).read_bytes() if (ue / relative).exists() else None
        if current != before: raise ValueError("source changed during Worker preparation")
        if before != after: (ue / relative).write_bytes(after)
    receipt_file.write_text(json.dumps({str(path): sha(edits[path][1])}, indent=2) + "\n")
    (run / "deployment.json").write_text(json.dumps({"kind": "worker_source_not_runtime_evidence",
        "engine_files": {str(p): sha(after) for p, (_, after) in edits.items()},
        "changes": sum(a != b for a, b in edits.values()), "default_path_preserved": True}, indent=2) + "\n")
    return run


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ue-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(prepare(args.ue_root, args.artifact_root))
    except (OSError, ValueError) as error:
        parser.exit(1, f"FAIL: {error}\n")
