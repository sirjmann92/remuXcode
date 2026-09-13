#!/usr/bin/env python3
"""Fail if the Biome version is pinned inconsistently across the repo.

Biome's version is declared in four places that must agree:

  1. frontend/package.json      "@biomejs/biome" (devDependencies)
  2. frontend/package-lock.json the resolved lock entry
  3. .pre-commit-config.yaml    the biomejs/pre-commit `rev` (vX.Y.Z)
  4. .pre-commit-config.yaml    `additional_dependencies` npm pin
  5. biome.json                 the "$schema" URL

Only 3 and 4 gate CI: the lint hook runs its own pinned copy of Biome in an
isolated environment and never touches frontend/node_modules. `npm run lint`
uses 1/2 but CI never invokes it. So when these drift apart nothing fails --
the gate just silently enforces an older ruleset than the repo advertises.
That is exactly how this repo ended up enforcing 2.2.4 against a declared
2.5.11, nine minor releases apart (issue #81).

Dependabot bumps 1 and 2 on its own and cannot touch 3, 4 or 5, so this
check is what turns that silent divergence into a loud, actionable failure
on the very first run after a bump.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent

PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"
PACKAGE_LOCK = REPO_ROOT / "frontend" / "package-lock.json"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
BIOME_CONFIG = REPO_ROOT / "biome.json"

PACKAGE = "@biomejs/biome"


def _declared_version() -> str | None:
    """The version in frontend/package.json — the source of truth."""
    data = json.loads(PACKAGE_JSON.read_text())
    for section in ("devDependencies", "dependencies"):
        version = data.get(section, {}).get(PACKAGE)
        if version:
            # Tolerate (but do not require) a range prefix such as ^ or ~.
            return version.lstrip("^~")
    return None


def _locked_version() -> str | None:
    """The resolved version in frontend/package-lock.json."""
    if not PACKAGE_LOCK.is_file():
        return None
    data = json.loads(PACKAGE_LOCK.read_text())
    entry = data.get("packages", {}).get(f"node_modules/{PACKAGE}", {})
    return entry.get("version")


def _hook_versions() -> tuple[str | None, str | None]:
    """The biomejs/pre-commit `rev` and its additional_dependencies pin."""
    text = PRE_COMMIT_CONFIG.read_text()
    block = re.search(
        r"- repo:\s*https://github\.com/biomejs/pre-commit\s*\n(.*?)(?=\n\s*- repo:|\nci:|\Z)",
        text,
        re.DOTALL,
    )
    if block is None:
        return None, None
    body = block.group(1)
    rev = re.search(r"^\s*rev:\s*['\"]?v?([0-9][^'\"\s]*)['\"]?", body, re.MULTILINE)
    dep = re.search(rf"{re.escape(PACKAGE)}@([0-9][^'\"\s]*)", body)
    return (rev.group(1) if rev else None, dep.group(1) if dep else None)


def _schema_version() -> str | None:
    """The version embedded in biome.json's "$schema" URL."""
    if not BIOME_CONFIG.is_file():
        return None
    data = json.loads(BIOME_CONFIG.read_text())
    match = re.search(r"/schemas/([0-9][^/]*)/schema\.json", data.get("$schema", ""))
    return match.group(1) if match else None


def main() -> int:
    """Compare every pin against package.json and report any mismatch."""
    declared = _declared_version()
    if declared is None:
        print(f"ERROR: could not find {PACKAGE} in {PACKAGE_JSON}", file=sys.stderr)
        return 1

    hook_rev, hook_dep = _hook_versions()
    found = {
        "frontend/package-lock.json (resolved)": _locked_version(),
        ".pre-commit-config.yaml (rev)": hook_rev,
        ".pre-commit-config.yaml (additional_dependencies)": hook_dep,
        "biome.json ($schema)": _schema_version(),
    }

    mismatched = {where: v for where, v in found.items() if v is not None and v != declared}
    missing = [where for where, v in found.items() if v is None]

    if not mismatched and not missing:
        return 0

    print(
        f"Biome version pins disagree with frontend/package.json ({declared}):\n",
        file=sys.stderr,
    )
    for where, version in mismatched.items():
        print(f"  {where}: {version}", file=sys.stderr)
    for where in missing:
        print(f"  {where}: could not be read", file=sys.stderr)
    print(
        "\nThe pre-commit rev/additional_dependencies pair is what actually gates CI,"
        "\nso leaving these out of step means the lint gate silently enforces a"
        "\ndifferent Biome ruleset than this repo declares. Update every pin above"
        f"\nto {declared} (rev takes a leading 'v'), then re-run.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
