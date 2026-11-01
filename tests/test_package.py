import tomllib
from pathlib import Path

import suss

REPO = Path(__file__).resolve().parent.parent
SRC_PACKAGE = REPO / "src" / "suss"


def test_suss_imports_from_src():
    assert suss.__file__ is not None, "suss imported as a namespace package, not from src/suss/"
    assert Path(suss.__file__).resolve().parent == SRC_PACKAGE, (
        f"suss imported from {suss.__file__}, not from {SRC_PACKAGE}"
    )


def test_the_modal_extra_installs_harbors_modal_extra_at_the_harbor_suss_pins():
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    [harbor] = [d for d in project["dependencies"] if d.startswith("harbor")]
    version = harbor.removeprefix("harbor")

    extra = project.get("optional-dependencies", {}).get("modal")

    assert extra == [f"harbor[modal]{version}"], (
        f"the modal extra is {extra!r}, so it may install a Harbor other than {harbor}"
    )
