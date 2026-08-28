"""Run the documentation checker as part of the suite.

It is also a standalone script (`python scripts/check_docs.py`), but a doc
inconsistency should turn the same build red as a failing assertion — the point
of writing it was that nothing else notices when the docs drift.

Each check gets its own test so a failure names which claim broke.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts/check_docs.py")
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)


def _run(check) -> list[str]:
    errors: list[str] = []
    check(errors)
    return errors


@pytest.mark.parametrize("check", [
    check_docs.check_env_parity,
    check_docs.check_doc_links,
    check_docs.check_spec_index,
    check_docs.check_scoring_constants,
], ids=lambda c: c.__name__)
def test_docs_agree_with_code(check):
    errors = _run(check)
    assert not errors, "\n" + "\n".join(f"  - {e}" for e in errors)


def test_checker_notices_a_wrong_constant(tmp_path, monkeypatch):
    """A checker that cannot fail is decoration. Feed it a spec that lies."""
    fake = tmp_path / ".spec/specs/score-engine"
    fake.mkdir(parents=True)
    real = (ROOT / ".spec/specs/score-engine/spec.md").read_text(encoding="utf-8")
    (fake / "spec.md").write_text(real.replace('Pro 14"×1.18', 'Pro 14"×1.30'), encoding="utf-8")

    for name in ("src/utils", "src/calculator"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    for name in ("src/utils/benchmark_db.py", "src/calculator/score_engine.py"):
        (tmp_path / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    errors = _run(check_docs.check_scoring_constants)
    assert any("form multipliers" in e for e in errors), errors
