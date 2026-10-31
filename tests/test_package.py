from pathlib import Path

import suss

SRC_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "suss"


def test_suss_imports_from_src():
    assert suss.__file__ is not None, "suss imported as a namespace package, not from src/suss/"
    assert Path(suss.__file__).resolve().parent == SRC_PACKAGE, (
        f"suss imported from {suss.__file__}, not from {SRC_PACKAGE}"
    )
