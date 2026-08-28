"""Fail the build when the documentation and the code disagree.

    python scripts/check_docs.py

Docs rot silently. Nothing breaks, no test goes red, and the next person
follows an instruction that stopped being true three commits ago. These are
the claims cheap enough to verify mechanically:

  1. every environment variable the code reads is in .env.example, and every
     key in .env.example is actually read by something
  2. every relative link in the Markdown resolves to a file that exists
  3. the spec files CLAUDE.md advertises as written are in fact written
  4. the scoring constants quoted in score-engine/spec.md still match the
     numbers in src/calculator/score_engine.py

Check 4 is the one that matters most: that table is what a reader trusts when
deciding whether a score looks right, and those weights were already tuned once.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Injected by the CI runner or the platform, never read from a .env file.
ENV_EXEMPT = {
    "GITHUB_ACTIONS",      # set by GitHub Actions itself
    "SHOPEE_STATE_B64",    # a repository secret, too large for .env
}

# Vendored tool skills and archived specs are not ours to keep consistent.
DOC_EXCLUDE = (".claude/", ".spec/archive/", "venv/", "design/")

CODE_DIRS = ("src", "api", "scripts")


def _code_files() -> list[Path]:
    files = [p for d in CODE_DIRS for p in (ROOT / d).rglob("*.py")]
    files.append(ROOT / "streamlit_app.py")
    # This file quotes os.getenv in its own docstring.
    files = [p for p in files if p.name != Path(__file__).name]
    return [p for p in files if p.exists()]


def _doc_files() -> list[Path]:
    out = []
    for p in ROOT.rglob("*.md"):
        rel = p.relative_to(ROOT).as_posix()
        if not rel.startswith(DOC_EXCLUDE):
            out.append(p)
    return sorted(out)


def check_env_parity(errors: list[str]) -> None:
    """os.getenv("X") on one side, .env.example on the other."""
    pattern = re.compile(
        r'(?:os\.getenv|os\.environ\.get|_read_secret)\(\s*["\']([A-Z][A-Z_0-9]*)["\']'
        r'|os\.environ\[\s*["\']([A-Z][A-Z_0-9]*)["\']'
    )
    used: dict[str, str] = {}
    for path in _code_files():
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            name = m.group(1) or m.group(2)
            used.setdefault(name, path.relative_to(ROOT).as_posix())

    example = ROOT / ".env.example"
    declared = set(re.findall(
        r"^\s*#?\s*([A-Z][A-Z_0-9]*)\s*=", example.read_text(encoding="utf-8"), re.M
    ))

    for name in sorted(set(used) - declared - ENV_EXEMPT):
        errors.append(f".env.example is missing {name}, read by {used[name]}")

    # A key nobody reads is worse than a missing one: it looks configurable.
    for name in sorted(declared - set(used) - ENV_EXEMPT):
        errors.append(f".env.example declares {name}, but no code reads it")


def check_doc_links(errors: list[str]) -> None:
    """Relative Markdown links must point at something that exists."""
    link = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
    for path in _doc_files():
        rel_doc = path.relative_to(ROOT).as_posix()
        for m in link.finditer(path.read_text(encoding="utf-8")):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            if not (path.parent / target).exists():
                errors.append(f"{rel_doc} links to {target}, which does not exist")


def check_spec_index(errors: list[str]) -> None:
    """CLAUDE.md's table marks each spec written or not. Both claims are checkable."""
    row = re.compile(r"\|[^|]*\|\s*`([^`]+/spec\.md)`\s*\|\s*(\S+)")
    for m in row.finditer((ROOT / "CLAUDE.md").read_text(encoding="utf-8")):
        rel, status = m.group(1), m.group(2)
        exists = (ROOT / ".spec" / "specs" / rel).exists() or (ROOT / rel).exists()
        if status.startswith("✅") and not exists:
            errors.append(f"CLAUDE.md marks {rel} written, but the file is missing")
        elif status.startswith("⬜") and exists:
            errors.append(f"CLAUDE.md marks {rel} unwritten, but it exists - update the table")


def _constants() -> dict:
    """Read the module constants and dataclass defaults without importing them.

    score_engine imports pydantic models; parsing the source keeps this script
    runnable with nothing but the standard library.
    """
    tree = ast.parse((ROOT / "src/calculator/score_engine.py").read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        target = value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        if target and value is not None:
            try:
                out[target] = ast.literal_eval(value)
            except ValueError:
                pass
    return out


def check_scoring_constants(errors: list[str]) -> None:
    """The parameter table in score-engine/spec.md quotes real numbers. Hold it to them."""
    spec = (ROOT / ".spec/specs/score-engine/spec.md").read_text(encoding="utf-8")
    c = _constants()

    def row(label: str):
        m = re.search(r"^\|\s*" + label + r"\s*\|(.+)$", spec, re.M)
        return m.group(1) if m else None

    def nums(text: str) -> list[float]:
        return [float(x) for x in re.findall(r"×([\d.]+)", text)]

    # "每滿一年 x0.9"
    r = row("年份折舊")
    if r is None:
        errors.append("score-engine/spec.md: no depreciation row - the checker needs updating")
    else:
        want = round(1 - c["DEPRECIATION_RATE"], 4)
        if nums(r) != [want]:
            errors.append(
                f"score-engine/spec.md quotes depreciation {nums(r)}, code computes x{want}"
            )

    # RAM and SSD state a threshold and a multiplier in the same row.
    for label, thr_key, mult_key in (
        ("RAM 加成", "RAM_BONUS_THRESHOLD_GB", "ram_multiplier"),
        ("SSD 加成", "SSD_BONUS_THRESHOLD_GB", "ssd_multiplier"),
    ):
        r = row(label)
        if r is None:
            errors.append(f"score-engine/spec.md: no {label} row - the checker needs updating")
            continue
        m = re.search(r"≥\s*([\d.]+)\s*(GB|TB)", r)
        if not m:
            errors.append(f"score-engine/spec.md: the {label} row states no threshold")
        else:
            stated = float(m.group(1)) * (1024 if m.group(2) == "TB" else 1)
            if stated != c[thr_key]:
                errors.append(
                    f"score-engine/spec.md says {label} applies at {m.group(1)}{m.group(2)}, "
                    f"code uses {c[thr_key]} GB"
                )
        if nums(r) != [c[mult_key]]:
            errors.append(
                f"score-engine/spec.md quotes {label} {nums(r)}, code uses x{c[mult_key]}"
            )

    # Form factors, in the order the row lists them.
    r = row("形態加成")
    if r is None:
        errors.append("score-engine/spec.md: no form-factor row - the checker needs updating")
    else:
        want = [c[k] for k in
                ("form_air13", "form_air15", "form_pro13", "form_pro14", "form_pro16")]
        if nums(r) != want:
            errors.append(
                f"score-engine/spec.md quotes form multipliers {nums(r)}, code uses {want} "
                f"(Air 13, Air 15, Pro 13, Pro 14, Pro 16)"
            )

    # The unknown-chip fallback. Anchored on get_benchmark() rather than on any
    # "回傳 N" in the file: a looser pattern read "回傳 13 / 14 / 15 / 16" out of
    # the screen-size section and reported a mismatch that did not exist. A
    # checker that cries wolf is the one failure mode this script cannot afford.
    bench = (ROOT / "src/utils/benchmark_db.py").read_text(encoding="utf-8")
    m = re.search(r"return (\d+)\s+# Default score", bench)
    if m:
        for quoted in set(re.findall(r"get_benchmark\(\)[^0-9\n]{0,12}(\d+)", spec)):
            if quoted != m.group(1):
                errors.append(
                    f"score-engine/spec.md quotes an unknown-chip fallback of {quoted}, "
                    f"benchmark_db.py returns {m.group(1)}"
                )


def main() -> int:
    errors: list[str] = []
    for check in (check_env_parity, check_doc_links, check_spec_index, check_scoring_constants):
        check(errors)

    if errors:
        print(f"{len(errors)} documentation inconsistencies:\n")
        for e in errors:
            print(f"  - {e}")
        print("\nFix the docs or the code, whichever is wrong.")
        return 1
    print("docs and code agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
