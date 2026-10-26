"""Validate the installed host's optional diagnostics serializer without booting it."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_delivery import channel


def test_patched_host_serializes_plugin_diagnostics(tmp_path):
    spec = importlib.util.find_spec("octop")
    source = Path(spec.origin).parent / "infra/gateway/gateway.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    method = next(
        item
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for item in cls.body
        if isinstance(item, ast.FunctionDef) and item.name == "runtime_status_to_dict"
    )
    if "diagnostics" not in ast.unparse(method):
        pytest.skip("Optional Octop runtime diagnostics patch is not applied")
    namespace = {"DEFAULT_LOCALE": "zh"}
    isolated = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            method,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(isolated), str(source), "exec"), namespace)
    obj = channel(tmp_path)
    try:
        status = SimpleNamespace(connected=True, reason=None, detail=None, updated_at=1)
        host = SimpleNamespace(
            get_runtime_status=lambda _: status,
            _channel_manager=SimpleNamespace(get_channel=lambda _: obj),
        )
        result = namespace["runtime_status_to_dict"](host, "instance")
        assert result["diagnostics"] == obj.runtime_status
        assert result["connected"] is True
        assert result["error"] is None
    finally:
        obj._inbox.close()
