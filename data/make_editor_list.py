"""Regenerate data/editor-<version>-files.json.gz from the Mod Editor install.

    python data/make_editor_list.py

The editor ships two manifests of every file it installs (Manifest_UFSFiles_Win64.txt and
Manifest_NonUFSFiles_Win64.txt, tab-separated: path, timestamp). This writes their union as a
sorted JSON list, gzipped, named after the editor version in Engine/Build/Build.version.

check_upload uses it, together with retail-<version>-files.json.gz, to tell which /Game assets a
mod references exist only in the editor (those fail the mod.io cloud cook). The retail list was
produced by listing the retail game's pak contents with a CUE4Parse-based tool; regenerate it
the same way after a game update, keeping the "AbioticFactor/Content/..." path form.
"""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths  # noqa: E402


def main():
    root = paths.find_editor_root()
    if not root:
        print("Mod Editor install not found (set ABF_EDITOR_ROOT).")
        return 1
    files = set()
    for name in ("Manifest_UFSFiles_Win64.txt", "Manifest_NonUFSFiles_Win64.txt"):
        for line in (root / name).read_text(encoding="utf-8", errors="replace").splitlines():
            path = line.split("\t")[0].strip()
            if path:
                files.add(path)
    version = "unknown"
    try:
        bv = json.loads((root / "Engine" / "Build" / "Build.version").read_text(encoding="utf-8-sig"))
        version = f"{bv.get('MajorVersion')}.{bv.get('MinorVersion')}.{bv.get('PatchVersion')}-{bv.get('Changelist')}"
    except (OSError, ValueError):
        pass
    out = Path(__file__).resolve().parent / f"editor-{version}-files.json.gz"
    out.write_bytes(gzip.compress(json.dumps(sorted(files), indent=0).encode("utf-8")))
    print(f"{len(files)} files -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
