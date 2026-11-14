import ast
import tomllib
from pathlib import Path

import swe_drill

REPO = Path(__file__).resolve().parent.parent
SRC_PACKAGE = REPO / "src" / "swe_drill"


def test_swe_drill_imports_from_src():
    assert swe_drill.__file__ is not None, "swe_drill imported as a namespace package, not from src/swe_drill/"
    assert Path(swe_drill.__file__).resolve().parent == SRC_PACKAGE, (
        f"swe_drill imported from {swe_drill.__file__}, not from {SRC_PACKAGE}"
    )


def test_the_modal_extra_installs_harbors_modal_extra_at_the_harbor_swe_drill_pins():
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    [harbor] = [d for d in project["dependencies"] if d.startswith("harbor")]
    version = harbor.removeprefix("harbor")

    extra = project.get("optional-dependencies", {}).get("modal")

    assert extra == [f"harbor[modal]{version}"], (
        f"the modal extra is {extra!r}, so it may install a Harbor other than {harbor}"
    )


def _module(path: Path) -> str:
    return ".".join(path.relative_to(REPO / "src").with_suffix("").parts).removesuffix(".__init__")


def _imported(path: Path) -> set[str]:
    """Every module `path` imports, a `from` import naming each of its names as a module too."""
    package = _module(path) if path.name == "__init__.py" else _module(path).rsplit(".", 1)[0]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                base = f"{parent}.{base}" if base else parent
            found.add(base)
            found |= {f"{base}.{alias.name}" for alias in node.names}
    return found


# Every module in agents/ but its __init__.py is an adapter, so a new one needs no entry here.
ADAPTERS = tuple(_module(path) for path in sorted((SRC_PACKAGE / "agents").glob("*.py")) if path.name != "__init__.py")


def test_no_core_module_imports_an_adapter():
    for path in sorted(SRC_PACKAGE.rglob("*.py")):
        module = _module(path)
        if module in ADAPTERS:
            continue
        reached = {name for name in _imported(path) if name.startswith(ADAPTERS)}
        assert not reached, (
            f"{module} imports {', '.join(sorted(reached))}, but only an adapter may import an adapter;"
            " the core loads an arm's class by its import path"
        )
