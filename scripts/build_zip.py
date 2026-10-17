"""Build a reproducible plugin ZIP from an explicit distribution allowlist."""

from pathlib import Path
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "main.py",
    "plugin.yaml",
    "README.md",
    "LICENSE",
    "NOTICE",
    "CHANGELOG.md",
    "pyproject.toml",
    "docs/PROVENANCE.md",
    "octo_channel_impl/__init__.py",
    "octo_channel_impl/api.py",
    "octo_channel_impl/channel.py",
    "octo_channel_impl/network.py",
    "octo_channel_impl/socket.py",
    "octo_channel_impl/state.py",
)


def build(output: Path | None = None) -> Path:
    output = output or ROOT / "dist/octop-channel-octo-0.1.0.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            path = ROOT / name
            if path.is_symlink() or path.resolve() != path or not path.is_file():
                raise ValueError(f"Unsafe or missing distribution file: {name}")
            info = zipfile.ZipInfo(f"octop-channel-octo/{name}", (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    print(f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output}")
    return output


if __name__ == "__main__":
    build()
