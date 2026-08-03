from __future__ import annotations

from aws_reference_agent.agent import build_source_options
from aws_reference_agent.runner import ReferenceRunner


class _StubSource:
    def __init__(self, name: str) -> None:
        self.name = name

    def list_concepts(self):
        return []


def test_should_load_glue_reference_instruction_by_default():
    opts = build_source_options()
    assert "SQL snippets" in opts.system_prompt
    assert "cubejs-api/v1/load" not in opts.system_prompt


def test_should_load_cube_instruction_when_requested():
    opts = build_source_options(instruction_file="cube_reference_instruction.md")
    assert "cubejs-api/v1/load" in opts.system_prompt
    assert "not a physical SQL table" in opts.system_prompt


def test_should_select_cube_instruction_for_cube_source(tmp_path):
    runner = ReferenceRunner(source=_StubSource("cube"), bundle_root=tmp_path)
    assert "cubejs-api/v1/load" in runner._source_options.system_prompt


def test_should_select_glue_instruction_for_non_cube_source(tmp_path):
    runner = ReferenceRunner(source=_StubSource("glue"), bundle_root=tmp_path)
    assert "cubejs-api/v1/load" not in runner._source_options.system_prompt
    assert "SQL snippets" in runner._source_options.system_prompt
