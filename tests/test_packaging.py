import importlib.util
from pathlib import Path
import zipfile
import os
import subprocess
import sys


def test_zip_is_reproducible_and_excludes_unlisted_files(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "build_zip", root / "scripts/build_zip.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    first = builder.build(tmp_path / "a.zip")
    second = builder.build(tmp_path / "b.zip")
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert len(names) == len(builder.FILES)
        assert all(name.startswith("octop-channel-octo/") for name in names)
        assert not any(".git" in n or ".env" in n or "sqlite" in n for n in names)
        assert "octop-channel-octo/plugin.yaml" in names
        assert "octop-channel-octo/LICENSE" in names


def test_built_zip_loads_with_real_host(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "zip_builder", root / "scripts/build_zip.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    archive_path = builder.build(tmp_path / "plugin.zip")
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(tmp_path / "extracted")
    installed = tmp_path / "extracted/octop-channel-octo"
    code = """
from pathlib import Path
import sys
from octop_harness.plugins.loader import load_plugin_dir
from octop_harness.plugins.registry import PluginRegistry
from octop.infra.gateway.plugin_channels import apply_plugin_channels
plugin = load_plugin_dir(Path(sys.argv[1]), install_deps=False)
import yaml
manifest = yaml.safe_load((Path(sys.argv[1]) / "plugin.yaml").read_text(encoding="utf-8"))
assert plugin.manifest.version == manifest["version"]
assert any(info.kind == "octo" for info in apply_plugin_channels(registry=PluginRegistry()))
assert (Path(sys.argv[1]) / "scripts/state_admin.py").is_file()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(installed)],
        cwd=tmp_path,
        env=dict(os.environ, OCTOP_HOME=str(tmp_path / "host-home")),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
