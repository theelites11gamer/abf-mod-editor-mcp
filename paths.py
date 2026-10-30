"""Where things live on this machine. Shared by server.py and install.py.

Nothing here is hardcoded to one PC: the Mod Editor is found through the Epic Games
Launcher's install manifests, the Documents folder through the Windows shell, and the
bridge folder lives under %LOCALAPPDATA% so the editor-side hook and the server agree on it
without either knowing where the other is installed.
"""
import ctypes
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK_MARKER = "abf-mod-editor-mcp"
EDITOR_DISPLAY_NAME = "Abiotic Factor Mod Editor"
EDITOR_EXE_REL = Path("AbioticFactor") / "Binaries" / "Win64" / "AbioticFactorEditorCooked-Win64-Test.exe"


def documents_dir():
    """The user's Documents folder as Windows (and Unreal) see it, even when redirected."""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:   # CSIDL_PERSONAL
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Documents"


def local_app_data():
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))


def bridge_dir():
    """Request/response folder for the file bridge; the editor hook computes the same path."""
    return local_app_data() / "abf-mod-editor-mcp" / "bridge"


def saved_dir():
    """The cooked editor's Saved folder (logs, config)."""
    return Path(os.environ.get("ABF_EDITOR_SAVED") or (local_app_data() / "AbioticFactor" / "Saved"))


def hook_dir():
    """Unreal runs every init_unreal.py in this folder at editor startup."""
    return documents_dir() / "UnrealEngine" / "Python"


def hook_installed():
    init = hook_dir() / "init_unreal.py"
    try:
        return HOOK_MARKER in init.read_text(encoding="utf-8", errors="replace") and (hook_dir() / "abf_mcp_startup.py").is_file()
    except OSError:
        return False


def _valid_root(p):
    p = Path(p)
    return p if (p / EDITOR_EXE_REL).is_file() else None


def find_editor_root(explain=False):
    """Locate the Mod Editor install. Order: ABF_EDITOR_ROOT, config.json next to this file,
    the Epic Games Launcher manifests, then a wildcard under Program Files\\Epic Games."""
    tried = []
    env = os.environ.get("ABF_EDITOR_ROOT")
    if env:
        tried.append(("ABF_EDITOR_ROOT", env))
        if _valid_root(env):
            return (Path(env), "ABF_EDITOR_ROOT") if explain else Path(env)
    cfg = HERE / "config.json"
    if cfg.is_file():
        try:
            root = json.loads(cfg.read_text(encoding="utf-8-sig")).get("editor_root")
            tried.append(("config.json", root))
            if root and _valid_root(root):
                return (Path(root), "config.json") if explain else Path(root)
        except ValueError:
            tried.append(("config.json", "not valid JSON"))
    manifests = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    for item in sorted(manifests.glob("*.item")) if manifests.is_dir() else []:
        try:
            m = json.loads(item.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            continue
        if m.get("DisplayName") == EDITOR_DISPLAY_NAME and m.get("InstallLocation"):
            tried.append(("Epic manifest", m["InstallLocation"]))
            if _valid_root(m["InstallLocation"]):
                return (Path(m["InstallLocation"]), "Epic Games Launcher manifest") if explain else Path(m["InstallLocation"])
    for pf in {os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"), r"C:\Program Files"}:
        if not pf:
            continue
        for d in sorted(Path(pf).glob("Epic Games/AbioticFactorModEdit*")):
            tried.append(("Program Files wildcard", str(d)))
            if _valid_root(d):
                return (d, "Program Files wildcard") if explain else d
    return (None, tried) if explain else None


def editor_exe(root=None):
    root = root or find_editor_root()
    return (root / EDITOR_EXE_REL) if root else None


def version_tag(engine_version):
    """'5.4.4-1050002+++DF+ABF' -> '1050002'; the editor build number, for logs."""
    m = re.search(r"-(\d+)\+", engine_version or "")
    return m.group(1) if m else None
