"""接入清单规则（docs/INTEGRATION_RULES.md）的可执行校验。"""
from __future__ import annotations

import textwrap

import pytest

from agent.contracts import TOOL_NAME_RE
from agent.manifest import ManifestError, load_manifest, load_manifests


def write(tmp_path, body: str):
    p = tmp_path / "manifest.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


BASE = """
manifest_version: 1
system: sample
title: 样例系统
base_url: http://localhost:9999
"""


def test_builtin_manifests_load(settings):
    manifests = load_manifests(settings.manifests_glob)
    assert {m.system for m in manifests} == {"fleet", "tms", "dispatch"}
    for m in manifests:
        assert m.skill_text, f"{m.system} 应提供 SKILL.md"
        for t in (m.api.expose if m.api else []):
            assert TOOL_NAME_RE.match(f"{m.system}__{t.tool}")


def test_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMPLE_URL", "http://example.test:1234")
    m = load_manifest(write(tmp_path, BASE.replace("http://localhost:9999", "${SAMPLE_URL:-http://x}")))
    assert m.base_url == "http://example.test:1234"


@pytest.mark.parametrize(
    "extra, rule",
    [
        (
            """
            api:
              openapi: http://localhost:9999/openapi.json
              expose:
                - {operation: applyX, tool: apply_x, risk: write, description: 提交某个变更到系统}
            """,
            "R3.1",
        ),
        (
            """
            api:
              openapi: http://localhost:9999/openapi.json
              expose:
                - {operation: getX, tool: GetX, description: 查询某个对象的详情}
            """,
            "R1.2",
        ),
        (
            """
            api:
              openapi: http://localhost:9999/openapi.json
              expose:
                - {operation: getX, tool: get_x, description: 短}
            """,
            "R6.1",
        ),
        ("crs: EPSG:3857", "R7.1"),
        ("color: red", "RRGGBB"),
        ("ui_bridge: {enabled: true}", "R5.2"),
        ("auth: {mode: token_exchange}", "R8.1"),
        (
            """
            api:
              openapi: http://localhost:9999/openapi.json
              expose:
                - {operation: previewX, tool: preview_x, risk: read, pairs_with: apply_x, description: 预览变更，不落库}
                - {operation: applyX, tool: apply_x, risk: write, description: 提交变更到系统落库,
                   params: {region: {description: 区域}}}
            """,
            "R3.2",
        ),
    ],
)
def test_rule_violations(tmp_path, extra, rule):
    with pytest.raises(ManifestError) as e:
        load_manifest(write(tmp_path, BASE + textwrap.dedent(extra)))
    assert rule in str(e.value)


def test_preview_apply_pair_ok(tmp_path):
    m = load_manifest(
        write(
            tmp_path,
            BASE
            + """
api:
  openapi: http://localhost:9999/openapi.json
  expose:
    - {operation: previewX, tool: preview_x, pairs_with: apply_x, description: 预览变更，不落库}
    - {operation: applyX, tool: apply_x, risk: write, description: 提交变更到系统落库}
""",
        )
    )
    assert m.api_tool("preview_x").pairs_with == "apply_x"
