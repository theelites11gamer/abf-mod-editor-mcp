"""Abiotic Factor Mod Editor MCP server (stdio).

Drives the official Abiotic Factor Mod Editor (cooked Unreal 5.4.4 editor). Two transports:

1. Python remote execution (preferred): UDP multicast discovery on 239.0.0.1:6766
   (loopback only), then a TCP command channel the editor connects back to.
2. File bridge (fallback): the editor-side hook runs a tick callback that executes
   request.json and writes response.json in %LOCALAPPDATA%/abf-mod-editor-mcp/bridge.

Both are switched on inside the editor at every launch by the hook that install.py puts in
Documents/UnrealEngine/Python, so nothing has to be typed into the editor.

No third-party packages. Run with any Python 3.9+:  python -I server.py
"""

import base64
import gzip
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wininput  # noqa: E402
import bpscan    # noqa: E402
import paths     # noqa: E402

SERVER_NAME = "abiotic-mod-editor"
SERVER_VERSION = "2.2.0"

EDITOR_ROOT = paths.find_editor_root()          # None when no Mod Editor install was found
EDITOR_EXE = paths.editor_exe(EDITOR_ROOT)
SAVED_DIR = paths.saved_dir()
EXPECTED_PROJECT = os.environ.get("ABF_EDITOR_PROJECT", "AbioticFactor")
BRIDGE = paths.bridge_dir()
DATA = HERE / "data"
SHOTS = HERE / "screenshots"
NOT_FOUND = ("No Mod Editor install found. Install it from the Epic Games Launcher, or set ABF_EDITOR_ROOT "
             "(or editor_root in config.json next to server.py) to its folder.")

MULTICAST_GROUP = ("239.0.0.1", 6766)
BIND_ADDRESS = "127.0.0.1"
MULTICAST_TTL = 0

OUTPUT_LIMIT = 60000          # characters of editor output returned per call
JSON_MARKER = "<<ABF_MCP_JSON>>"


def log(*args):
    print("[abiotic-mod-editor]", *args, file=sys.stderr, flush=True)


class RemoteError(RuntimeError):
    pass


class NotSent(RemoteError):
    """The command never reached the editor, so another transport may safely try it."""


# ---------------------------------------------------------------------------
# Transport 1: Python remote execution
# ---------------------------------------------------------------------------

class RemoteExecution:
    """Minimal client for Unreal's Python remote execution protocol (version 1).

    Each command opens its own command connection and releases it afterwards: the editor
    accepts one command connection at a time, and another remote-execution client (another
    MCP server, say) may hold it between our commands. The editor finishes a command before
    it handles anyone's open_connection, so a takeover can't cut a command off halfway.
    """

    def __init__(self):
        self.node_id = str(uuid.uuid4())
        self._nodes = {}            # node_id -> (data, last_seen)
        self._lock = threading.Lock()
        self._sock = None

    def _message(self, type_, dest=None, data=None):
        msg = {"version": 1, "magic": "ue_py", "type": type_, "source": self.node_id}
        if dest:
            msg["dest"] = dest
        if data:
            msg["data"] = data
        return json.dumps(msg, ensure_ascii=False).encode("utf-8")

    def _accepts(self, msg):
        return (isinstance(msg, dict) and msg.get("magic") == "ue_py"
                and msg.get("source") != self.node_id
                and (not msg.get("dest") or msg.get("dest") == self.node_id))

    def start(self):
        if self._sock:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((BIND_ADDRESS, MULTICAST_GROUP[1]))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, MULTICAST_TTL)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(BIND_ADDRESS))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     socket.inet_aton(MULTICAST_GROUP[0]) + socket.inet_aton(BIND_ADDRESS))
        s.settimeout(0.1)
        self._sock = s
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        last_ping = 0.0
        while True:
            try:
                data = self._sock.recv(65535)
            except socket.timeout:
                data = None
            except OSError:
                time.sleep(0.2)
                continue
            if data:
                try:
                    msg = json.loads(data.decode("utf-8"))
                except ValueError:
                    msg = None
                if self._accepts(msg) and msg.get("type") == "pong":
                    with self._lock:
                        self._nodes[msg["source"]] = (msg.get("data") or {}, time.time())
                continue
            now = time.time()
            if now - last_ping >= 1.0:
                last_ping = now
                try:
                    self._sock.sendto(self._message("ping"), MULTICAST_GROUP)
                except OSError:
                    pass
            with self._lock:
                for nid in [n for n, (_, seen) in self._nodes.items() if now - seen > 5.0]:
                    del self._nodes[nid]

    def nodes(self):
        with self._lock:
            return [dict(data, node_id=nid) for nid, (data, _) in self._nodes.items()]

    def find_node(self, wait=2.0):
        try:
            self.start()
        except OSError as e:
            log("remote execution discovery unavailable:", e)
            return None, []
        deadline = time.time() + wait
        while True:
            nodes = self.nodes()
            matching = [n for n in nodes if (n.get("project_name") or "").lower() == EXPECTED_PROJECT.lower()]
            if matching or time.time() >= deadline:
                return (matching[0] if matching else None), nodes
            time.sleep(0.1)

    def run(self, command, exec_mode, timeout):
        node, _ = self.find_node()
        if node is None:
            raise NotSent("no editor answered remote-execution discovery")
        remote_id = node["node_id"]
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        listener.bind((BIND_ADDRESS, 0))
        listener.listen(1)
        listener.settimeout(1.0)
        port = listener.getsockname()[1]
        conn = None
        try:
            deadline = time.time() + 6.0
            while conn is None:
                self._sock.sendto(self._message("open_connection", remote_id, {
                    "command_ip": BIND_ADDRESS, "command_port": port}), MULTICAST_GROUP)
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    if time.time() > deadline:
                        raise NotSent("editor did not open a command connection (busy or a modal dialog is open)")
            conn.setblocking(True)
            conn.sendall(self._message("command", remote_id, {
                "command": command, "unattended": True, "exec_mode": exec_mode}))
            return self._receive_result(conn, timeout)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
            listener.close()
            try:
                self._sock.sendto(self._message("close_connection", remote_id), MULTICAST_GROUP)
            except OSError:
                pass

    def _receive_result(self, conn, timeout):
        conn.settimeout(1.0)
        buf = b""
        deadline = time.time() + timeout
        decoder = json.JSONDecoder()
        while True:
            try:
                chunk = conn.recv(1 << 20)
            except socket.timeout:
                if time.time() > deadline:
                    raise RemoteError(f"No result after {timeout:.0f} s. The command may still be running in the editor.")
                continue
            except OSError as e:
                raise RemoteError(f"Command connection failed after sending ({e}); the command may or may not have run.")
            if not chunk:
                raise RemoteError("The editor closed the command connection before replying "
                                  "(another remote-execution client may have taken it); the command may or may not have run.")
            buf += chunk
            try:
                msg, _ = decoder.raw_decode(buf.decode("utf-8").lstrip())
            except (UnicodeDecodeError, ValueError):
                continue
            if self._accepts(msg) and msg.get("type") == "command_result":
                data = msg.get("data") or {}
                lines = [(e.get("output") or "").rstrip("\r\n") if e.get("type", "Info") == "Info"
                         else f"[{e.get('type')}] " + (e.get("output") or "").rstrip("\r\n")
                         for e in data.get("output") or []]
                return {"success": bool(data.get("success")), "result": _clean(data.get("result")),
                        "output": "\n".join(lines), "transport": "remote-execution"}
            buf = b""


def _clean(text):
    if text in (None, "None"):
        return None
    return re.sub(r"\r?\n(\r?\n)+", "\n", str(text)).strip()


# ---------------------------------------------------------------------------
# Transport 2: file bridge
# ---------------------------------------------------------------------------

def bridge_alive():
    try:
        beat = json.loads((BRIDGE / "heartbeat.json").read_text())
        return time.time() - beat["time"] < 10
    except Exception:
        return False


def bridge_run(payload, timeout):
    if not bridge_alive():
        raise NotSent("file bridge is not running")
    rid = str(uuid.uuid4())
    payload = dict(payload, id=rid)
    tmp = BRIDGE / "request.tmp"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, BRIDGE / "request.json")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.1)
        try:
            r = json.loads((BRIDGE / "response.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if r.get("id") == rid:
            return {"success": bool(r.get("ok")), "result": r.get("result") or _clean(r.get("error")),
                    "output": (r.get("output") or "").rstrip(), "transport": "file-bridge"}
    if (BRIDGE / "request.json").exists():
        (BRIDGE / "request.json").unlink(missing_ok=True)
        raise NotSent("bridge did not pick the request up (editor busy or closed)")
    raise RemoteError(f"No bridge result after {timeout:.0f} s. The command may still be running in the editor.")


REMOTE = RemoteExecution()


def execute(code=None, mode="exec", timeout=120.0, script=None, script_args=None):
    """Run Python in the editor via remote execution, falling back to the file bridge."""
    reasons = []
    if script is None:
        remote_cmd, remote_mode = code, ("EvaluateStatement" if mode == "eval" else "ExecuteFile")
    else:
        remote_cmd = '"' + str(script).replace("\\", "/") + '"'
        remote_mode = "ExecuteFile"
    # Script arguments are a dict for the bridge (ARGS); remote execution has only sys.argv, so use the bridge.
    if not script_args and os.environ.get("ABF_MCP_TRANSPORT") != "bridge":
        try:
            return REMOTE.run(remote_cmd, remote_mode, timeout)
        except NotSent as e:
            reasons.append(f"remote execution: {e}")
    payload = {"mode": mode, "args": script_args or {}}
    payload.update({"code": code} if script is None else {"script": str(script)})
    try:
        return bridge_run(payload, timeout)
    except NotSent as e:
        reasons.append(f"bridge: {e}")
    running = bool(wininput.editor_windows()) if sys.platform == "win32" else False
    raise RemoteError(
        "Could not reach the Mod Editor (" + "; ".join(reasons) + "). "
        + ("The editor is open but neither transport answered: a modal dialog may be blocking it, "
           "or the startup hook isn't installed (run install.py, then restart the editor)."
           if running else "The editor is not running: use launch_editor."))


def format_result(r):
    parts = [f"success: {r['success']}  (via {r['transport']})"]
    if r.get("result"):
        parts.append(f"result: {r['result']}")
    out = r.get("output") or ""
    if len(out) > OUTPUT_LIMIT:
        out = out[:OUTPUT_LIMIT] + f"\n... (output truncated, {len(out)} chars total)"
    if out:
        parts.append("output:\n" + out)
    return "\n".join(parts), not r["success"]          # (text, is_error)


def run_json(body, args=None, timeout=120.0):
    """Run a snippet that defines `def _abf(args): ...` in the editor and return its JSON result."""
    code = (
        "import unreal, json\n"
        f"{body}\n"
        f"_abf_args = json.loads({json.dumps(json.dumps(args or {}))})\n"
        f"print({JSON_MARKER!r} + json.dumps(_abf(_abf_args), default=str))\n"
    )
    r = execute(code, timeout=timeout)
    for line in (r.get("output") or "").splitlines():
        idx = line.find(JSON_MARKER)
        if idx >= 0:
            return json.loads(line[idx + len(JSON_MARKER):].strip())
    text, _ = format_result(r)
    raise RemoteError("Editor snippet failed:\n" + text)


# ---------------------------------------------------------------------------
# Python run inside the editor (each defines _abf(args))
# ---------------------------------------------------------------------------

PY_MOD_ROOTS = r'''
def _mod_roots():
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    return [r for r in (str(p) for p in reg.get_sub_paths("/", False))
            if unreal.EditorAssetLibrary.does_asset_exist(r + "/AbioticModConfig")]

def _plugin_dir(root):
    try:
        return unreal.Paths.convert_relative_path_to_full(unreal.PluginBlueprintLibrary.get_plugin_base_dir(root.strip("/")))
    except Exception:
        return None
'''

PY_STATUS = PY_MOD_ROOTS + r'''
def _abf(args):
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    return {
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "open_mods": [{"root": r, "dir": _plugin_dir(r)} for r in _mod_roots()],
        "editor_map": world.get_path_name() if world else None,
        "play_in_editor": unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None,
    }
'''

PY_LIST_MODS = PY_MOD_ROOTS + r'''
def _abf(args):
    out = []
    for root in _mod_roots():
        cfg = unreal.load_asset(root + "/AbioticModConfig")
        info = {"root": root, "dir": _plugin_dir(root)}
        for prop in ("mod_prefix", "local_only", "load_after"):
            try:
                v = cfg.get_editor_property(prop)
                info[prop] = list(v) if prop == "load_after" else v
            except Exception as e:
                info[prop] = "<" + str(e) + ">"
        assets = unreal.EditorAssetLibrary.list_assets(root, recursive=True, include_folder=False)
        info["asset_count"] = len(assets)
        if args.get("include_assets"):
            reg = unreal.AssetRegistryHelpers.get_asset_registry()
            info["assets"] = []
            for a in assets:
                ad = reg.get_asset_by_object_path(str(a))
                info["assets"].append({"path": str(a).split(".")[0],
                                       "class": str(ad.asset_class_path.asset_name) if ad.is_valid() else "?"})
        out.append(info)
    return out
'''

PY_FIND_ASSETS = r'''
def _abf(args):
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    path = args.get("path") or "/Game"
    name = (args.get("name_contains") or "").lower()
    cls = (args.get("class_name") or "").lower()
    limit = int(args.get("limit") or 200)
    found, total = [], 0
    for ad in reg.get_assets_by_path(path, recursive=True):
        aname = str(ad.asset_name)
        acls = str(ad.asset_class_path.asset_name)
        if name and name not in aname.lower():
            continue
        if cls and cls != acls.lower():
            continue
        total += 1
        if len(found) < limit:
            found.append({"path": str(ad.package_name), "class": acls})
    return {"total_matches": total, "returned": len(found), "assets": found}
'''

PY_GET_ROW = r'''
def _fields(st):
    names = []
    for n in dir(st):
        if n.startswith("_"):
            continue
        try:
            v = getattr(st, n)
        except Exception:
            continue
        if callable(v) and not isinstance(v, (unreal.StructBase, unreal.Object)):
            continue
        names.append(n)
    return names

def _abf(args):
    path = args["asset_path"]
    asset = unreal.load_asset(path)
    if asset is None:
        return {"error": "asset not found: " + path}
    info = {"asset": asset.get_path_name(), "class": asset.get_class().get_name()}
    if isinstance(asset, unreal.DataTable):
        names = [str(n) for n in unreal.DataTableFunctionLibrary.get_data_table_row_names(asset)]
        info["row_count"] = len(names)
        want = args.get("row_name")
        if want:
            rows = json.loads(unreal.DataTableFunctionLibrary.export_data_table_to_json_string(asset))
            info["row"] = next((r for r in rows if str(r.get("Name")) == want), None)
        else:
            info["row_names"] = names[: int(args.get("limit") or 500)]
        return info
    try:
        row = asset.get_editor_property("row")
    except Exception:
        row = None
    if row is None:
        info["note"] = "asset has no 'row' property; use run_python to inspect it"
        return info
    info["row_struct"] = type(row).__name__
    info["fields"] = _fields(row)
    info["export_text"] = row.export_text()
    return info
'''

PY_SET_ROW = r'''
def _set_path(obj, parts, value):
    if len(parts) == 1:
        obj.set_editor_property(parts[0], value)
        return
    child = obj.get_editor_property(parts[0])
    _set_path(child, parts[1:], value)
    obj.set_editor_property(parts[0], child)

def _abf(args):
    path = args["asset_path"]
    if path.startswith("/Game/"):
        return {"error": "refusing to edit base-game content under /Game"}
    asset = unreal.load_asset(path)
    if asset is None:
        return {"error": "asset not found: " + path}
    row = asset.get_editor_property("row")
    changed = []
    if args.get("import_text"):
        row.import_text(args["import_text"])
        changed.append("<import_text>")
    for key, value in (args.get("values") or {}).items():
        _set_path(row, key.split("."), value)
        changed.append(key)
    scope = {"unreal": unreal}
    for key, expr in (args.get("expressions") or {}).items():
        _set_path(row, key.split("."), eval(expr, scope))
        changed.append(key)
    asset.set_editor_property("row", row)
    saved = None
    if args.get("save", True):
        saved = unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False)
    return {"asset": asset.get_path_name(), "changed": changed, "saved": saved,
            "export_text": asset.get_editor_property("row").export_text()}
'''

PY_SAVE = r'''
def _abf(args):
    paths = args.get("asset_paths") or []
    if not paths:
        return {"saved_all_dirty": unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)}
    out = {}
    for p in paths:
        a = unreal.load_asset(p)
        out[p] = bool(a) and unreal.EditorAssetLibrary.save_loaded_asset(a, only_if_is_dirty=False)
    return out
'''

PY_COMPILE = r'''
def _abf(args):
    bp = unreal.load_asset(args["asset_path"])
    if bp is None:
        return {"error": "asset not found"}
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    saved = None
    if args.get("save", True):
        saved = unreal.EditorAssetLibrary.save_loaded_asset(bp, only_if_is_dirty=False)
    return {"blueprint": bp.get_path_name(), "saved": saved}
'''

PY_VALIDATE = r'''
def _abf(args):
    paths = []
    for p in args.get("paths") or []:
        if unreal.EditorAssetLibrary.does_directory_exist(p):
            paths += [str(a).split(".")[0] for a in unreal.EditorAssetLibrary.list_assets(p, recursive=True, include_folder=False)]
        else:
            paths.append(p)
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    datas = [reg.get_asset_by_object_path(p + "." + p.rsplit("/", 1)[-1]) for p in paths]
    sub = unreal.get_editor_subsystem(unreal.EditorValidatorSubsystem)
    settings = unreal.ValidateAssetsSettings()
    settings.set_editor_property("show_if_no_failures", False)
    results = unreal.ValidateAssetsResults()
    sub.validate_assets_with_settings(datas, settings, results)
    return {"checked": results.get_editor_property("num_checked"),
            "valid": results.get_editor_property("num_valid"),
            "invalid": results.get_editor_property("num_invalid"),
            "warnings": results.get_editor_property("num_warnings"),
            "skipped": results.get_editor_property("num_skipped")}
'''

PY_EXPORT_T3D = r'''
def _abf(args):
    path = args["asset_path"]
    asset = unreal.load_asset(path)
    if asset is None:
        return {"error": "asset not found: " + path}
    task = unreal.AssetExportTask()
    for k, v in dict(object=asset, exporter=unreal.ObjectExporterT3D(), filename=args["out_file"],
                     automated=True, prompt=False, replace_identical=True).items():
        setattr(task, k, v)
    ok = unreal.Exporter.run_asset_export_task(task)
    return {"ok": bool(ok), "file": args["out_file"]}
'''

PY_PIE_STOP = r'''
def _abf(args):
    sub = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    was = sub.get_game_world() is not None
    if was:
        unreal.EditorLevelLibrary.editor_end_play()
    return {"was_playing": was}
'''

# Row "kinds" map to the game's <Kind>HandleFunctionLibrary: conv_name_to_<kind>, get_<kind>_row, get_all_<kind>_row_names.
PY_ROW_LIB = r'''
def _camel(kind):
    return "".join(p.capitalize() if not p.isupper() else p for p in kind.split("_"))

def _lib(kind):
    name = _camel(kind) + "HandleFunctionLibrary"
    lib = getattr(unreal, name, None)
    if lib is None:
        kinds = sorted(n[:-len("HandleFunctionLibrary")] for n in dir(unreal) if n.endswith("HandleFunctionLibrary"))
        raise ValueError("unknown row kind %r; libraries: %s" % (kind, kinds))
    return lib

def _snake(kind):
    return kind.lower()

def _get_row(kind, name):
    lib = _lib(kind)
    conv = getattr(lib, "conv_name_to_" + _snake(kind))
    getter = getattr(lib, "get_" + _snake(kind) + "_row")
    r = getter(conv(name))
    if isinstance(r, tuple):
        data, valid = r[0], r[1]
        if str(valid).endswith("INVALID") or "NOT" in str(valid).upper():
            return None
        return data
    return r
'''

PY_GET_GAME_ROW = PY_ROW_LIB + r'''
def _abf(args):
    kind = args["kind"]
    if args.get("name") is None:
        lib = _lib(kind)
        names = [str(n) for n in getattr(lib, "get_all_" + _snake(kind) + "_row_names")()]
        q = (args.get("name_contains") or "").lower()
        hits = [n for n in names if q in n.lower()]
        return {"kind": kind, "total": len(names), "matches": hits[: int(args.get("limit") or 300)]}
    data = _get_row(kind, args["name"])
    if data is None:
        return {"error": "no %s row named %r" % (kind, args["name"])}
    return {"kind": kind, "name": args["name"], "struct": type(data).__name__, "export_text": data.export_text()}
'''

PY_CREATE_ROW_ASSET = PY_ROW_LIB + r'''
def _abf(args):
    path = args["asset_path"].rstrip("/")
    if path.startswith("/Game/"):
        return {"error": "refusing to create assets under /Game"}
    cls = getattr(unreal, args["class_name"], None)
    if cls is None or not issubclass(cls, unreal.AbioticModRowAsset):
        return {"error": "class_name must be a *ModRowAsset class, e.g. GearItemModRowAsset, RecipeModRowAsset"}
    folder, name = path.rsplit("/", 1)
    # Resolve what to copy BEFORE creating anything, so a bad request leaves no half-made asset behind.
    want = type(cls().get_editor_property("row")).__name__
    data, copied = None, None
    if args.get("copy_from_asset"):
        src = unreal.load_asset(args["copy_from_asset"])
        if src is None:
            return {"error": "copy_from_asset not found"}
        data = src.get_editor_property("row")
        copied = args["copy_from_asset"]
    elif args.get("copy_from_kind"):
        data = _get_row(args["copy_from_kind"], args["copy_from_name"])
        if data is None:
            return {"error": "no %s row named %r" % (args["copy_from_kind"], args["copy_from_name"])}
        copied = args["copy_from_kind"] + "/" + args["copy_from_name"]
    if data is not None and type(data).__name__ != want:
        return {"error": "%s holds a %s row, but %s is a %s" % (args["class_name"], want, copied, type(data).__name__)}
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        if not args.get("overwrite"):
            return {"error": "asset already exists: " + path + " (pass overwrite=true to replace its row)"}
        asset = unreal.load_asset(path)
    else:
        f = unreal.DataAssetFactory()
        f.set_editor_property("data_asset_class", cls)
        asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, cls, f)
        if asset is None:
            return {"error": "create_asset failed (see read_editor_log)"}
    if data is not None:
        asset.set_editor_property("row", data)
    asset.set_editor_property("row_name", args.get("row_name") or name)
    saved = unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False)
    return {"asset": asset.get_path_name(), "class": asset.get_class().get_name(), "row_name": str(asset.get_editor_property("row_name")),
            "copied_from": copied, "saved": saved, "export_text": asset.get_editor_property("row").export_text()}
'''

PY_LOAD_MAP = r'''
def _abf(args):
    sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    ok = sub.load_level(args["map_path"])
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    return {"loaded": bool(ok), "editor_map": world.get_path_name() if world else None}
'''

PY_CONSOLE = r'''
def _abf(args):
    sub = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = sub.get_game_world() if args.get("world") == "pie" else sub.get_editor_world()
    if world is None:
        return {"error": "no %s world" % args.get("world", "editor")}
    unreal.SystemLibrary.execute_console_command(world, args["command"])
    return {"ran": args["command"], "world": world.get_name()}
'''

PY_PIE_INFO = r'''
def _abf(args):
    sub = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = sub.get_game_world()
    if world is None:
        return {"playing": False}
    out = {"playing": True, "map": world.get_name(), "players": []}
    for i in range(4):
        pc = unreal.GameplayStatics.get_player_controller(world, i)
        if pc is None:
            break
        pawn = unreal.GameplayStatics.get_player_pawn(world, i)
        p = {"index": i, "controller": pc.get_class().get_name(), "pawn": pawn.get_class().get_name() if pawn else None}
        if pawn:
            loc = pawn.get_actor_location()
            p["location"] = [round(loc.x), round(loc.y), round(loc.z)]
            for prop in args.get("pawn_properties") or []:
                try:
                    p[prop] = str(pawn.get_editor_property(prop))[:200]
                except Exception as e:
                    p[prop] = "<" + str(e)[:80] + ">"
        out["players"].append(p)
    return out
'''

PY_DELETE_ASSET = r'''
def _abf(args):
    path = args["asset_path"]
    if path.startswith("/Game/"):
        return {"error": "refusing to delete base-game content"}
    if not unreal.EditorAssetLibrary.does_asset_exist(path):
        return {"error": "no asset at " + path}
    return {"deleted": unreal.EditorAssetLibrary.delete_asset(path), "asset": path}
'''


# ---------------------------------------------------------------------------
# Offline helpers
# ---------------------------------------------------------------------------

_LISTS = {}


def asset_list(name):
    """File list of the retail game / the editor install: data/<name>-<version>-files.json(.gz), newest version."""
    if name not in _LISTS:
        found = sorted(DATA.glob(f"{name}-*-files.json*"), key=lambda p: p.name)
        if not found:
            _LISTS[name] = None
        else:
            p = found[-1]
            raw = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
            _LISTS[name] = set(json.loads(raw.decode("utf-8")))
    return _LISTS[name]


# Files the official templates themselves contain; anything else in the folder ships too.
SHIPPABLE = {".uasset", ".umap", ".uexp", ".ubulk", ".uplugin", ".abfmod", ".ini", ".png"}
GAME_REF = re.compile(rb"/Game/[A-Za-z0-9_\-/]+(?:\.[A-Za-z0-9_\-]+)?")


def check_upload(mod_dir):
    root = Path(mod_dir)
    if not root.is_dir():
        raise RemoteError(f"Not a folder: {root}")
    report = {"folder": str(root), "errors": [], "warnings": [], "files": 0, "size_mb": 0}
    uplugins = list(root.glob("*.uplugin"))
    if not uplugins:
        report["errors"].append("No .uplugin in the folder root (point this at the mod project folder).")
    else:
        try:
            up = json.loads(uplugins[0].read_text(encoding="utf-8-sig"))
            report["uplugin"] = {k: up.get(k) for k in ("FriendlyName", "Version", "VersionName", "EngineVersion")}
        except ValueError as e:
            report["errors"].append(f"{uplugins[0].name} is not valid JSON: {e}")
    retail, editor = asset_list("retail"), asset_list("editor")
    missing, editor_only = {}, {}
    total = 0
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        report["files"] += 1
        total += f.stat().st_size
        top = rel.split("/")[0]
        if top in ("Saved", "Intermediate", "Binaries", "DerivedDataCache"):
            report["warnings"].append(f"{rel}: editor working folder; it is zipped and uploaded too")
            continue
        ext = f.suffix.lower()
        if ext == ".md":
            report["errors"].append(f"{rel}: mod.io rejects .md files (error 29200, shown as 'Unknown ApiError'). Move it out.")
        elif ext not in SHIPPABLE:
            report["warnings"].append(f"{rel}: not a file type the templates contain; it would ship inside the mod zip")
        if ext in (".uasset", ".umap") and retail is not None:
            data = f.read_bytes()
            for m in set(GAME_REF.findall(data)):
                pkg = m.decode("ascii", "replace").split(".")[0]
                rel_game = "AbioticFactor/Content/" + pkg[len("/Game/"):]
                if rel_game + ".uasset" in retail or rel_game + ".umap" in retail:
                    continue
                if editor and (rel_game + ".uasset" in editor or rel_game + ".umap" in editor):
                    editor_only.setdefault(pkg, []).append(rel)
                else:
                    missing.setdefault(pkg, []).append(rel)
    report["size_mb"] = round(total / 1048576, 2)
    for pkg, users in sorted(editor_only.items()):
        report["errors"].append(f"{pkg}: exists only in the editor, not the retail game (cloud cook / runtime will fail); used by {sorted(set(users))[:5]}")
    for pkg, users in sorted(missing.items()):
        report["warnings"].append(f"{pkg}: not in the retail or editor asset list (may be a name, not a package); used by {sorted(set(users))[:5]}")
    if retail is None:
        report["warnings"].append("data/retail-1.5-files.json missing: /Game reference check skipped")
    report["verdict"] = "FAIL" if report["errors"] else ("CHECK WARNINGS" if report["warnings"] else "OK")
    report["note"] = ("Offline pre-check only. The editor's own upload Asset Audit (Blueprint filter) "
                      "and mod.io's cloud cook still decide; a file list match is not proof.")
    return report


def t3d_errors(text, prefix=None):
    nodes, problems = 0, []
    for m in re.finditer(r'Begin Object Class=([^\s]+) Name="([^"]+)"(.*?)\n\s*End Object', text, re.S):
        cls, name, body = m.group(1), m.group(2), m.group(3)
        if "Node" not in cls.split(".")[-1]:
            continue
        if prefix and not name.startswith(prefix):
            continue
        nodes += 1
        err = re.search(r'ErrorMsg="(.*)"', body)
        orphan = re.findall(r'PinName="([^"]+)"[^\n]*bOrphanedPin=True', body)
        if err or orphan:
            problems.append({"node": name, "class": cls.split(".")[-1],
                             "error": err.group(1)[:300] if err else None, "orphaned_pins": orphan})
    return {"nodes_checked": nodes, "problems": problems}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

_LAST_SCALE = {}


def _json(r):
    """JSON text for a tool result; a dict with an 'error' key is reported as a tool error."""
    return json.dumps(r, indent=2, default=str), isinstance(r, dict) and bool(r.get("error"))


def _window(args):
    return wininput.find_window(args.get("window"))


def t_editor_status(args):
    node, nodes = REMOTE.find_node(wait=float(args.get("wait_seconds", 2)))
    report = {"remote_execution": node is not None, "file_bridge": bridge_alive(),
              "windows": [w["title"] for w in wininput.editor_windows()],
              "hook_installed": paths.hook_installed(),
              "editor_install": str(EDITOR_ROOT) if EDITOR_ROOT else None}
    if node is not None or report["file_bridge"]:
        report.update(run_json(PY_STATUS, timeout=30))
        if not report["open_mods"] and any("Mod Browser" in t for t in report["windows"]):
            report["hint"] = "The Mod Browser picker is open and no mod is loaded: use open_mod, or create one in the picker."
    elif not report["windows"]:
        report["hint"] = NOT_FOUND if EDITOR_ROOT is None else "The Mod Editor is not running: use launch_editor."
    elif not report["hook_installed"]:
        report["hint"] = "Editor is open but the startup hook isn't installed: run install.py, then restart the editor."
    else:
        report["hint"] = "Editor is open but not reachable: close any modal dialog, or restart it if the hook was installed after it started."
    return json.dumps(report, indent=2, default=str)


def t_launch_editor(args):
    wins = wininput.editor_windows()
    if wins:
        return "Already running: " + ", ".join(w["title"] for w in wins)
    if EDITOR_EXE is None or not EDITOR_EXE.is_file():
        return NOT_FOUND, True
    import subprocess
    cmd = [str(EDITOR_EXE)]
    subprocess.Popen(cmd,cwd=str(EDITOR_EXE.parent), creationflags=0x00000008 | 0x00000200)  # DETACHED, NEW_GROUP
    deadline = time.time() + float(args.get("wait_seconds", 180))
    while time.time() < deadline:
        time.sleep(2)
        if bridge_alive() or REMOTE.find_node(wait=0.5)[0]:
            return "Editor started and reachable. Windows: " + ", ".join(w["title"] for w in wininput.editor_windows())
    return "Editor started but not reachable yet (still loading, or the startup hook is not installed).", True


def t_open_mod(args):
    mod_file = Path(args["mod_file"])
    if mod_file.is_dir():
        mod_file = next(iter(mod_file.glob("*.abfmod")), mod_file)
    if mod_file.suffix.lower() != ".abfmod" or not mod_file.is_file():
        return f"Not an .abfmod file: {mod_file}", True
    root = "/" + mod_file.stem
    try:
        if any(m["root"].lower() == root.lower() for m in run_json(PY_STATUS, timeout=20)["open_mods"]):
            return f"{root} is already open."
    except RemoteError:
        pass
    picker = [w for w in wininput.editor_windows() if "Mod Browser" in w["title"]]
    if not picker:
        return ("The Mod Browser picker isn't open (another mod is loaded). Save your work, close the editor, "
                "then launch_editor and open_mod again."), True
    win = picker[0]
    l, t, r, b = win["rect"]
    wininput.click(win, (r - l) - 228, (b - t) - 30)               # "Browse..." (anchored bottom-right)
    deadline = time.time() + 10
    dialog = None
    while time.time() < deadline and dialog is None:
        time.sleep(0.5)
        dialog = next((w for w in wininput.editor_windows() if w["title"] == "Open Project"), None)
    if dialog is None:
        return "Clicked Browse... but no 'Open Project' dialog appeared; take an editor_screenshot to see why.", True
    def dialogs():
        # The file dialog and its "file name is not valid" message box share the title; the box is the smaller one.
        return sorted((w for w in wininput.editor_windows() if w["title"] == "Open Project"),
                      key=lambda w: -(w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]))

    for attempt in range(3):
        wininput.focus(dialog)
        time.sleep(0.8)                      # the file-name box takes focus a moment after the dialog shows
        wininput.set_clipboard(str(mod_file))
        wininput.keys("ctrl+a")
        time.sleep(0.2)
        wininput.keys("ctrl+v")              # one keystroke: typed characters get dropped while the dialog settles
        time.sleep(0.5)
        wininput.keys("enter")
        deadline = time.time() + float(args.get("wait_seconds", 240))
        outcome = "timeout"
        while time.time() < deadline:
            time.sleep(2)
            try:
                mods = run_json(PY_STATUS, timeout=20)["open_mods"]
            except RemoteError:
                continue                                             # editor may restart while opening
            if any(m["root"].lower() == root.lower() for m in mods):
                return f"Opened {root} from {mod_file.parent}"
            open_now = dialogs()
            if len(open_now) >= 2:                                   # the dialog rejected the path
                wininput.focus(open_now[-1])
                wininput.keys("enter")                               # OK on the message box
                time.sleep(0.6)
                outcome = "rejected"
                break
        if outcome == "timeout":
            return f"Sent the path, but {root} didn't open in time; take an editor_screenshot.", True
        remaining = dialogs()
        if not remaining:
            return f"The Open Project dialog closed without opening {root}; take an editor_screenshot.", True
        dialog = remaining[0]
    return f"The dialog rejected the path three times; take an editor_screenshot. Path sent: {mod_file}", True


def t_run_python(args):
    return format_result(execute(args["code"], timeout=float(args.get("timeout_seconds", 120))))


def t_evaluate_python(args):
    return format_result(execute(args["expression"], mode="eval", timeout=float(args.get("timeout_seconds", 60))))


def t_run_python_file(args):
    path = Path(args["path"]).resolve()
    if not path.is_file():
        return f"File not found: {path}", True
    return format_result(execute(script=path, script_args=args.get("args"),
                                 timeout=float(args.get("timeout_seconds", 300))))


def t_list_mods(args):
    return json.dumps(run_json(PY_LIST_MODS, {"include_assets": bool(args.get("include_assets"))}), indent=2, default=str)


def t_list_mod_templates(args):
    if EDITOR_ROOT is None:
        return NOT_FOUND, True
    out = []
    for d in sorted((EDITOR_ROOT / "ModTemplates").glob("TP_*")):
        defs = next(iter(d.rglob("TemplateDefs.ini")), None)
        name = desc = cats = None
        if defs:
            txt = defs.read_text(encoding="utf-8-sig", errors="replace")
            m = re.search(r'LocalizedDisplayNames=\(Language="en",Text="([^"]*)"', txt)
            name = m and m.group(1)
            m = re.search(r'LocalizedDescriptions=\(Language="en",Text="([^"]*)"', txt)
            desc = m and m.group(1)
            m = re.search(r"^Categories=(.*)$", txt, re.M)
            cats = m and m.group(1).strip()
        entry = {"template": d.name, "display_name": name, "category": cats, "description": desc}
        if args.get("include_files"):
            entry["assets"] = [str(p.relative_to(d)).replace("\\", "/") for p in d.rglob("*.uasset")]
        out.append(entry)
    return json.dumps(out, indent=2)


def t_find_assets(args):
    return json.dumps(run_json(PY_FIND_ASSETS, args, timeout=120), indent=2)


def t_get_row(args):
    return _json(run_json(PY_GET_ROW, args))


def t_set_row(args):
    return _json(run_json(PY_SET_ROW, args))


def t_save_assets(args):
    return _json(run_json(PY_SAVE, args))


def t_compile_blueprint(args):
    return _json(run_json(PY_COMPILE, args))


def t_validate_assets(args):
    return json.dumps(run_json(PY_VALIDATE, args, timeout=300), indent=2, default=str)


def t_export_t3d(args):
    path = args["asset_path"]
    if path.startswith("/Game/"):
        return "Refusing: T3D-exporting a /Game Blueprint crashed this editor before. Export mod assets only.", True
    out = Path(args.get("out_file") or (HERE / "exports" / (path.strip("/").replace("/", "_") + ".t3d")))
    out.parent.mkdir(parents=True, exist_ok=True)
    r = run_json(PY_EXPORT_T3D, {"asset_path": path, "out_file": str(out).replace("\\", "/")}, timeout=120)
    if not r.get("ok"):
        return json.dumps(r), True
    text = out.read_text(encoding="utf-8", errors="replace")
    r.update(t3d_errors(text, args.get("node_prefix")))
    r["bytes"] = len(text)
    return json.dumps(r, indent=2)


def t_t3d_errors(args):
    text = args.get("text") or Path(args["file"]).read_text(encoding="utf-8", errors="replace")
    return json.dumps(t3d_errors(text, args.get("node_prefix")), indent=2)


def t_check_upload(args):
    d = args.get("mod_dir")
    if not d and args.get("mod_root"):
        mods = run_json(PY_LIST_MODS, {})
        d = next((m["dir"] for m in mods if m["root"].strip("/").lower() == args["mod_root"].strip("/").lower()), None)
        if not d:
            return f"Mod root {args['mod_root']} is not open in the editor; pass mod_dir instead.", True
    if not d:
        return "Pass mod_dir (the project folder holding the .uplugin) or mod_root.", True
    r = check_upload(d)
    return json.dumps(r, indent=2), r["verdict"] == "FAIL"


def t_editor_windows(args):
    return json.dumps(wininput.editor_windows(), indent=2)


def t_screenshot(args):
    win = _window(args)
    png, f, (w, h) = wininput.capture(win, int(args.get("max_width", 1600)))
    _LAST_SCALE[win["hwnd"]] = f
    SHOTS.mkdir(exist_ok=True)
    path = SHOTS / ("latest.png" if not args.get("save_as") else Path(args["save_as"]).name)
    path.write_bytes(png)
    note = (f"Window '{win['title']}' {w}x{h}, image scaled 1/{f}. Click coordinates are image pixels "
            f"(editor_click scales them by {f}). Saved: {path}")
    return {"content": [{"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"},
                        {"type": "text", "text": note}], "isError": False}


def _scaled(win, args):
    f = 1 if args.get("window_pixels") else _LAST_SCALE.get(win["hwnd"], 1)
    return int(round(args["x"] * f)), int(round(args["y"] * f))


def t_click(args):
    win = _window(args)
    x, y = _scaled(win, args)
    wininput.click(win, x, y, args.get("button", "left"), bool(args.get("double")))
    return f"Clicked {args.get('button', 'left')}{' double' if args.get('double') else ''} at window ({x}, {y}) in '{win['title']}'"


def t_keys(args):
    win = _window(args)
    if "x" in args and "y" in args:
        wininput.click(win, *_scaled(win, args))
        time.sleep(0.3)
    else:
        wininput.focus(win)
    done = []
    for combo in args.get("keys") or []:
        wininput.keys(combo, int(args.get("hold_ms", 50)))
        done.append(combo)
        time.sleep(0.15)
    if args.get("text"):
        wininput.type_text(args["text"])
        done.append(f"typed {len(args['text'])} chars")
    if args.get("press_enter"):
        wininput.keys("enter")
        done.append("enter")
    return f"Sent to '{win['title']}': {done}"


def t_paste_graph(args):
    text = args.get("text") or Path(args["file"]).read_text(encoding="utf-8-sig")
    win = _window(args)
    x, y = _scaled(win, args) if "x" in args else (600, 640)
    wininput.set_clipboard(text)
    # One process for click, select-all, delete and paste, so the graph keeps keyboard focus.
    wininput.click(win, x, y)
    time.sleep(0.5)
    if args.get("clear"):
        wininput.keys("ctrl+a")
        time.sleep(0.6)
        wininput.keys("delete")
        time.sleep(0.8)
        wininput.click(win, x, y)
        time.sleep(0.4)
    wininput.keys("ctrl+v")
    time.sleep(float(args.get("settle_seconds", 3)))
    return (f"Pasted {len(text)} chars into '{win['title']}' at ({x}, {y}). Compile, then check with "
            "export_t3d(node_prefix=...) - calls to functions that don't exist yet are dropped silently.")


def t_play_in_editor(args):
    action = args.get("action", "start")
    if action == "stop":
        return json.dumps(run_json(PY_PIE_STOP, timeout=30))
    status = run_json(PY_STATUS, timeout=30)
    if status.get("play_in_editor"):
        return "Already playing."
    win = wininput.find_window()
    wininput.focus(win)
    wininput.keys("alt+p", 80)
    deadline = time.time() + float(args.get("wait_seconds", 60))
    while time.time() < deadline:
        time.sleep(1.5)
        if run_json(PY_STATUS, timeout=30).get("play_in_editor"):
            return "Play In Editor started (Alt+P). Stop with play_in_editor(action='stop')."
    return "Sent Alt+P but no game world appeared. Is a map loaded (not 'Empty')? Is a dialog or the Message Log in the way?", True


def _export_mod_t3d(path):
    if path.startswith("/Game/"):
        raise RemoteError("Refusing: T3D-exporting a /Game Blueprint crashed this editor before. Export mod assets only.")
    out = HERE / "exports" / (path.strip("/").replace("/", "_") + ".t3d")
    out.parent.mkdir(parents=True, exist_ok=True)
    r = run_json(PY_EXPORT_T3D, {"asset_path": path, "out_file": str(out).replace("\\", "/")}, timeout=120)
    if not r.get("ok"):
        raise RemoteError("export failed: " + json.dumps(r))
    return out.read_text(encoding="utf-8", errors="replace")


def t_inspect_blueprint(args):
    parsed = bpscan.parse_t3d(_export_mod_t3d(args["asset_path"]))
    nodes = parsed["nodes"]
    if args.get("graph"):
        nodes = [n for n in nodes if n.get("graph") == args["graph"]]
    if args.get("node_prefix"):
        nodes = [n for n in nodes if n["name"].startswith(args["node_prefix"])]
    out = {"blueprint": parsed["blueprint"], "graphs": parsed["graphs"], "node_count": len(nodes)}
    calls = {}
    for n in nodes:
        for key in ("function", "event", "variable", "macro"):
            r = n.get(key)
            if r:
                calls.setdefault(key, set()).add((r.get("parent") or "self") + "." + r["name"])
    out["references"] = {k: sorted(v) for k, v in calls.items()}
    out["problems"] = [{"node": n["name"], "error": n.get("error"), "orphaned_pins": n.get("orphaned_pins")} for n in nodes if n.get("error") or n.get("orphaned_pins")]
    if args.get("include_nodes", True):
        slim = []
        for n in nodes[: int(args.get("limit") or 400)]:
            e = {"name": n["name"], "class": n["class"], "graph": n.get("graph")}
            for key in ("function", "event", "variable", "macro", "customFunctionName", "inputKey", "structType"):
                if n.get(key):
                    e[key] = n[key]
            if args.get("include_pins"):
                e["pins"] = n["pins"]
            slim.append(e)
        out["nodes"] = slim
    return json.dumps(out, indent=2)


def t_blueprint_filter_check(args):
    rules, meta = bpscan.load_filter()
    paths = list(args.get("asset_paths") or [])
    if args.get("mod_root"):
        found = run_json(PY_FIND_ASSETS, {"path": args["mod_root"], "limit": 2000}, timeout=120)
        paths += [a["path"] for a in found["assets"] if a["class"] in ("Blueprint", "WidgetBlueprint", "AnimBlueprint")]
    if not paths:
        return "Pass asset_paths (mod Blueprints) or mod_root.", True
    report = {"filter": meta, "checked": [], "blocked": [], "unlisted": []}
    for p in paths:
        parsed = bpscan.parse_t3d(_export_mod_t3d(p))
        res = bpscan.check_nodes(parsed["nodes"], rules)
        report["checked"].append({"asset": p, "nodes": len(parsed["nodes"]), "blocked": len(res["blocked"])})
        for b in res["blocked"]:
            b["asset"] = p
            report["blocked"].append(b)
        for u in res["unlisted"]:
            u["asset"] = p
            report["unlisted"].append(u)
    report["verdict"] = "BLOCKED REFERENCES" if report["blocked"] else "OK"
    report["note"] = ("Same rule file the upload Asset Audit uses, applied to function/variable/object references found in the "
                      "T3D export. Parent-class override signatures and the cloud cook's editor-only checks are not covered; "
                      "run check_upload too.")
    return json.dumps(report, indent=2), bool(report["blocked"])


def t_get_game_row(args):
    return _json(run_json(PY_GET_GAME_ROW, args, timeout=120))


def t_create_row_asset(args):
    return _json(run_json(PY_CREATE_ROW_ASSET, args, timeout=120))


def t_load_map(args):
    return _json(run_json(PY_LOAD_MAP, args, timeout=float(args.get("timeout_seconds", 300))))


def t_console_command(args):
    return _json(run_json(PY_CONSOLE, args, timeout=60))


def t_pie_info(args):
    return _json(run_json(PY_PIE_INFO, args, timeout=30))


def t_delete_asset(args):
    return _json(run_json(PY_DELETE_ASSET, args, timeout=60))


def t_close_window(args):
    win = wininput.find_window(args["window"])
    wininput.close_window(win)
    still = [w["title"] for w in wininput.editor_windows()]
    return f"Sent close to '{win['title']}'. Open now: {still}"


def t_read_editor_log(args):
    name = args.get("file") or "AbioticFactor.log"
    path = SAVED_DIR / "Logs" / name
    if not path.is_file():
        logs = sorted(p.name for p in (SAVED_DIR / "Logs").glob("*.log"))
        return f"No log at {path}. Available: {logs}", True
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    if args.get("contains"):
        rx = re.compile(args["contains"], re.I)
        lines = [l for l in lines if rx.search(l)]
    n = int(args.get("lines", 100))
    return f"{path} ({len(lines)} matching lines, showing last {min(n, len(lines))})\n" + "\n".join(lines[-n:])


S = lambda **p: p  # noqa: E731
STR, NUM, INT, BOOL, OBJ = ({"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}, {"type": "object"})
WIN = {"type": "string", "description": "Part of the editor window title (default: the main editor window). See editor_windows."}
XY = {"x": NUM, "y": NUM, "window_pixels": {"type": "boolean", "description": "x/y are real window pixels, not screenshot pixels."}}

TOOLS = [
    ("editor_status", t_editor_status,
     "Call first. Whether the Mod Editor is running and reachable (remote execution / file bridge), which mod is open, the map, and whether Play In Editor is running.",
     S(wait_seconds=NUM), []),
    ("launch_editor", t_launch_editor,
     "Start the Mod Editor and wait until it is reachable. It opens on the Mod Browser picker; then use open_mod.",
     S(wait_seconds=NUM), []),
    ("open_mod", t_open_mod,
     "Open a mod project (.abfmod file or its folder) from the Mod Browser picker via Browse... "
     "(the command line can't open one). Works only while the picker is showing.",
     S(mod_file=STR, wait_seconds=NUM), ["mod_file"]),
    ("run_python", t_run_python,
     "Run a multi-statement Python script inside the Mod Editor (`unreal` is available); print() output is returned. "
     "Runs on the editor's game thread: keep it short, never open modal dialogs, save assets explicitly. "
     "Mod row assets keep their data in the 'row' property. Python can't create Blueprint nodes: use paste_graph.",
     S(code=STR, timeout_seconds=NUM), ["code"]),
    ("evaluate_python", t_evaluate_python,
     "Evaluate one Python expression in the Mod Editor and return its repr.",
     S(expression=STR, timeout_seconds=NUM), ["expression"]),
    ("run_python_file", t_run_python_file,
     "Run a .py file inside the Mod Editor. `args` (an object) is available in the script as the ARGS dict.",
     S(path=STR, args=OBJ, timeout_seconds=NUM), ["path"]),
    ("list_mods", t_list_mods,
     "Mods open in the editor: content root, project folder on disk, prefix, local_only, load_after, asset count (and assets with classes).",
     S(include_assets=BOOL), []),
    ("list_mod_templates", t_list_mod_templates,
     "The 36 official mod templates (TP_*): name, category, description. Works without the editor.",
     S(include_files=BOOL), []),
    ("find_assets", t_find_assets,
     "Search the asset registry: base-game assets under /Game, or a mod's under /<ModRoot>.",
     S(path=STR, name_contains=STR, class_name={"type": "string", "description": "Exact class name, e.g. DataTable, Blueprint, WidgetBlueprint."}, limit=INT), []),
    ("get_row", t_get_row,
     "Read a mod row asset (its 'row' struct as export text + field names), or a DataTable (row names, or one row as JSON via row_name).",
     S(asset_path=STR, row_name=STR, limit=INT), ["asset_path"]),
    ("set_row", t_set_row,
     "Edit a mod row asset's 'row' and save. `values`: plain JSON (str/number/bool); `expressions`: Python with `unreal` in scope "
     "for enums/structs/arrays; `import_text`: a whole export_text string. Keys may be dotted paths. Refuses /Game assets.",
     S(asset_path=STR, values=OBJ, expressions=OBJ, import_text=STR, save=BOOL), ["asset_path"]),
    ("save_assets", t_save_assets,
     "Save the given assets, or every dirty package when asset_paths is empty.",
     S(asset_paths={"type": "array", "items": STR}), []),
    ("compile_blueprint", t_compile_blueprint,
     "Compile a Blueprint (saves by default).", S(asset_path=STR, save=BOOL), ["asset_path"]),
    ("validate_assets", t_validate_assets,
     "Run Unreal's asset validators on assets or folders. Passing this does NOT mean the upload Asset Audit passes.",
     S(paths={"type": "array", "items": STR}), ["paths"]),
    ("export_t3d", t_export_t3d,
     "Export a MOD asset (Blueprint/widget) to T3D and report nodes with compile errors or orphaned pins (filter by node_prefix). "
     "Refuses /Game assets (that crashed the editor).",
     S(asset_path=STR, out_file=STR, node_prefix=STR), ["asset_path"]),
    ("t3d_errors", t_t3d_errors,
     "Report nodes with ErrorMsg / orphaned pins in T3D text or a .t3d file. Deleted nodes linger in exports (undo buffer): use a fresh node-name prefix per paste.",
     S(file=STR, text=STR, node_prefix=STR), []),
    ("check_upload", t_check_upload,
     "Offline pre-upload check of a mod project folder (Upload zips the WHOLE folder): .md files (mod.io error 29200), dev files that would ship, "
     "/Game references that exist only in the editor (cloud cook fails), .uplugin version. Give mod_dir, or mod_root of an open mod.",
     S(mod_dir=STR, mod_root=STR), []),
    ("editor_windows", t_editor_windows,
     "List the Mod Editor's open windows (main editor, asset editors, Mod Browser, PIE, dialogs).", S(), []),
    ("editor_screenshot", t_screenshot,
     "Screenshot ONE Mod Editor window (never the desktop) and return the image. Works with the window in the background.",
     S(window=WIN, max_width=INT, save_as=STR), []),
    ("editor_click", t_click,
     "Click in a Mod Editor window. x/y are pixels in that window's latest editor_screenshot image.",
     dict(XY, window=WIN, button={"type": "string", "enum": ["left", "right"]}, double=BOOL), ["x", "y"]),
    ("editor_keys", t_keys,
     "Focus a Mod Editor window (optionally click x/y first), then press key combos (e.g. ['ctrl+s'], ['alt+p'], ['delete']), "
     "type text, and/or press Enter. Keys only go to the editor when it is in front. In PIE hold keys 300 ms+ (hold_ms).",
     dict(XY, window=WIN, keys={"type": "array", "items": STR}, text=STR, press_enter=BOOL, hold_ms=INT), []),
    ("paste_graph", t_paste_graph,
     "Paste Blueprint clipboard text (T3D nodes) into the graph shown in a window, in one step: click the graph at x/y "
     "(default window 600,640), optionally Ctrl+A + Delete first (clear), then Ctrl+V. Paste in passes so custom events exist "
     "and are compiled before callers; calls to missing functions are dropped silently.",
     dict(XY, window=WIN, text=STR, file=STR, clear=BOOL, settle_seconds=NUM), []),
    ("play_in_editor", t_play_in_editor,
     "Start (Alt+P in the main window) or stop Play In Editor. Load a real map first (e.g. /Game/Maps/Facility); close the Message Log first.",
     S(action={"type": "string", "enum": ["start", "stop"]}, wait_seconds=NUM), []),
    ("get_game_row", t_get_game_row,
     "Read a base-game row as export text by kind + name (kind = snake_case of the game's <Kind>HandleFunctionLibrary: "
     "global_item, gear_item, weapon_item, craftable_item, deployable_item, pickup_item, food_and_gibs_item, pet_item, plant_item, "
     "recipe, chemistry_recipe, soup_recipe, buff_debuff, npc, pet, trait, ...). With no name: list row names (name_contains filter).",
     S(kind=STR, name=STR, name_contains=STR, limit=INT), ["kind"]),
    ("create_row_asset", t_create_row_asset,
     "Create a mod row asset (DataAssetFactory) of a *ModRowAsset class at asset_path inside a mod, e.g. "
     "/<Mod>/DataTables/ItemTable_Gear/MyHelmet with class GearItemModRowAsset. Optionally copy the row from a base-game row "
     "(copy_from_kind + copy_from_name, struct types must match) or from another mod row asset (copy_from_asset). "
     "row_name defaults to the asset name (the game adds the mod prefix). Then edit with set_row.",
     S(class_name=STR, asset_path=STR, copy_from_kind=STR, copy_from_name=STR, copy_from_asset=STR, row_name=STR, overwrite=BOOL),
     ["class_name", "asset_path"]),
    ("delete_asset", t_delete_asset,
     "Delete ONE mod asset. Never call it in a loop over many assets (that crashed the editor); delete a few, then let the editor settle.",
     S(asset_path=STR), ["asset_path"]),
    ("inspect_blueprint", t_inspect_blueprint,
     "Structure of a MOD Blueprint from its T3D export: parent class, variables, graphs with node counts, every function/event/variable "
     "reference, nodes with compile errors or orphaned pins, and (by default) the node list. Filter by graph or node_prefix.",
     S(asset_path=STR, graph=STR, node_prefix=STR, include_nodes=BOOL, include_pins=BOOL, limit=INT), ["asset_path"]),
    ("blueprint_filter_check", t_blueprint_filter_check,
     "Check mod Blueprints against Deep Field's Blueprint filter (the list the upload Asset Audit enforces; cached in data/, refreshed daily): "
     "lists every Blocked function/variable/class reference with its node and the rule that blocks it. Pass asset_paths or mod_root.",
     S(asset_paths={"type": "array", "items": STR}, mod_root=STR), []),
    ("load_map", t_load_map,
     "Load a map in the editor, e.g. /Game/Maps/Facility (needed before Play In Editor; a fresh mod opens on Empty).",
     S(map_path=STR, timeout_seconds=NUM), ["map_path"]),
    ("console_command", t_console_command,
     "Run an Unreal console command in the editor world or the running PIE game world (world='pie'), e.g. 'stat fps', 'slomo 2'.",
     S(command=STR, world={"type": "string", "enum": ["editor", "pie"]}), ["command"]),
    ("pie_info", t_pie_info,
     "While Play In Editor runs: map, player controllers, pawn classes and locations; optional pawn_properties to read (e.g. ['Invincible']).",
     S(pawn_properties={"type": "array", "items": STR}), []),
    ("close_editor_window", t_close_window,
     "Close one editor window by title (Message Log, a dialog, an asset editor, the PIE window). Refuses the main editor window.",
     S(window=STR), ["window"]),
    ("read_editor_log", t_read_editor_log,
     "Tail of the Mod Editor log, optional regex filter (works with the editor closed). Upload/mod.io errors are under LogModio.",
     S(lines=INT, contains={"type": "string", "description": "Case-insensitive regex."}, file=STR), []),
]
TOOL_MAP = {t[0]: t for t in TOOLS}

INSTRUCTIONS = (
    "Tools for the official Abiotic Factor Mod Editor (custom Unreal 5.4.4 cooked editor, Python 3.11). Start with editor_status. "
    "Mods are plugins with an AbioticModConfig and per-row assets (e.g. /<Mod>/DataTables/ItemTable_Gear/<Row>) whose data is the 'row' property. "
    "Never edit or save /Game (base content); never T3D-export /Game Blueprints (crash); never delete many assets in one loop (crash). "
    "Python cannot create Blueprint nodes: generate T3D and use paste_graph, then compile and export_t3d to check. "
    "Copied game Blueprints must pass the upload Asset Audit (Blueprint filter) and the cloud cook; run check_upload before an upload. "
    "Screenshots are per editor window only. The user does mod.io uploads."
)


# ---------------------------------------------------------------------------
# MCP stdio (JSON-RPC 2.0, one message per line)
# ---------------------------------------------------------------------------

def send(msg):
    sys.stdout.buffer.write(json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def handle(req):
    method = req.get("method")
    params = req.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d,
                           "inputSchema": {"type": "object", "properties": p, "required": r}}
                          for n, _, d, p, r in TOOLS]}
    if method == "tools/call":
        tool = TOOL_MAP.get(params.get("name"))
        if not tool:
            return {"content": [{"type": "text", "text": f"Unknown tool {params.get('name')}"}], "isError": True}
        try:
            res = tool[1](params.get("arguments") or {})
            if isinstance(res, dict) and "content" in res:
                return res
            text, is_error = res if isinstance(res, tuple) else (res, False)
        except (RemoteError, wininput.WinError) as e:
            text, is_error = str(e), True
        except Exception:
            text, is_error = traceback.format_exc(), True
        return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}
    if req.get("id") is None:
        return None          # notification
    raise LookupError(method)


def main():
    log(f"starting {SERVER_VERSION}; editor install {EDITOR_ROOT}")
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8"))
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        rid = req.get("id")
        try:
            result = handle(req)
            if rid is not None:
                send({"jsonrpc": "2.0", "id": rid, "result": result})
        except LookupError as e:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {e}"}})
        except Exception as e:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
