#!/usr/bin/env python3
"""N3XORA Phase 10 — execution-environment preflight.

The command is intentionally strict: it does not silently accept another Vina
version, a missing pocket engine, or a missing Python dependency when the real
campaign is supposed to be executed.
"""
from __future__ import annotations

import argparse
import importlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

EXPECTED_VINA = "1.2.5"
REQUIRED_MODULES = ("numpy", "rdkit")


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _version(binary: str, args: list[str]) -> str | None:
    path = shutil.which(binary)
    if not path:
        return None
    rc, text = _run([path, *args])
    if rc != 0 and not text:
        return None
    return text.splitlines()[0] if text else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--vina", default="vina")
    ap.add_argument("--fpocket", default="fpocket")
    ap.add_argument("--allow-vina-version", default=EXPECTED_VINA)
    args = ap.parse_args()

    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "executables": {},
        "python_packages": {},
        "status": "PASS",
        "problems": [],
    }

    for name, binary, version_args in (
        ("vina", args.vina, ["--version"]),
        ("fpocket", args.fpocket, ["-h"]),
    ):
        path = shutil.which(binary)
        result["executables"][name] = {
            "path": path,
            "version_probe": _version(binary, version_args),
            "available": bool(path),
        }
        if not path:
            result["problems"].append(f"{name} executable not found: {binary}")

    try:
        importlib.import_module("numpy")
        result["python_packages"]["numpy"] = __import__("numpy").__version__
    except Exception as exc:
        result["problems"].append(f"numpy unavailable: {exc}")

    try:
        rdkit = importlib.import_module("rdkit")
        result["python_packages"]["rdkit"] = getattr(rdkit, "__version__", "unknown")
    except Exception as exc:
        result["problems"].append(f"rdkit unavailable: {exc}")

    vina_probe = result["executables"]["vina"]["version_probe"] or ""
    if result["executables"]["vina"]["available"]:
        expected = args.allow_vina_version
        # Official Vina binaries usually return a first line containing the version.
        if expected not in vina_probe:
            result["problems"].append(
                f"Vina version mismatch: expected {expected}, probe={vina_probe!r}"
            )

    if result["problems"]:
        result["status"] = "BLOCKED"

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Phase 10 preflight: {result['status']}")
        for key, item in result["executables"].items():
            print(f"- {key}: {item['path'] or 'MISSING'} | {item['version_probe'] or 'n/a'}")
        for key, value in result["python_packages"].items():
            print(f"- {key}: {value}")
        for problem in result["problems"]:
            print(f"BLOCK: {problem}", file=sys.stderr)

    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
