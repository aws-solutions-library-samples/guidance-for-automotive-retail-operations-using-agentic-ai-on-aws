"""Per-agent code bundles for the AgentCore runtimes (ADR 0009, design §8).

Each runtime ships only its own agent's code: the static import closure of the
agent's adapter module, its tool and job targets, its reasoning entry (A3) and
the service modules the dispatcher loads, plus the fixed `app/agentcore/*`
files. Imports inside functions are followed too (the service layer imports
lazily); `importlib` strings are not, which keeps the API handler out.

  entry_modules(spec)              the closure roots for one agent
  closure(spec, root)              sorted repo-relative .py paths of the closure
  bundle_files(spec, root)         fixed agentcore files + this agent's adapter + closure
  code_sha256(files, deps_dir)     no-op detection: code bytes + resolved dist-info names
  build_info(...)                  the BUILD_INFO.json written at the bundle root
  build_zip(spec, deps_dir, ...)   reproducible zip bytes and its BUILD_INFO
  aws_calls(files, root)           (service, method) pairs called on boto3 clients

Stdlib only (`ast`, `hashlib`, `zipfile`).
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import registry

ROOT = Path(__file__).resolve().parents[2]

# Dependency profiles: requirements file per profile (design §8.1).
PROFILES = {"tools": "requirements-agentcore-tools.txt", "strands": "requirements-agentcore.txt"}

# Files every bundle carries besides its closure (design §8.1).
FIXED_FILES = (
    "app/__init__.py",
    "app/agentcore/__init__.py",
    "app/agentcore/agent.py",
    "app/agentcore/registry.py",
    "app/agentcore/client.py",
    "app/agentcore/tools/__init__.py",
    "app/services/__init__.py",
)
_RUNTIME_MODULES = ("app.agentcore.agent", "app.agentcore.registry", "app.agentcore.client")

ZIP_DATE = (1980, 1, 1, 0, 0, 0)        # fixed timestamp: the same code gives the same bytes
_FILE_ATTR = (0o100644 & 0xFFFF) << 16  # regular file, rw-r--r--


def adapter_module(spec: registry.AgentSpec) -> str:
    """The agent's own tool adapter module (`app.agentcore.tools.a1`, ...)."""
    return f"app.agentcore.tools.{spec.code.lower()}"


def _module_of(ref: str) -> str:
    return ref.partition(":")[0]


def entry_modules(spec: registry.AgentSpec) -> list[str]:
    """The closure roots: adapter, tool/job targets, reasoning entry, the
    dispatcher's service modules and the runtime entry point itself."""
    mods = [adapter_module(spec)]
    mods += [_module_of(t.target) for t in spec.tools]
    mods += [_module_of(j.target) for j in spec.jobs]
    if spec.reasoning:
        mods.append(_module_of(spec.reasoning))
    mods += list(registry.DISPATCHER_MODULES) + list(_RUNTIME_MODULES)
    return list(dict.fromkeys(mods))


# --- static import closure ------------------------------------------------------------

def _file_for(module: str, root: Path) -> Path | None:
    """The source file of an `app.` module (module.py or package/__init__.py)."""
    if module != "app" and not module.startswith("app."):
        return None
    base = root.joinpath(*module.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _imports(module: str, path: Path, root: Path) -> set[str]:
    """Every `app.` module imported anywhere in the file (module or function level)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(parts + (node.module.split(".") if node.module else []))
            else:
                base = node.module or ""
            found.add(base)
            for alias in node.names:           # `from pkg import name` may name a submodule
                sub = f"{base}.{alias.name}"
                if _file_for(sub, root) is not None:
                    found.add(sub)
    return {m for m in found if _file_for(m, root) is not None}


def _with_parents(modules: Iterable[str], root: Path) -> set[str]:
    out = set(modules)
    for m in list(out):
        parts = m.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[:i])
            if _file_for(parent, root) is not None:
                out.add(parent)
    return out


def closure_modules(spec: registry.AgentSpec, root: Path = ROOT) -> set[str]:
    """Dotted names of every module the agent's runtime can import statically."""
    seen: set[str] = set()
    pending = [m for m in entry_modules(spec) if _file_for(m, root) is not None]
    while pending:
        mod = pending.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = _file_for(mod, root)
        if path is not None:
            pending.extend(_imports(mod, path, root) - seen)
    return _with_parents(seen, root)


def closure(spec: registry.AgentSpec, root: Path = ROOT) -> list[str]:
    """Sorted repo-relative .py paths of the agent's static import closure."""
    paths = {_file_for(m, root) for m in closure_modules(spec, root)}
    return sorted(p.relative_to(root).as_posix() for p in paths if p is not None)


def bundle_files(spec: registry.AgentSpec, root: Path = ROOT) -> list[str]:
    """The code files of the agent's bundle: the fixed agentcore files, only
    this agent's adapter module, and the closure."""
    files = set(closure(spec, root)) | {f for f in FIXED_FILES if (root / f).is_file()}
    adapter = _file_for(adapter_module(spec), root)
    if adapter is not None:
        files.add(adapter.relative_to(root).as_posix())
    return sorted(files)


def service_module_count(files: Iterable[str]) -> int:
    """Non-`__init__` service modules in a file list (the design §8.2 count)."""
    return sum(1 for f in files if f.startswith("app/services/") and not f.endswith("/__init__.py"))


# --- hashes and build info ------------------------------------------------------------

def dist_infos(deps_dir: Path | None) -> list[str]:
    """Sorted `*.dist-info` directory names in a deps tree (the resolved deps)."""
    if deps_dir is None or not deps_dir.is_dir():
        return []
    return sorted(p.name for p in deps_dir.iterdir() if p.is_dir() and p.name.endswith(".dist-info"))


def code_sha256(files: Iterable[str], deps_dir: Path | None, root: Path = ROOT) -> str:
    """sha256 over the sorted code files (path and bytes) plus the sorted
    dist-info names in `deps_dir`, so a re-run with nothing new is a no-op."""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode() + b"\0")
        h.update((root / rel).read_bytes() + b"\0")
    for name in dist_infos(deps_dir):
        h.update(b"dist:" + name.encode() + b"\0")
    return h.hexdigest()


def build_info(spec: registry.AgentSpec, sha: str, deps_dir: Path | None,
               git_commit: str | None = None) -> dict[str, Any]:
    """BUILD_INFO.json (design §6.2); the dispatcher reports code_sha256[:12] as its version."""
    return {"agent": spec.code, "runtime_name": spec.runtime_name, "code_sha256": sha,
            "deps_profile": spec.deps,
            "deps": [n.removesuffix(".dist-info") for n in dist_infos(deps_dir)],
            "git_commit": git_commit}


def _zip_entry(z: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = _FILE_ATTR
    info.create_system = 3                # unix, so the permissions above are honoured
    z.writestr(info, data)


def build_zip(spec: registry.AgentSpec, deps_dir: Path | None, *, requirements: Path,
              root: Path = ROOT, git_commit: str | None = None) -> tuple[bytes, dict[str, Any]]:
    """The agent's bundle as zip bytes, with entries in sorted order and fixed
    timestamps and permissions, plus its BUILD_INFO. `deps_dir=None` builds a
    code-only bundle (tests, `--plan-json`)."""
    files = bundle_files(spec, root)
    info = build_info(spec, code_sha256(files, deps_dir, root), deps_dir, git_commit)
    entries: dict[str, bytes | Path] = {rel: root / rel for rel in files}
    entries["requirements.txt"] = requirements
    entries["BUILD_INFO.json"] = (json.dumps(info, indent=2, sort_keys=True) + "\n").encode()
    if deps_dir is not None and deps_dir.is_dir():
        for f in deps_dir.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                entries[(Path("deps") / f.relative_to(deps_dir)).as_posix()] = f
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(entries):
            src = entries[name]
            _zip_entry(z, name, src if isinstance(src, bytes) else src.read_bytes())
    return buf.getvalue(), info


# --- AWS calls made by the bundled code ------------------------------------------------

def _boto3_service(node: ast.AST) -> str | None:
    """'svc' for a `boto3.client("svc", ...)` call, else None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "client"
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "boto3"
            and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
        return node.args[0].value
    return None


def _target_key(node: ast.AST) -> str | None:
    """A name (`rt`) or a simple attribute (`self.rt`) a client can be bound to."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _module_calls(tree: ast.AST) -> set[tuple[str, str]]:
    # functions that return a client, e.g. ddb._client() -> "dynamodb"
    factories: dict[str, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Return) and node.value is not None and _boto3_service(node.value):
                    factories[fn.name] = _boto3_service(node.value) or ""

    def service_of(expr: ast.AST, names: dict[str, str]) -> str | None:
        svc = _boto3_service(expr)
        if svc:
            return svc
        if isinstance(expr, ast.Call):
            callee = expr.func
            name = callee.id if isinstance(callee, ast.Name) else callee.attr if isinstance(callee, ast.Attribute) else None
            if name in factories:
                return factories[name]
        key = _target_key(expr)
        return names.get(key) if key else None

    # names bound to a client anywhere in the module (flat: names are short-lived locals)
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            svc = service_of(node.value, names)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                key = _target_key(t)
                if svc and key:
                    names[key] = svc
    calls: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            svc = service_of(node.func.value, names)
            if svc:
                calls.add((svc, node.func.attr))
    return calls


def aws_calls(files: Iterable[str], root: Path = ROOT) -> set[tuple[str, str]]:
    """(service, method) for every method called on a boto3 client in the files:
    clients from `boto3.client("<svc>")`, from functions that return one (such as
    `ddb._client`), names assigned from either, and chained calls."""
    out: set[tuple[str, str]] = set()
    for rel in files:
        path = root / rel
        out |= _module_calls(ast.parse(path.read_text(), filename=str(path)))
    return out
