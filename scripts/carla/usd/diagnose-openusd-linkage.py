#!/usr/bin/env python3
"""Relink copied failed OpenUSD outputs to test libc++ visibility; diagnostic only."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run", type=Path, required=True)
args = parser.parse_args()
source = args.run.resolve()
diag = Path(tempfile.mkdtemp(prefix="openusd-link-diagnostic-", dir=source.parent))
diag.chmod(0o755)
prefix = diag / "install"
shutil.copytree(source / "install", prefix, symlinks=True)
mapping = {}
for file in prefix.rglob("*.so"):
    if file.name in mapping:
        raise ValueError("ambiguous installed DSO basename")
    mapping[file.name] = file
commands = subprocess.check_output(["ninja", "-t", "commands"], cwd=source / "work", text=True)
retained = []
for line in commands.splitlines():
    tokens = shlex.split(line)
    if "-shared" not in tokens or "-o" not in tokens or tokens[:2] != [":", "&&"]:
        continue
    if tokens[-2:] != ["&&", ":"]:
        raise ValueError("unexpected link-command suffix")
    tokens = tokens[2:-2]
    original = tokens[tokens.index("-o") + 1]
    name = Path(original).name
    if name not in mapping:
        raise ValueError("unmapped shared output: " + original)
    output = mapping[name]
    tokens[tokens.index("-o") + 1] = str(output)
    tokens = [t for t in tokens if t != "-Wl,--exclude-libs,ALL"]
    for i, token in enumerate(tokens):
        if token.startswith("-Wl,-rpath,"):
            parts = token.removeprefix("-Wl,-rpath,").split(":")
            external = [p for p in parts if p and not p.startswith(str(source / "work"))]
            tokens[i] = "-Wl,-rpath," + ":".join([str(prefix / "lib"), *external])
    before = hashlib.sha256(output.read_bytes()).hexdigest()
    command_file = diag / (name + ".command.json")
    command_file.write_text(json.dumps(tokens, indent=2) + "\n")
    with (diag / (name + ".log")).open("wb") as log:
        result = subprocess.run(tokens, cwd=source / "work", stdout=log, stderr=subprocess.STDOUT, timeout=180)
    retained.append({"source_output": original, "diagnostic_output": str(output),
                     "input_sha256": before, "exit_code": result.returncode})
    if result.returncode:
        raise RuntimeError("diagnostic link failed: " + name)
deps = json.loads((source / "dependencies.json").read_text())
python = Path(deps["python"]["prefix"]) / "bin/python3.11"
import os
environment = dict(os.environ)
environment["PXR_MTLX_STDLIB_SEARCH_PATHS"] = str(Path(deps["materialx"]["prefix"]) / "libraries")
command = [str(python), "-I", "-B", str(source / "openusd-smoke.py"),
           str(prefix), str(diag / "outputs"), str(Path(deps["materialx"]["path"]).parent),
           str(Path(deps["alembic"]["path"]).parent)]
with (diag / "smoke.log").open("wb") as log:
    result = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=120)
(diag / "diagnostic.json").write_text(json.dumps({"kind": "relink_diagnostic_not_full_build",
    "source": str(source), "links": retained, "smoke_command": command, "smoke_exit": result.returncode}, indent=2) + "\n")
print(diag)
print((diag / "smoke.log").read_text())
raise SystemExit(result.returncode)
