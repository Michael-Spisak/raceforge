"""The EV3 runs ev3dev-stretch with Python 3.5: reject syntax newer than that in ev3_side/."""

import ast
from pathlib import Path

import pytest

EV3_SIDE = Path(__file__).parents[2] / "ev3_side"
FILES = sorted(EV3_SIDE.rglob("*.py"))

# Node types that do not exist in Python 3.5.
FORBIDDEN: dict[type[ast.AST], str] = {
    ast.JoinedStr: "f-string (3.6)",
    ast.AnnAssign: "variable annotation (3.6)",
    ast.NamedExpr: "walrus operator (3.8)",
    ast.Match: "match statement (3.10)",
}
FORBIDDEN_IMPORTS = {"dataclasses", "contextvars", "__future__"}


def test_there_are_files() -> None:
    assert len(FILES) >= 4


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_python35_subset(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems = []
    for node in ast.walk(tree):
        for t, what in FORBIDDEN.items():
            if isinstance(node, t):
                problems.append(f"line {getattr(node, 'lineno', 0)}: {what}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            for n in names:
                if n and n.split(".")[0] in FORBIDDEN_IMPORTS:
                    problems.append(f"line {node.lineno}: import {n}")
        if isinstance(node, ast.arguments) and node.posonlyargs:
            problems.append("positional-only parameters (3.8)")
    # Underscores in numeric literals are 3.6+ and invisible in the AST: check the source.
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        for tok in code.replace("(", " ").replace(")", " ").replace(",", " ").split():
            if tok[:1].isdigit() and "_" in tok:
                problems.append(f"line {i}: underscore in number literal (3.6)")
    assert not problems, "\n".join(problems)
