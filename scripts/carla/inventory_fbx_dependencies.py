#!/usr/bin/env python3
"""Read-only, lexical FBX dependency inventory; not a C++ or UBT call graph."""

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import NamedTuple


CPP_ROOTS = (
    "Engine/Source/Editor",
    "Engine/Source/Developer",
    "Engine/Plugins/Interchange",
)
BUILD_ROOTS = ("Engine/Source", "Engine/Plugins")
CPP_SUFFIXES = frozenset((
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".inc", ".inl", ".ipp", ".m", ".mm",
))
HEADER_SUFFIXES = frozenset((".h", ".hh", ".hpp", ".hxx", ".inc", ".inl", ".ipp"))
EXCLUDED_DIRS = frozenset((".git", "Binaries", "Intermediate", "Saved"))
DEPENDENCY_COLLECTIONS = frozenset((
    "PublicDependencyModuleNames", "PrivateDependencyModuleNames",
    "PublicIncludePathModuleNames", "PrivateIncludePathModuleNames",
    "DynamicallyLoadedModuleNames", "CircularlyReferencedDependentModules",
))
THIRD_PARTY_CALLS = frozenset((
    "AddEngineThirdPartyPrivateStaticDependencies",
    "AddEngineThirdPartyPrivateDynamicDependencies",
))

# Strings precede identifiers so comments and quoted examples cannot create uses.
LEXER = re.compile(
    r"(?P<space>\s+|\\\r?\n)"
    r"|(?P<comment>//(?:\\\r?\n|[^\n])*|/\*[\s\S]*?(?:\*/|\Z))"
    r'|(?P<raw>(?:u8|[uUL])?R"(?P<delimiter>[^\s()\\]{0,16})'
    r'\([\s\S]*?\)(?P=delimiter)")'
    r'|(?P<verbatim>(?:\$@|@\$|@)"(?:[^"]|"")*")'
    r'|(?P<string>(?:u8|[uUL]|\$)?"(?:\\[\s\S]|[^"\\])*")'
    r"|(?P<number>[0-9](?:[A-Za-z_0-9.]|'[A-Za-z_0-9])*)"
    r"|(?P<char>(?:u8|[uUL])?'(?:\\[\s\S]|[^'\\\n])+')"
    r"|(?P<identifier>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<punct>[^\s])"
)


class Token(NamedTuple):
    kind: str
    text: str
    line: int
    column: int


def tokenize(text):
    """Keep physical, one-based positions, omitting comments and whitespace."""
    line = 1
    line_start = 0
    tokens = []
    for match in LEXER.finditer(text):
        value = match.group()
        if match.lastgroup not in ("space", "comment"):
            tokens.append(Token(match.lastgroup, value, line, match.start() - line_start + 1))
        newlines = value.count("\n")
        if newlines:
            line += newlines
            line_start = match.start() + value.rfind("\n") + 1
    return tokens


def type_declarations(tokens):
    """Recognize named declarations and simple aliases, without resolving C++."""
    found = {}
    for index, token in enumerate(tokens):
        cursor = index + 1
        if token.text in ("class", "struct", "enum"):
            if token.text == "enum" and cursor < len(tokens) and tokens[cursor].text in ("class", "struct"):
                cursor += 1
            while cursor < len(tokens) and (
                tokens[cursor].text.endswith("_API")
                or tokens[cursor].text in ("FBXSDK_DLL", "FBXSDK_DLL_FRIEND")
            ):
                cursor += 1
            if cursor < len(tokens) and tokens[cursor].kind == "identifier":
                candidate = tokens[cursor]
                found.setdefault(candidate, token.text)
        elif token.text == "using":
            if cursor + 1 < len(tokens) and tokens[cursor + 1].text == "=":
                found.setdefault(tokens[cursor], "using")
        elif token.text == "typedef":
            end = cursor
            while end < len(tokens) and tokens[end].text not in (";", "{", "}", "("):
                end += 1
            if end > cursor and end < len(tokens) and tokens[end].text == ";":
                candidate = tokens[end - 1]
                if candidate.kind == "identifier":
                    found.setdefault(candidate, "typedef")
    return sorted(found.items(), key=lambda item: (item[0].line, item[0].column))


def evidence(token, lines):
    return {"line": token.line, "column": token.column, "source": lines[token.line - 1]}


def group_references(tokens, lines):
    by_name = defaultdict(lambda: defaultdict(set))
    for token in tokens:
        by_name[token.text][token.line].add(token.column)
    return [
        {
            "name": name,
            "evidence": [
                {"line": line, "columns": sorted(columns), "source": lines[line - 1]}
                for line, columns in sorted(by_name[name].items())
            ],
        }
        for name in sorted(by_name)
    ]


def includes(tokens):
    """Return literal #includes and exclude their header tokens from type uses."""
    found = []
    header_tokens = set()
    for index, token in enumerate(tokens):
        if token.text != "#" or (index and tokens[index - 1].line == token.line):
            continue
        if index + 2 >= len(tokens) or tokens[index + 1].text != "include":
            continue
        first = tokens[index + 2]
        last = index + 2
        name = None
        if first.kind == "string" and first.text.startswith('"'):
            name = first.text[1:-1]
        elif first.text == "<":
            last += 1
            parts = []
            while last < len(tokens) and tokens[last].line == first.line:
                if tokens[last].text == ">":
                    name = "".join(parts)
                    break
                parts.append(tokens[last].text)
                last += 1
        if name is not None:
            found.append((first, name))
            header_tokens.update(range(index, last + 1))
    return found, header_tokens


def callee_before(tokens, index):
    cursor = index - 1
    if cursor < 0 or tokens[cursor].kind != "identifier":
        return None
    parts = [tokens[cursor].text]
    while cursor >= 2 and tokens[cursor - 1].text == "." and tokens[cursor - 2].kind == "identifier":
        parts.insert(0, tokens[cursor - 2].text)
        cursor -= 2
    return ".".join(parts)


def dependency_call(name):
    if not name:
        return False
    parts = name.split(".")
    return parts[-1] in THIRD_PARTY_CALLS or (
        len(parts) >= 2 and parts[-2] in DEPENDENCY_COLLECTIONS
        and parts[-1] in ("Add", "AddRange")
    )


def literal_string(token):
    if token.kind == "verbatim" and token.text.startswith('@"'):
        return token.text[2:-1].replace('""', '"')
    if token.kind == "string" and token.text.startswith('"'):
        try:
            return json.loads(token.text)
        except ValueError:
            return None
    return None


def scan_build(text):
    tokens = tokenize(text)
    lines = text.splitlines()
    stack = []
    dependencies = []
    mentions = []
    for index, token in enumerate(tokens):
        if token.text == "(":
            stack.append((callee_before(tokens, index), token))
        elif token.text == ")":
            if stack:
                stack.pop()
        elif token.kind in ("identifier", "string", "verbatim", "raw") and "fbx" in token.text.lower():
            mentions.append({**evidence(token, lines), "token": token.text, "token_kind": token.kind})
            value = literal_string(token)
            # Only standalone literals in recognized calls are dependency evidence.
            if (
                value is not None and stack and dependency_call(stack[-1][0])
                and index and index + 1 < len(tokens)
                and tokens[index - 1].text in ("(", "{", ",")
                and tokens[index + 1].text in (")", "}", ",")
            ):
                operation, opening = stack[-1]
                dependencies.append({
                    **evidence(token, lines),
                    "dependency": value,
                    "kind": "autodesk_sdk_module" if value == "FBX" else "fbx_named_module",
                    "operation": operation,
                    "call_line": opening.line,
                })
    return dependencies, mentions


def walk_files(root, predicate):
    def onerror(error):
        raise error

    for directory, directories, files in os.walk(root, onerror=onerror, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in EXCLUDED_DIRS)
        for name in sorted(files):
            path = Path(directory) / name
            if predicate(path):
                yield path


def inventory(ue_root, sdk_include=None):
    ue_root = Path(ue_root).resolve()
    for relative in (*CPP_ROOTS, *BUILD_ROOTS):
        if not (ue_root / relative).is_dir():
            raise ValueError(f"Required source directory is missing: {relative}")
    if sdk_include is None:
        candidates = sorted(
            path.parent for path in (ue_root / "Engine/Source/ThirdParty/FBX").glob("*/include/fbxsdk.h")
        )
        if len(candidates) != 1:
            raise ValueError("Expected one local FBX SDK include tree; select it with --sdk-include")
        sdk_include = candidates[0]
    else:
        sdk_include = Path(sdk_include)
        if not sdk_include.is_absolute():
            sdk_include = ue_root / sdk_include
        sdk_include = sdk_include.resolve()
    if not sdk_include.is_relative_to(ue_root) or not (sdk_include / "fbxsdk.h").is_file():
        raise ValueError("--sdk-include must be an SDK include directory inside the UE root with fbxsdk.h")

    input_hash = hashlib.sha256()

    def relative(path):
        return path.relative_to(ue_root).as_posix()

    def read(path):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        input_hash.update(relative(path).encode("utf-8") + b"\0" + digest.encode("ascii") + b"\0")
        return data.decode("utf-8-sig"), digest

    sdk_headers = sorted(walk_files(sdk_include, lambda path: path.suffix in HEADER_SUFFIXES))
    sdk_aliases = defaultdict(list)
    sdk_types = defaultdict(list)
    for path in sdk_headers:
        name = path.relative_to(sdk_include).as_posix()
        sdk_aliases[name].append(relative(path))
        if name.startswith("fbxsdk/"):
            sdk_aliases[name[len("fbxsdk/"):]].append(relative(path))
        text, _ = read(path)
        lines = text.splitlines()
        for token, form in type_declarations(tokenize(text)):
            if token.text.startswith(("Fbx", "EFbx")):
                sdk_types[token.text].append({
                    "path": relative(path), "form": form, **evidence(token, lines),
                })
    if not sdk_types:
        raise ValueError("No Fbx*/EFbx* type declarations found in the local SDK headers")

    build_files = sorted({
        path for root in BUILD_ROOTS
        for path in walk_files(ue_root / root, lambda path: path.name.endswith(".Build.cs"))
    })
    module_rules = defaultdict(list)
    build_references = []
    build_dependencies = []
    for path in build_files:
        module_rules[path.parent].append(relative(path))
        text, digest = read(path)
        dependencies, mentions = scan_build(text) if "fbx" in text.lower() else ([], [])
        identity = {"path": relative(path), "module": path.name[:-len(".Build.cs")]}
        build_dependencies.extend({**identity, **entry} for entry in dependencies)
        if mentions:
            build_references.append({**identity, "sha256": digest, "lexical_references": mentions})

    cpp_files = sorted({
        path for root in CPP_ROOTS
        for path in walk_files(ue_root / root, lambda path: path.suffix in CPP_SUFFIXES)
    })
    ue_headers = defaultdict(list)
    for path in cpp_files:
        if path.suffix in HEADER_SUFFIXES:
            ue_headers[path.name].append(relative(path))
    sources = []
    wrapper_types = defaultdict(list)
    for path in cpp_files:
        text, digest = read(path)
        if "fbx" not in text.lower():
            continue
        tokens = tokenize(text)
        lines = text.splitlines()
        for token, form in type_declarations(tokens):
            if "fbx" in token.text.lower() and token.text not in sdk_types:
                wrapper_types[token.text].append({
                    "path": relative(path), "form": form, **evidence(token, lines),
                })
        sources.append((path, tokens, lines, digest))

    def owners(path):
        for parent in path.parents:
            if parent in module_rules:
                return module_rules[parent]
            if parent == ue_root:
                break
        return []

    files = []
    used_sdk_types = set()
    used_wrappers = set()
    for path, tokens, lines, digest in sources:
        sdk_includes = []
        wrapper_includes = []
        other_includes = []
        found_includes, excluded_tokens = includes(tokens)
        for token, name in found_includes:
            item = {**evidence(token, lines), "include": name}
            if name in sdk_aliases or name == "fbxsdk.h" or name.startswith("fbxsdk/"):
                sdk_includes.append({**item, "header_candidates": sorted(sdk_aliases.get(name, []))})
            elif "fbx" in name.lower():
                candidates = [
                    candidate for candidate in ue_headers.get(name.rsplit("/", 1)[-1], [])
                    if candidate.endswith("/" + name) or candidate == name
                ]
                destination = wrapper_includes if candidates else other_includes
                destination.append({**item, "header_candidates": sorted(candidates)})
        sdk_refs, wrapper_refs, other_refs = [], [], []
        for index, token in enumerate(tokens):
            if token.kind != "identifier" or index in excluded_tokens:
                continue
            if token.text in sdk_types:
                sdk_refs.append(token)
                used_sdk_types.add(token.text)
            elif token.text in wrapper_types:
                wrapper_refs.append(token)
                used_wrappers.add(token.text)
            elif token.text.startswith(("Fbx", "EFbx")):
                other_refs.append(token)
        if any((sdk_includes, wrapper_includes, other_includes, sdk_refs, wrapper_refs, other_refs)):
            files.append({
                "path": relative(path),
                "module_build_rules": owners(path),
                "sha256": digest,
                "sdk_includes": sdk_includes,
                "sdk_type_name_references": group_references(sdk_refs, lines),
                "unreal_wrapper_includes": wrapper_includes,
                "unreal_wrapper_type_name_references": group_references(wrapper_refs, lines),
                "other_fbx_includes": other_includes,
                "other_fbx_identifiers": group_references(other_refs, lines),
            })

    return {
        "schema_version": 1,
        "analysis": "lexical_references_not_a_semantic_call_graph",
        "limitations": [
            "No preprocessing, macro expansion, UBT execution, target selection or transitive call/include graph.",
            "All conditional branches are scanned; references do not establish active target dependencies.",
            "SDK type names match named class/struct/enum declarations and simple typedef/using aliases in local headers.",
            "Type-name matches can also be same-spelled variables, macro parameters or names in other namespaces.",
            "Unreal wrappers match FBX-named declarations in the scoped UE sources, not every Unreal API.",
            "Other Fbx*/EFbx* identifiers are unclassified, not evidence of SDK types.",
            "Only standalone FBX-containing string literals in recognized module-list Add/AddRange or third-party calls are build dependencies.",
            "Build references are lexical mentions, including paths, definitions and diagnostics; not all are dependencies.",
            "Includes use literal spellings and candidate paths, not the compiler's include search order.",
            "Comments and strings are excluded from C++ type references; interpolated-string expressions are not analyzed.",
            "Token-pasted names, split identifiers and complex or macro-generated type aliases can be missed.",
            "Traversal excludes the listed generated/cache directories and does not follow directory symlinks.",
            "An empty inventory is not proof of SDK removal, successful linking, Editor/Cook or feature parity.",
        ],
        "scope": {
            "path_basis": "UE-root-relative POSIX paths; line/column positions are one-based characters",
            "cpp_roots": list(CPP_ROOTS),
            "build_roots": list(BUILD_ROOTS),
            "cpp_suffixes": sorted(CPP_SUFFIXES),
            "excluded_directory_names": sorted(EXCLUDED_DIRS),
            "cpp_files_scanned": len(cpp_files),
            "build_files_scanned": len(build_files),
            "input_sha256": input_hash.hexdigest(),
        },
        "sdk": {
            "include_root": relative(sdk_include),
            "headers_scanned": len(sdk_headers),
            "type_names_cataloged": len(sdk_types),
            "referenced_type_declarations": [
                {"name": name, "evidence": sdk_types[name]} for name in sorted(used_sdk_types)
            ],
        },
        "unreal_wrapper_declarations": [
            {"name": name, "evidence": wrapper_types[name]} for name in sorted(used_wrappers)
        ],
        "files": files,
        "build_dependencies": build_dependencies,
        "build_references": build_references,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ue-root", type=Path,
        default=Path(__file__).resolve().parents[2] / "third_party/unreal-engine",
    )
    parser.add_argument("--sdk-include", type=Path, help="SDK include directory, relative to --ue-root or absolute")
    args = parser.parse_args(argv)
    try:
        report = inventory(args.ue_root, args.sdk_include)
    except (OSError, ValueError) as error:
        parser.exit(2, f"FBX inventory failed: {error}\n")
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
