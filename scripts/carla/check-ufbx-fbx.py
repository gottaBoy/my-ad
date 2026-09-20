#!/usr/bin/env python3
"""Verify the native ufbx static backend; does not build or imply UE/Cook support."""

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import resource
import subprocess
import sys


VERSION = "0.23.0"
COMMIT = "fcc5d6ba444cfd3eb80677dba5e37e493941abe5"
STAGE = "ufbx-fbx-static-backend"
SCOPE = "ufbx static FBX backend only; not Autodesk SDK ABI, UE Editor/Cook or RPC"
UPSTREAM_TESTS = (
    "test_bigint_basics", "test_bigint_mad", "test_bigint_div_manual", "test_double_parse_nan",
)
FIXTURES = (
    ("blender-cube", "BlenderCube.fbx", 12, "35d5023b2f5d690a3c4fecba0c754537eebd6b7c2f271e4b37c8dcbb6fa755a3"),
    ("multi-material", "MultiMatId.fbx", 960, "ca9a168ef899f0ddb96df1e76bb7d19d189c9f31422c7fea053d9580344c3592"),
    ("unsupported-animation", "AnimatedCharacter.fbx", None, "be8c9549d0c466a45e54c0c4c26eaea4d44222704706f2bd3ca3225eba82b0ca"),
    ("unsupported-morph", "MorphTargets.fbx", None, "5566434aaace81f88add81d9b708cc37e0b87998191144221dc8a5198cf3b6cb"),
)
FEATURE_KEYS = ("animation_stacks", "animation_curves", "skin_deformers", "blend_deformers", "cache_deformers")
spec = importlib.util.spec_from_file_location("ufbx_stage_report", Path(__file__).with_name("stage_report.py"))
stage_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage_report)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError(f"non-finite JSON value: {value}")


def read_json(path):
    return json.loads(path.read_bytes(), object_pairs_hook=unique_object, parse_constant=invalid_constant)


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify_static_archive(path):
    """Inspect every object in a normal ar archive, not just its filename."""
    data = path.read_bytes()
    require(data.startswith(b"!<arch>\n"), "static library must be a normal ar archive, not thin")
    offset, members, names = 8, [], b""
    while offset < len(data):
        header = data[offset:offset+60]
        require(len(header) == 60 and header[58:] == b"`\n", "invalid ar member header")
        size = int(header[48:58].decode("ascii").strip())
        require(size >= 0 and offset + 60 + size <= len(data), "truncated ar member")
        name = header[:16].decode("ascii").strip()
        body = data[offset+60:offset+60+size]
        offset += 60 + size + size % 2
        require(offset <= len(data), "missing ar padding")
        if name == "//":
            names = body
            continue
        if name in ("/", "/SYM64/"):
            continue
        if name.startswith("#1/"):
            length = int(name[3:])
            require(0 <= length <= len(body), "invalid BSD ar name length")
            name, body = body[:length].rstrip(b"\0").decode("utf-8"), body[length:]
        elif name.startswith("/") and name[1:].isdigit():
            index = int(name[1:])
            end = names.find(b"/\n", index)
            require(0 <= index < len(names) and end >= index, "invalid GNU ar long name")
            name = names[index:end].decode("utf-8")
        else:
            name = name.rstrip("/")
        require(len(body) >= 20 and body[:6] == b"\x7fELF\x02\x01", f"non-ELF64 member: {name}")
        require(int.from_bytes(body[16:18], "little") == 1, f"non-relocatable member: {name}")
        require(int.from_bytes(body[18:20], "little") == 183, f"non-AArch64 member: {name}")
        members.append(name)
    require(members, "empty static archive")
    return members


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, f"expected integer >= {minimum}: {value!r}")
    return value


def vector(value, size):
    require(isinstance(value, list) and len(value) == size, f"expected vector of length {size}")
    require(all(type(x) in (int, float) and math.isfinite(x) for x in value), "non-finite vector")
    return value


def close(actual, expected, message):
    require(len(actual) == len(expected) and all(
        math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-7) for a, b in zip(actual, expected)
    ), message)


def transform(matrix, point):
    return [sum(matrix[row*4 + k] * point[k] for k in range(3)) + matrix[row*4 + 3] for row in range(3)]


def multiply(a, b):
    return [
        sum(a[row*4 + k] * b[k*4 + column] for k in range(3)) + (a[row*4 + 3] if column == 3 else 0)
        for row in range(3) for column in range(4)
    ]


def normal_matrix(matrix):
    rows = [matrix[row*4:row*4+3] for row in range(3)]
    cofactors = []
    for row in range(3):
        for column in range(3):
            minor = [rows[r][c] for r in range(3) if r != row for c in range(3) if c != column]
            cofactors.append((-1)**(row + column) * (minor[0]*minor[3] - minor[1]*minor[2]))
    determinant = sum(rows[0][i] * cofactors[i] for i in range(3))
    require(math.isfinite(determinant) and determinant != 0, "singular geometry matrix")
    return determinant, [value / determinant for value in cofactors]


def unit_normal(value):
    vector(value, 3)
    close([math.hypot(*value)], [1.0], "normal is not normalized")


def indexed(items):
    require(isinstance(items, list), "expected object list")
    result = {}
    for item in items:
        require(isinstance(item, dict), "list entry is not an object")
        key = integer(item["id"])
        require(key not in result, f"duplicate element id: {key}")
        require(isinstance(item["name"], str), "element name is not a string")
        result[key] = item
    return result


def validate_scene(report):
    nodes, meshes, materials = (indexed(report[key]) for key in ("nodes", "meshes", "materials"))
    root = report["root_id"]
    require(nodes and meshes and root in nodes and nodes[root]["parent"] is None, "missing root or static geometry")
    axes = report["source_axes"]
    require(isinstance(axes, list) and len(axes) == 3, "invalid source axes")
    require(all(type(axis) is int and 0 <= axis < 6 for axis in axes) and len({axis // 2 for axis in axes}) == 3,
            "source axes are not a basis")
    unit = report["source_unit_meters"]
    require(type(unit) in (int, float) and math.isfinite(unit) and unit > 0, "invalid source unit")
    policy = report["policy"]
    require(policy["target_axes"] == ([2, 4, 1] if report["space"] == "ue-centimeters" else []), "unexpected target axes")
    require(policy["target_unit_meters"] == (0.01 if report["space"] == "ue-centimeters" else 0.0), "unexpected target units")
    require(policy["uv"] == "ufbx source convention; no V flip", "unexpected UV policy")
    for node in nodes.values():
        for name in ("node_to_parent", "node_to_world", "geometry_to_node", "geometry_to_world"):
            vector(node[name], 12)
        parent = node["parent"]
        require(parent in nodes if node["id"] != root else parent is None, "disconnected node")
        require(all(item in materials for item in node["materials"]), "invalid node material")
        children = node["children"]
        require(len(children) == len(set(children)), "duplicate child")
        require(set(children) == {item["id"] for item in nodes.values() if item["parent"] == node["id"]},
                "parent/child links disagree")
        visited, cursor = set(), node["id"]
        while cursor is not None:
            require(cursor not in visited, "cycle in node hierarchy")
            visited.add(cursor)
            cursor = nodes[cursor]["parent"]
        close(node["geometry_to_world"], multiply(node["node_to_world"], node["geometry_to_node"]),
              "geometry transform was not composed independently of child transforms")
        if parent is not None and node["inherit_mode"] == 0:
            close(node["node_to_world"], multiply(nodes[parent]["node_to_world"], node["node_to_parent"]),
                  "normal inheritance is inconsistent")
        require(node["mesh"] is None or node["mesh"] in meshes, "unknown mesh reference")
    for mesh in meshes.values():
        require(mesh["positions"] and mesh["triangles"], "empty mesh")
        for position in mesh["positions"]:
            vector(position, 3)
        integer(mesh["face_count"], 1)
        require(all(isinstance(name, str) for name in mesh["uv_sets"] + mesh["color_sets"]), "invalid attribute set name")
        for triangle in mesh["triangles"]:
            require(integer(triangle["source_face"]) < mesh["face_count"], "invalid source face")
            integer(triangle["material_slot"], -1)
            corners = triangle["corners"]
            require(len(corners) == 3, "not a triangle")
            indices = [integer(corner["vertex"]) for corner in corners]
            require(len(set(indices)) == 3 and max(indices) < len(mesh["positions"]), "invalid triangle vertices")
            for corner in corners:
                unit_normal(corner["normal"])
                require(len(corner["uv"]) == len(mesh["uv_sets"]), "UV channel lost")
                require(len(corner["color"]) == len(mesh["color_sets"]), "color channel lost")
                for uv in corner["uv"]:
                    vector(uv, 2)
                for rgba in corner["color"]:
                    vector(rgba, 4)
    seen_instances = set()
    for instance in report["instances"]:
        node_id = instance["node"]
        require(node_id in nodes and node_id not in seen_instances, "invalid or duplicate instance")
        seen_instances.add(node_id)
        node = nodes[node_id]
        require(instance["mesh"] == node["mesh"], "instance references the wrong mesh")
        mesh = meshes[node["mesh"]]
        points = instance["positions_world"]
        require(len(points) == len(mesh["positions"]), "world vertices lost")
        for local, world in zip(mesh["positions"], points):
            vector(world, 3)
            close(world, transform(node["geometry_to_world"], local), "world position omitted geometry transform")
        bounds = vector(instance["bounds_world"], 6)
        close(bounds, [op(point[i] for point in points) for op in (min, max) for i in range(3)], "bounds disagree with geometry")
        det, normals = normal_matrix(node["geometry_to_world"])
        close([instance["determinant"]], [det], "determinant mismatch")
        require(len(instance["normals_world"]) == 3 * len(mesh["triangles"]), "world normals lost")
        require(len(instance["triangle_materials"]) == len(mesh["triangles"]), "face materials lost")
        for index, triangle in enumerate(mesh["triangles"]):
            slot = triangle["material_slot"]
            require(slot < len(node["materials"]) if slot >= 0 else not node["materials"], "invalid instance material slot")
            require(instance["triangle_materials"][index] == (node["materials"][slot] if slot >= 0 else None),
                    "per-instance material binding was replaced by mesh material")
            a, b, c = (points[corner["vertex"]] for corner in triangle["corners"])
            u, v = ([b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)])
            area = math.hypot(u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
            require(math.isfinite(area) and area > 0, "degenerate triangle")
            for offset, corner in enumerate(triangle["corners"]):
                expected = [sum(normals[row*3+k] * corner["normal"][k] for k in range(3)) for row in range(3)]
                length = math.hypot(*expected)
                require(math.isfinite(length) and length > 0, "invalid transformed normal")
                actual = instance["normals_world"][index*3+offset]
                unit_normal(actual)
                close(actual, [x / length for x in expected], "normal did not use inverse-transpose")
    require(seen_instances == {node["id"] for node in nodes.values() if node["mesh"] is not None}, "mesh instances lost")
    counts = {
        "nodes": len(nodes), "meshes": len(meshes), "materials": len(materials),
        "triangles": sum(len(mesh["triangles"]) for mesh in meshes.values()),
        "mesh_instances": len(seen_instances),
        "instanced_triangles": sum(len(meshes[nodes[node]["mesh"]]["triangles"]) for node in seen_instances),
    }
    require(report["counts"] == counts, "reported counts disagree with the actual arrays")
    for value in report["counts"].values():
        integer(value)
    return counts


def evaluate(report, returncode, path, space, expected):
    require(type(returncode) is int and returncode == {"PASS": 0, "REJECTED": 2, "FAIL": 1}[expected],
            f"unexpected process exit: {returncode}")
    require(isinstance(report, dict), "probe report must be an object")
    for key, value in (
        ("schema_version", 1), ("stage", STAGE), ("scope", SCOPE),
        ("version", VERSION), ("commit", COMMIT), ("input", str(path)), ("space", space), ("status", expected),
    ):
        require(report.get(key) == value, f"{key} mismatch")
    require(isinstance(report.get("error"), str), "missing error string")
    for key in FEATURE_KEYS:
        integer(report["features"][key])
    if expected != "PASS":
        require(report["error"].strip(), "rejection/failure must have an error")
        require(all(report.get(key) == [] for key in ("nodes", "meshes", "instances", "materials")), "partial output on failure")
        if expected == "REJECTED":
            require(report["error_code"] == "unsupported_features", "wrong rejection reason")
            require(any(report["features"][key] for key in FEATURE_KEYS[1:]), "no unsupported feature was detected")
        else:
            require(report["error_code"] == "parse_error", "corrupt input did not fail parsing cleanly")
        return None
    require(report["error"] == "" and report["error_code"] == "", "PASS contains an error")
    require(not any(report["features"][key] for key in FEATURE_KEYS[1:]), "unsupported feature silently accepted")
    return validate_scene(report)


def compare_spaces(source, converted):
    source_nodes, converted_nodes = indexed(source["nodes"]), indexed(converted["nodes"])
    require(source_nodes.keys() == converted_nodes.keys(), "axis conversion changed node identities")
    require(source["counts"] == converted["counts"] and source["materials"] == converted["materials"],
            "conversion lost topology or materials")
    require(source["source_axes"] == converted["source_axes"], "source axis metadata lost")
    require(source["source_unit_meters"] == converted["source_unit_meters"], "source unit metadata lost")
    for key, node in source_nodes.items():
        for field in ("name", "parent", "mesh", "children", "materials", "inherit_mode"):
            require(node[field] == converted_nodes[key][field], f"conversion changed node {field}")
    target_axes = [2, 4, 1]  # right +Y, up +Z, front -X (forward +X)
    scale = source["source_unit_meters"] / 0.01
    by_node = {instance["node"]: instance for instance in converted["instances"]}
    for instance in source["instances"]:
        for before, after in zip(instance["positions_world"], by_node[instance["node"]]["positions_world"]):
            expected = [0.0, 0.0, 0.0]
            for axis, target in zip(source["source_axes"], target_axes):
                expected[target // 2] = before[axis // 2] * (-1 if axis % 2 else 1) * (-1 if target % 2 else 1) * scale
            close(after, expected, "axis/unit conversion was missing or applied twice")


def check_hierarchy(report):
    nodes = {node["name"]: node for node in report["nodes"]}
    require({"Parent", "MeshA", "MeshB", "Child"} <= nodes.keys(), "control fixture nodes lost")
    require(nodes["Child"]["parent"] == nodes["MeshA"]["id"], "child hierarchy changed")
    require(nodes["MeshA"]["mesh"] == nodes["MeshB"]["mesh"], "shared geometry was duplicated")
    by_node = {instance["node"]: instance for instance in report["instances"]}
    expected = {
        "source": {
            "MeshA": [[3, 2, 4], [1, 2, 4], [3, 3, 4]],
            "MeshB": [[-3, 2, 0], [-2, 2, 0], [-3, 3, 0]], "Child": [3, 2, 1],
        },
        "ue-centimeters": {
            "MeshA": [[-400, 300, 200], [-400, 100, 200], [-400, 300, 300]],
            "MeshB": [[0, -300, 200], [0, -200, 200], [0, -300, 300]], "Child": [-100, 300, 200],
        },
    }[report["space"]]
    for name in ("MeshA", "MeshB"):
        for actual, point in zip(by_node[nodes[name]["id"]]["positions_world"], expected[name]):
            close(actual, point, f"incorrect {name} geometry transform")
    close(transform(nodes["Child"]["node_to_world"], [0, 0, 0]), expected["Child"], "geometry transform leaked into child")
    names = {item["id"]: item["name"] for item in report["materials"]}
    require(names[by_node[nodes["MeshA"]["id"]]["triangle_materials"][0]] == "Red", "MeshA lost Red")
    require(names[by_node[nodes["MeshB"]["id"]]["triangle_materials"][0]] == "Blue", "MeshB lost Blue")


def invoke(program, path, space, directory, expected):
    argv = [str(program), f"--{space}", str(path)]
    write_json(directory.with_suffix(".command.json"), argv)
    stdout, stderr = directory.with_suffix(".raw.json"), directory.with_suffix(".stderr.log")
    with stdout.open("xb") as out, stderr.open("xb") as err:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=60, check=False)
    report = read_json(stdout)
    evaluate(report, result.returncode, path, space, expected)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("program", "upstream", "fixtures", "control-fixture", "run-dir", "source-dir", "ue-root", "library", "static-library"):
        parser.add_argument(f"--{argument}", type=Path, required=True)
    args = parser.parse_args(argv)
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    args.run_dir.mkdir(parents=True, exist_ok=True)
    work = args.run_dir / "checks"
    work.mkdir(mode=0o755)
    checks, evidence = {}, {
        "library": args.library, "static-library": args.static_library,
        "probe": args.program, "upstream-program": args.upstream,
        "evaluator": Path(__file__).resolve(), "stage-writer": Path(stage_report.__file__).resolve(),
    }
    for pattern in ("*.log", "*.sha256", "*.patch", "*commit.txt", "*identity.json", "*command.json"):
        for path in args.run_dir.glob(pattern):
            if path.name != "validation.log":
                evidence[f"build.{path.name}"] = path
    ue_commit = "unverified"

    def run_case(name, action):
        result = {"status": "FAIL", "error": ""}
        try:
            result["details"] = action()
            result["status"] = "PASS"
        except (OSError, ValueError, KeyError, TypeError, OverflowError, subprocess.TimeoutExpired) as error:
            result["error"] = str(error)
        path = work / f"{name}.result.json"
        write_json(path, result)
        checks[name] = result["status"]
        evidence[name] = path
        print(f"{result['status']} {name}: {result['error']}", flush=True)

    def preflight():
        nonlocal ue_commit
        require((args.run_dir / "ufbx-commit.txt").read_text().strip() == COMMIT, "wrong ufbx source revision")
        ue_commit = (args.run_dir / "ue-commit.txt").read_text().strip()
        require(len(ue_commit) == 40 and all(c in "0123456789abcdef" for c in ue_commit), "invalid UE commit")
        for path in (args.program, args.upstream, args.library):
            with path.open("rb") as stream:
                elf = stream.read(20)
            require(elf[:6] == b"\x7fELF\x02\x01" and int.from_bytes(elf[18:20], "little") == 183, "not AArch64 ELF")
        for _, filename, _, checksum in FIXTURES:
            path = args.fixtures / filename
            require(digest(path) == checksum, f"fixture hash changed: {filename}")
            evidence[f"fixture.{filename}"] = path
        evidence["fixture.control"] = args.control_fixture
        require(args.control_fixture.is_file(), "missing control fixture")
        return {"version": VERSION, "commit": COMMIT, "architecture": "AArch64",
                "static_members": verify_static_archive(args.static_library)}

    run_case("preflight", preflight)
    if checks["preflight"] == "PASS":
        saved_core = resource.getrlimit(resource.RLIMIT_CORE)
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, saved_core[1]))

            def upstream():
                for name in UPSTREAM_TESTS:
                    command = [str(args.upstream), "--test", name]
                    write_json(work / f"{name}.command.json", command)
                    path = work / f"{name}.log"
                    with path.open("xb") as out:
                        result = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT, timeout=90, check=False)
                    require(result.returncode == 0 and path.read_text().splitlines() == [name], f"upstream test failed or did not run: {name}")
                return {"completed": list(UPSTREAM_TESTS), "scope": "upstream core arithmetic tests only; not full FBX suite"}

            run_case("upstream-core", upstream)

            def positive(name, path, triangles):
                reports = {}
                for space in ("source", "ue-centimeters"):
                    first = invoke(args.program, path, space, work / f"{name}-{space}-first", "PASS")
                    second = invoke(args.program, path, space, work / f"{name}-{space}-repeat", "PASS")
                    require(first == second, "identical input did not produce deterministic scene data")
                    require(first["counts"]["instanced_triangles"] == triangles, "fixture triangle count changed")
                    if name == "hierarchy-geometry":
                        check_hierarchy(first)
                    if name == "multi-material":
                        used = {material for item in first["instances"] for material in item["triangle_materials"]}
                        require(len(used - {None}) >= 2, "fixture no longer exercises multiple material bindings")
                    reports[space] = first
                compare_spaces(reports["source"], reports["ue-centimeters"])
                return {"counts": reports["source"]["counts"], "source_unit_meters": reports["source"]["source_unit_meters"],
                        "checks": ["hierarchy", "geometry transforms", "per-instance materials", "normals", "attributes", "determinism", "axis/unit conversion"]}

            for name, filename, triangles, _ in FIXTURES:
                if triangles is not None:
                    run_case(name, lambda name=name, filename=filename, triangles=triangles:
                             positive(name, args.fixtures / filename, triangles))
                else:
                    def negative(name=name, filename=filename):
                        report = invoke(args.program, args.fixtures / filename, "ue-centimeters", work / name, "REJECTED")
                        key = "animation_curves" if name == "unsupported-animation" else "blend_deformers"
                        require(report["features"][key] > 0, f"fixture did not exercise {key}")
                        return {"rejected": report["features"], "support": False}
                    run_case(name, negative)
            run_case("hierarchy-geometry", lambda: positive("hierarchy-geometry", args.control_fixture, 2))
            for name, content in (
                ("invalid-input", b"not an FBX document\n"),
                ("truncated-input", (args.fixtures / "BlenderCube.fbx").read_bytes()[:32]),
            ):
                path = work / f"{name}.fbx"
                path.write_bytes(content)
                run_case(name, lambda name=name, path=path: {
                    "error_code": invoke(args.program, path, "source", work / name, "FAIL")["error_code"],
                    "rejected": True,
                })
        finally:
            resource.setrlimit(resource.RLIMIT_CORE, saved_core)
    required = ["preflight", "upstream-core", *[item[0] for item in FIXTURES],
                "hierarchy-geometry", "invalid-input", "truncated-input"]
    for name in required:
        if name not in checks:
            checks[name] = "MISSING"
    for path in sorted(work.iterdir()):
        evidence[f"raw.{path.name}"] = path
    report = stage_report.write_report(
        args.run_dir / "stage-report.json", stage_id=STAGE, scope=SCOPE,
        exit_code=0 if all(value == "PASS" for value in checks.values()) else 1,
        required_checks=required, checks=checks, evidence=evidence,
        sources={"ufbx": {"location": str(args.source_dir), "revision": COMMIT},
                 "ue": {"location": str(args.ue_root), "revision": ue_commit}},
        command=[sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], prerequisites=[],
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
