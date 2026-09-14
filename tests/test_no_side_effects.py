"""Honesty and no-side-effect gates (SPEC honesty rules and section 8.6).

* No module under ``restwert/`` imports a network, mail, cloud or shell
  library. ``subprocess`` is tolerated in ``cli.py`` only, where it starts
  Streamlit for the ``dashboard`` command and nothing else.
* No real DaaS provider, customer or employer name in any ``.py``, ``.md`` or
  ``.yaml`` file of the repository (``tests/fixtures/denylist.py`` holds them
  base64-encoded and decodes them here).
* ``GOVERNANCE_PRINCIPLE`` appears verbatim in ``README.md``.
* No em dash in the prose this repository ships (README, package, tests,
  config): the spec forbids them and they read as an AI tell.
"""

from __future__ import annotations

import re
from pathlib import Path

from restwert import GOVERNANCE_PRINCIPLE
from tests.fixtures.denylist import DENYLIST, find_denylisted

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "restwert"

FORBIDDEN_MODULES = ("smtplib", "requests", "httpx", "urllib.request", "boto3", "paramiko")
SHELL_MODULES = ("subprocess",)
EM_DASH = "\u2014"  # written as an escape so this file does not trip its own check
SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", ".pytest_cache", "node_modules", "build", "dist", "outputs", "data"}

_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+(?P<mod>[\w.]+)|from\s+(?P<frm>[\w.]+)\s+import\b)", re.MULTILINE
)


def _iter_files(suffixes: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        out.append(path)
    return sorted(out)


def _imported_modules(text: str) -> set[str]:
    mods: set[str] = set()
    for m in _IMPORT_RE.finditer(text):
        name = m.group("mod") or m.group("frm")
        if name:
            mods.add(name)
    return mods


def _hits(mods: set[str], forbidden: tuple[str, ...]) -> list[str]:
    hits = []
    for mod in mods:
        for bad in forbidden:
            if mod == bad or mod.startswith(bad + "."):
                hits.append(mod)
    return sorted(hits)


def test_package_has_files():
    assert (PACKAGE / "__init__.py").exists()
    assert len(list(PACKAGE.rglob("*.py"))) >= 5


def test_no_network_or_mail_imports_in_package():
    offenders: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = _hits(_imported_modules(text), FORBIDDEN_MODULES)
        if hits:
            offenders[str(path.relative_to(ROOT))] = hits
    assert not offenders, f"network/mail imports found: {offenders}"


def test_subprocess_only_in_cli():
    offenders: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "cli.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = _hits(_imported_modules(text), SHELL_MODULES)
        if hits:
            offenders[str(path.relative_to(ROOT))] = hits
    assert not offenders, f"subprocess imported outside cli.py: {offenders}"


def test_no_os_system_or_exec_outside_cli():
    pattern = re.compile(r"\bos\.(system|exec[lv]p?e?|popen|spawn\w*)\s*\(")
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "cli.py":
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"process spawn outside cli.py: {offenders}"


def test_denylist_is_decoded_and_non_empty():
    assert len(DENYLIST) >= 5
    assert all(isinstance(n, str) and n == n.lower() and len(n) >= 4 for n in DENYLIST)


def test_no_denylisted_names_in_repo_text():
    offenders: dict[str, list[str]] = {}
    for path in _iter_files((".py", ".md", ".yaml", ".yml", ".toml", ".txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = find_denylisted(text)
        if hits:
            offenders[str(path.relative_to(ROOT))] = hits
    assert not offenders, f"denylisted names found (case-insensitive): {offenders}"


def test_governance_principle_in_readme():
    readme = ROOT / "README.md"
    assert readme.exists(), "README.md missing"
    assert GOVERNANCE_PRINCIPLE in readme.read_text(encoding="utf-8")


def test_governance_principle_in_package_init():
    text = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    assert "GOVERNANCE_PRINCIPLE" in text
    assert GOVERNANCE_PRINCIPLE.rstrip(".") in text


def test_no_em_dash_in_shipped_prose():
    scan_roots = [ROOT / "README.md", PACKAGE, ROOT / "tests", ROOT / "config", ROOT / "pyproject.toml", ROOT / "requirements.txt"]
    offenders: list[str] = []
    for root in scan_roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if not path.is_file() or path.suffix not in (".py", ".md", ".yaml", ".yml", ".toml", ".txt"):
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if EM_DASH in text:
                line_no = text[: text.index(EM_DASH)].count("\n") + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line_no}")
    assert not offenders, f"em dash found in: {offenders}"


def test_no_utf8_bom_in_text_files():
    offenders = []
    for path in _iter_files((".py", ".md", ".yaml", ".yml", ".toml", ".txt")):
        with path.open("rb") as fh:
            if fh.read(3) == b"\xef\xbb\xbf":
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"UTF-8 BOM found in: {offenders}"
