"""Repair test files broken by automated registration_payload conversion."""
from __future__ import annotations

import pathlib
import re


REPLACEMENTS = [
    (r'json=\{agent_name=\s*([^,}]+),\s*description=\s*([^}]+)\}', r'json=registration_payload(agent_name=\1, description=\2)'),
    (r'json=\{agent_name=\s*([^,}]+),\s*email=\s*([^}]+)\}', r'json=registration_payload(agent_name=\1, email=\2)'),
    (r'json=\{agent_name=\s*([^,}]+),\s*capabilities=\s*([^}]+)\}', r'json=registration_payload(agent_name=\1, capabilities=\2)'),
    (r'json=\{agent_name=\s*([^,}]+)\}', r'json=registration_payload(agent_name=\1)'),
    (r'json=\{description=\s*"missing name"\}', r'json={"description": "missing name"}'),
    (r'details=\{agent_name=\s*"Test"\)', r'details={"agent_name": "Test"}'),
    (r'json=\{description=\s*"Updated description"\)', r'json={"description": "Updated description"}'),
    (r'agent_name=\s*"RecruitBot"', r'"agent_name": "RecruitBot"'),
    (r'agent_name=\s*"Demo Agent"', r'"agent_name": "Demo Agent"'),
]


def repair(text: str) -> str:
    for pattern, repl in REPLACEMENTS:
        text = re.sub(pattern, repl, text)
    return text


def main() -> None:
    for path in sorted(pathlib.Path("tests").glob("test_agent*.py")):
        original = path.read_text(encoding="utf-8")
        fixed = repair(original)
        if fixed != original:
            path.write_text(fixed, encoding="utf-8")
            print(path.name)


if __name__ == "__main__":
    main()