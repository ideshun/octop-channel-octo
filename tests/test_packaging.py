import importlib.util
from pathlib import Path
import zipfile


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
