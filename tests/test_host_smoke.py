"""Real host integration runs only in a disposable subprocess and OCTOP_HOME."""

import os
from pathlib import Path
import subprocess
import sys


def test_isolated_installed_host_loader_and_router(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = """
import importlib.metadata as metadata
from pathlib import Path
import sys
from octop_harness.plugins.loader import load_plugin_dir
from octop_harness.plugins.context import PluginContext
from octop_harness.plugins.registry import PluginRegistry
from octop.infra.gateway.plugin_channels import apply_plugin_channels
from octop_gateway.channel import BaseChannel
from octop_gateway.channels import unregister_channel_kind
assert callable(PluginContext.channel)
loaded = load_plugin_dir(Path(sys.argv[1]), install_deps=False)
assert loaded.manifest.kind == "channel"
reg = loaded.channels[0]
assert reg.kind == "octo" and issubclass(reg.channel_cls, BaseChannel)
assert all("name" in field and "label" in field for field in reg.fields)
infos = apply_plugin_channels(registry=PluginRegistry())
assert any(info.kind == "octo" for info in infos)
unregister_channel_kind("octo", plugin_id=loaded.manifest.id)
for name in ("octop", "octop-gateway", "octop-harness"):
    print(name, metadata.version(name))
"""
    env = dict(os.environ, OCTOP_HOME=str(tmp_path / "host-home"))
    result = subprocess.run(
        [sys.executable, "-c", code, str(root)],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
