"""Static checks on scripts/smoke/.

These scripts need a live server or database, so the suite cannot execute them —
which is exactly why they rot unnoticed. Two of them had been importing
`backend.tools` and `backend.services`, packages that stopped existing at some
reorg, and four could not resolve `backend` at all once run from their own
directory rather than as pytest modules.

The checks here are static: they parse each script and verify the things that
would make it fail on the very first line, without importing anything.
"""
import ast
import os
import pathlib

import pytest

SMOKE_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "smoke"
SCRIPTS = sorted(SMOKE_DIR.glob("*.py"))


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _backend_imports(tree: ast.Module) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("backend"):
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith("backend")]
    return found


def _adds_repo_root_to_path(tree: ast.Module, script: pathlib.Path) -> bool:
    """Whether a sys.path modification actually resolves to the repo root.

    Checking merely that *some* sys.path call exists is not enough: these
    scripts arrived carrying inserts left over from their previous location
    (``.../'backend'``, ``.../'..'``) that point somewhere useless. The path
    expression is evaluated with ``__file__`` bound to the real script so the
    check reflects what Python would actually do.
    """
    repo_root = SMOKE_DIR.parents[1].resolve()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("insert", "append"):
            continue
        if not ast.unparse(node.func.value).endswith("sys.path"):
            continue

        expr = node.args[-1] if node.args else None
        if expr is None:
            continue
        try:
            value = eval(  # noqa: S307 - first-party source, evaluated in a test
                ast.unparse(expr),
                {"__file__": str(script), "pathlib": pathlib, "os": os, "Path": pathlib.Path},
            )
        except Exception:
            continue
        if pathlib.Path(str(value)).resolve() == repo_root:
            return True
    return False


def test_there_are_smoke_scripts_to_check():
    assert SCRIPTS, f"expected scripts in {SMOKE_DIR}"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_parses(script):
    _tree(script)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_importing_backend_can_find_it(script):
    """Run directly, sys.path[0] is scripts/smoke — not the repo root."""
    tree = _tree(script)
    if not _backend_imports(tree):
        pytest.skip("does not import backend")

    assert _adds_repo_root_to_path(tree, script), (
        f"{script.name} imports backend.* but never puts the repo root on sys.path, "
        "so `python scripts/smoke/<name>.py` fails with ModuleNotFoundError"
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_imported_backend_modules_exist(script):
    """Catch references to packages that were renamed away."""
    repo_root = SMOKE_DIR.parents[1]
    for module in _backend_imports(_tree(script)):
        parts = module.split(".")
        as_module = repo_root.joinpath(*parts).with_suffix(".py")
        as_package = repo_root.joinpath(*parts, "__init__.py")
        assert as_module.exists() or as_package.exists(), (
            f"{script.name} imports {module!r}, which does not exist"
        )
