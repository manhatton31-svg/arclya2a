"""Add terms_accepted to agent registration payloads in tests."""
from __future__ import annotations

import ast
import pathlib


REGISTER_PATH = "/agents/register"


def add_import(text: str) -> str:
    if "registration_payload" in text:
        return text
    if "from tests.agent_helpers import" in text:
        return text.replace(
            "from tests.agent_helpers import ",
            "from tests.agent_helpers import registration_payload, ",
            1,
        )
    lines = text.splitlines()
    insert = 0
    for i, line in enumerate(lines):
        if line.startswith(("import ", "from ")):
            insert = i + 1
    lines.insert(insert, "from tests.agent_helpers import registration_payload")
    return "\n".join(lines)


class RegisterCallTransformer(ast.NodeTransformer):
    def visit_Call(self, node: ast.Call):  # noqa: N802
        self.generic_visit(node)
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "client"
            and node.func.attr == "post"
        ):
            return node
        if len(node.args) < 1 or not (
            isinstance(node.args[0], ast.Constant) and node.args[0].value == REGISTER_PATH
        ):
            return node
        json_kw = None
        for kw in node.keywords:
            if kw.arg == "json":
                json_kw = kw
                break
        if json_kw is None:
            return node
        value = json_kw.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "registration_payload":
            return node
        if isinstance(value, ast.Dict):
            keys = {k.value if isinstance(k, ast.Constant) else None for k in value.keys}
            if "terms_accepted" in keys:
                return node
            new_keys = list(value.keys) + [ast.Constant(value="terms_accepted")]
            new_values = list(value.values) + [ast.Constant(value=True)]
            json_kw.value = ast.Dict(keys=new_keys, values=new_values)
        return node


def transform_file(path: pathlib.Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if REGISTER_PATH not in source or path.name == "test_agent_terms_acceptance.py":
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    transformer = RegisterCallTransformer()
    new_tree = transformer.visit(tree)
    ast.fix_missing_locations(new_tree)
    new_source = ast.unparse(new_tree)
    new_source = add_import(new_source)
    if new_source != source:
        path.write_text(new_source + "\n", encoding="utf-8")
        return True
    return False


def main() -> None:
    for path in sorted(pathlib.Path("tests").glob("test_agent*.py")):
        if transform_file(path):
            print(path.name)


if __name__ == "__main__":
    main()