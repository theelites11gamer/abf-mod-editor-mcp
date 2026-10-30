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
import calendar
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
import graphbuild  # noqa: E402
import paths     # noqa: E402

SERVER_NAME = "abiotic-mod-editor"
SERVER_VERSION = "2.3.1"

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


def execute(code=None, mode="exec", timeout=120.0, script=None, script_args=None, remote_only=False):
    """Run Python in the editor via remote execution, falling back to the file bridge.

    remote_only: no fallback. Remote execution stops answering while a modal dialog is open, but the bridge keeps
    running behind the dialog, and deleting an asset there has crashed the editor.
    """
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
            if remote_only:
                raise RemoteError("The editor isn't answering, so this was not run. A dialog is probably open: close it and try again. "
                                  f"Windows: {[w['title'] for w in wininput.editor_windows()]}")
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


def run_json(body, args=None, timeout=120.0, remote_only=False):
    """Run a snippet that defines `def _abf(args): ...` in the editor and return its JSON result."""
    code = (
        "import unreal, json\n"
        f"{body}\n"
        f"_abf_args = json.loads({json.dumps(json.dumps(args or {}))})\n"
        f"print({JSON_MARKER!r} + json.dumps(_abf(_abf_args), default=str))\n"
    )
    r = execute(code, timeout=timeout, remote_only=remote_only)
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
        return {"error": "asset not found: " + args["asset_path"]}
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    return {"blueprint": bp.get_path_name()}
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
    failures, _settings, results = sub.validate_assets_with_settings(datas)
    report = {k: getattr(results, "num_" + k, None) for k in ("requested", "checked", "valid", "invalid", "warnings", "skipped")}
    report["failures"] = failures
    return report
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

PY_OPEN_ASSET = r'''
def _abf(args):
    asset = unreal.load_asset(args["asset_path"])
    if asset is None:
        return {"error": "asset not found: " + args["asset_path"]}
    unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).open_editor_for_assets([asset])
    return {"asset": asset.get_path_name(), "name": asset.get_name(), "class": asset.get_class().get_name(),
            "widget": isinstance(asset, unreal.WidgetBlueprint)}
'''

PY_CLOSE_ASSET = r'''
def _abf(args):
    asset = unreal.load_asset(args["asset_path"])
    if asset is None:
        return {"error": "asset not found: " + args["asset_path"]}
    unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).close_all_editors_for_asset(asset)
    return {"closed": asset.get_path_name()}
'''

PY_ASSET_BY_NAME = PY_MOD_ROOTS + r'''
def _abf(args):
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    hits = []
    for root in _mod_roots():
        for ad in reg.get_assets_by_path(root, recursive=True):
            if str(ad.asset_name) == args["name"]:
                hits.append(str(ad.package_name))
    return {"assets": hits}
'''

# The editor's filter says what it blocks, per class. It prints to the log.
PY_FILTER_DUMP = r'''
def _abf(args):
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    for path in args["classes"]:
        unreal.SystemLibrary.execute_console_command(world, "ABF.BlueprintFilter.DumpClass " + path)
    return {"asked": len(args["classes"])}
'''

# A throwaway Blueprint: never saved and outside the mod, so it can't ship. /Temp is refused as an asset path here.
PY_PROBE_CREATE = r'''
def _abf(args):
    parent = None
    if args.get("parent_asset"):
        bp = unreal.load_asset(args["parent_asset"])
        if bp is not None:
            parent = unreal.BlueprintEditorLibrary.generated_class(bp)
    if parent is None and args.get("parent_class"):
        parent = unreal.load_class(None, args["parent_class"])
    if parent is None:
        parent = unreal.Actor.static_class()
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    widget = unreal.MathLibrary.class_is_child_of(parent, unreal.UserWidget.static_class())
    factory = unreal.WidgetBlueprintFactory() if widget else unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", parent)
    asset = tools.create_asset(args["name"], "/Engine/Transient", unreal.WidgetBlueprint if widget else unreal.Blueprint, factory)
    if asset is None:
        return {"error": "could not create a probe Blueprint with parent " + parent.get_path_name()}
    unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).open_editor_for_assets([asset])
    return {"asset": asset.get_outermost().get_name(), "parent": parent.get_path_name(), "widget": widget}
'''

PY_PROBE_REMOVE = r'''
def _abf(args):
    asset = unreal.load_asset(args["asset_path"])
    if asset is None:
        return {"removed": False}
    unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).close_all_editors_for_asset(asset)
    return {"removed": bool(unreal.EditorAssetLibrary.delete_asset(args["asset_path"]))}
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
    parsed = bpscan.parse_t3d(text)
    nodes = [n for n in parsed["nodes"] if not prefix or n["name"].startswith(prefix)]
    problems = [{"node": n["name"], "class": n["class"], "graph": n.get("graph"), "error": n.get("error"),
                 "orphaned_pins": n.get("orphaned_pins", [])}
                for n in nodes if n.get("error") or n.get("orphaned_pins")]
    warnings = [{"node": n["name"], "graph": n.get("graph"), "warning": n["warning"]} for n in nodes if n.get("warning")]
    return {"nodes_checked": len(nodes), "deleted_or_generated_nodes_ignored": parsed["dead_nodes"], "problems": problems, "warnings": warnings}


# ---- editor log -------------------------------------------------------------
# The log grows past 100 MB in a long session, so it's read from the end, a block at a time.

LOG_STAMP = re.compile(r"^\[(\d{4})\.(\d\d)\.(\d\d)-(\d\d)\.(\d\d)\.(\d\d):")


def log_file(name=None):
    """The named log, or the one the running editor writes: a second editor instance uses AbioticFactor_2.log."""
    folder = SAVED_DIR / "Logs"
    if name:
        return folder / name
    current = [p for p in folder.glob("AbioticFactor*.log") if "-backup-" not in p.name]
    return max(current, key=lambda p: p.stat().st_mtime) if current else folder / "AbioticFactor.log"


def log_mark():
    """(file, size): where the log ends now, for log_since."""
    path = log_file()
    return path, path.stat().st_size if path.is_file() else 0


def log_since(mark):
    """Complete lines written after log_mark()."""
    path, offset = mark
    if not path.is_file():
        return []
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    return data.decode("utf-8", "replace").splitlines()


def tail_log(path, want, rx=None, since=None, max_bytes=64 << 20):
    """Newest `want` matching lines, oldest first. Returns (lines, whole file searched?). since: a UTC epoch."""
    out = []
    with open(path, "rb") as f:
        f.seek(0, 2)
        pos, scanned, partial = f.tell(), 0, b""
        while pos > 0 and len(out) < want and scanned < max_bytes:
            step = min(1 << 20, pos)
            pos -= step
            scanned += step
            f.seek(pos)
            lines = (f.read(step) + partial).split(b"\n")
            partial = lines.pop(0) if pos > 0 else b""
            for raw in reversed(lines):
                line = raw.decode("utf-8", "replace").rstrip("\r")
                if since is not None:
                    m = LOG_STAMP.match(line)
                    if m and calendar.timegm(tuple(int(v) for v in m.groups())) < since:
                        return list(reversed(out)), True
                if line and (rx is None or rx.search(line)):
                    out.append(line)
                    if len(out) >= want:
                        break
    return list(reversed(out)), pos == 0 or len(out) >= want


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


def compile_blueprint(path, save=True, save_on_error=False):
    """Compile and report what the compiler said. A Blueprint with errors isn't saved unless asked."""
    mark = log_mark()
    r = run_json(PY_COMPILE, {"asset_path": path}, timeout=300)
    if r.get("error"):
        return r
    time.sleep(0.8)                 # the log file is written a moment after the compile
    name = path.rsplit("/", 1)[-1]
    errors, warnings = [], []
    for line in log_since(mark):
        if "[Compiler]" in line and name in line:
            text = line.split("[Compiler]", 1)[1].strip()
            (errors if "Error:" in line.split("[Compiler]", 1)[0] else warnings).append(text)
    r.update(ok=not errors, errors=errors, warnings=warnings)
    if save and (r["ok"] or save_on_error):
        r["saved"] = bool(run_json(PY_SAVE, {"asset_paths": [path]}).get(path))
    else:
        r["saved"] = False
    return r


def t_compile_blueprint(args):
    r = compile_blueprint(args["asset_path"], args.get("save", True), bool(args.get("save_on_error")))
    return json.dumps(r, indent=2), not r.get("ok")


def t_validate_assets(args):
    mark = log_mark()
    r = run_json(PY_VALIDATE, args, timeout=300)
    time.sleep(0.5)
    r["messages"] = [l for l in log_since(mark) if re.search(r"(LogContentValidation|AssetCheck|DataValidation): (Error|Warning)", l)][-40:]
    return json.dumps(r, indent=2, default=str), bool(r.get("invalid"))


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


def asset_window(name):
    """The asset editor window for this asset name, if it has its own."""
    wins = [w for w in wininput.editor_windows() if name.lower() in w["title"].lower() and "Unreal Editor" not in w["title"]]
    exact = [w for w in wins if w["title"].rstrip("*").strip().lower() == name.lower()]
    return (exact or wins or [None])[0]


def open_asset_window(path, wait=20.0):
    """Open an asset's editor; returns (window, info). A user setting can dock editors in the main window instead."""
    info = run_json(PY_OPEN_ASSET, {"asset_path": path}, timeout=120)
    if info.get("error"):
        raise RemoteError(info["error"])
    deadline = time.time() + wait
    while time.time() < deadline:
        win = asset_window(info["name"])
        if win:
            return win, info
        time.sleep(0.3)
    return wininput.find_window(), info


def graph_point(win, widget=False):
    """A window pixel on empty graph: past the greyed-out default events in the middle of the panel, because a click on
    one of their comment bubbles would paste the text into the comment. A widget's graph panel is wider."""
    l, t, r, b = win["rect"]
    return int((r - l) * (0.80 if widget else 0.68)), int((b - t) * 0.40)


def show_event_graph(win, widget=False):
    """A new Blueprint opens on its Viewport tab and a widget on its Designer, so bring the graph forward. These are
    positions in the default layout. A click up there can't change anything, and callers check by reading the Blueprint back."""
    s = wininput.ui_scale(win)
    width = win["rect"][2] - win["rect"][0]
    if widget:
        wininput.click(win, int(width - 62 * s), int(80 * s))         # the Graph button, top right
    else:
        wininput.click(win, int(width * 0.15 + 394 * s), int(118 * s))  # the Event Graph tab, right of the left column
    time.sleep(0.8)


def paste_into(win, text, x, y, clear=False, settle=3.0):
    # One process for click, select-all, delete and paste, so the graph keeps keyboard focus.
    wininput.set_clipboard(text)
    wininput.click(win, x, y)
    time.sleep(0.5)
    if clear:
        wininput.keys("ctrl+a")
        time.sleep(0.6)
        wininput.keys("delete")
        time.sleep(0.8)
        wininput.click(win, x, y)
        time.sleep(0.4)
    wininput.keys("ctrl+v")
    time.sleep(settle)


def window_titles():
    return {w["title"] for w in wininput.editor_windows()}


def new_dialogs(before):
    """Windows that appeared since `before`: a paste can open one, like Fix Self Context Function References for a call
    to a function the Blueprint doesn't have. It blocks the editor until someone answers it."""
    return sorted(window_titles() - before - {"Message Log"})


DIALOG_OPEN = ("The paste opened a dialog, which blocks the editor until it's answered. Read it with editor_screenshot, then close it with "
               "close_editor_window. A call to a function the Blueprint doesn't have does this: paste and compile the function first.")
NOTHING_ARRIVED = ("None of the nodes arrived, even with the Event Graph brought forward. Check the window with editor_screenshot: a graph has to be "
                   "showing (a data-only Blueprint needs 'Open Full Blueprint Editor' first), then pass x/y on empty graph.")


def paste_checked(text, asset=None, win=None, xy=None, clear=False, settle=3.0, end_comment=True, compile_it=False, save=True):
    """Paste clipboard text, then read the Blueprint back and report what actually arrived. (report, is_error)"""
    if end_comment:
        text, _ = graphbuild.ensure_end_comment(text)
    check = graphbuild.lint(text)
    report = {"lint_warnings": check["warnings"]}
    if check["errors"]:
        report.update(ok=False, pasted=False, lint_errors=check["errors"])
        return report, True
    if asset is None and win is not None and "Unreal Editor" not in win["title"]:
        hits = run_json(PY_ASSET_BY_NAME, {"name": win["title"].rstrip("*").strip()}, timeout=60)["assets"]
        asset = hits[0] if len(hits) == 1 else None
    widget = False
    if asset:
        opened, info = open_asset_window(asset)
        win, widget = win or opened, info["widget"]
    elif win is None:
        win = wininput.find_window()
    x, y = xy or graph_point(win, widget)
    report.update(window=win["title"], at=[x, y])
    if not asset:
        if clear:
            report.update(ok=False, pasted=False, note="clear needs asset_path: without it, nothing can confirm a graph has focus before Ctrl+A and Delete.")
            return report, True
        paste_into(win, text, x, y, settle=settle)
        report.update(ok=None, pasted=True, verified=False, note="Pasted but not checked: pass asset_path so the result can be read back.")
        return report, False

    def attempt(body, wait):
        """Paste and read back. If nothing arrived the graph wasn't showing, so bring it forward once and try again."""
        titles = window_titles()
        paste_into(win, body, x, y, settle=wait)
        dialogs = new_dialogs(titles)
        if dialogs:
            return {"nodes_found": 0, "ok": False, "dialogs": dialogs}
        result = graphbuild.verify(body, export_mod_t3d(asset))
        if not result["nodes_found"] and not xy:
            show_event_graph(win, widget)
            paste_into(win, body, x, y, settle=wait)
            result = graphbuild.verify(body, export_mod_t3d(asset))
            result["brought_graph_forward"] = True
        return result

    before = bpscan.parse_t3d(export_mod_t3d(asset))
    if clear:
        marker = attempt(graphbuild.comment_block(), 1.0)
        if not marker["nodes_found"]:
            report.update(ok=False, pasted=False, hint=DIALOG_OPEN if marker.get("dialogs") else NOTHING_ARRIVED, dialogs=marker.get("dialogs", []))
            return report, True
        # The marker landed, so a graph has focus: Ctrl+A and Delete can't reach components on the Viewport tab.
        wininput.keys("ctrl+a")
        time.sleep(0.6)
        wininput.keys("delete")
        time.sleep(0.8)
    result = attempt(text, settle)
    report.update(result, pasted=True, chars=len(text))
    if result.get("dialogs"):
        report.update(verified=False, hint=DIALOG_OPEN)
        return report, True
    report["verified"] = True
    sent = {n["name"] for n in bpscan.parse_t3d(text)["nodes"]}
    reused = sorted({n["name"] for n in before["nodes"]} & sent)
    if reused and not clear:
        report["note"] = (f"{len(reused)} node names were already in the Blueprint, so the editor renamed the new copies and they read as "
                          "missing. Use a new node-name prefix for each paste (build_graph does).")
    if not result["nodes_found"]:
        report["hint"] = NOTHING_ARRIVED
    is_error = not result["ok"]
    if compile_it and result["ok"]:
        c = compile_blueprint(asset, save)
        report["compile"] = c
        is_error = not c.get("ok")
        if not c.get("ok"):
            report["compile_node_problems"] = t3d_errors(export_mod_t3d(asset))["problems"]
    return report, is_error


def t_paste_graph(args):
    text = args.get("text") or Path(args["file"]).read_text(encoding="utf-8-sig")
    win = _window(args) if args.get("window") else None
    xy = None
    if "x" in args and "y" in args:
        win = win or (open_asset_window(args["asset_path"])[0] if args.get("asset_path") else wininput.find_window())
        xy = _scaled(win, args)
    report, is_error = paste_checked(text, args.get("asset_path"), win, xy, bool(args.get("clear")), float(args.get("settle_seconds", 3)),
                                     args.get("end_comment", True), bool(args.get("compile")), args.get("save", True))
    return json.dumps(report, indent=2), is_error


def t_open_asset(args):
    win, info = open_asset_window(args["asset_path"], float(args.get("wait_seconds", 20)))
    if args.get("show_graph"):
        show_event_graph(win, info["widget"])
    return json.dumps({"asset": info["asset"], "class": info["class"], "window": win["title"],
                       "docked_in_main_window": "Unreal Editor" in win["title"], "graph_point": graph_point(win, info["widget"])}, indent=2)


def t_close_asset(args):
    return _json(run_json(PY_CLOSE_ASSET, args, timeout=60))


# ---- building graphs from a node list ------------------------------------------

def editor_build():
    """Identifies the installed editor, so templates from an older build aren't reused."""
    try:
        bv = json.loads((EDITOR_ROOT / "Engine" / "Build" / "Build.version").read_text(encoding="utf-8-sig"))
        return f"{bv.get('MajorVersion')}.{bv.get('MinorVersion')}.{bv.get('PatchVersion')}-{bv.get('Changelist')}"
    except (OSError, ValueError, TypeError):
        return "unknown"


TEMPLATES = DATA / "node-templates.json"


def load_templates():
    build = editor_build()
    try:
        saved = json.loads(TEMPLATES.read_text(encoding="utf-8"))
        if saved.get("build") == build:
            return saved["nodes"]
    except (OSError, ValueError, KeyError):
        pass
    return {}


def save_templates(nodes):
    TEMPLATES.parent.mkdir(exist_ok=True)
    TEMPLATES.write_text(json.dumps({"build": editor_build(), "nodes": nodes}), encoding="utf-8")


def probe_nodes(nodes, parent_asset=None, parent_class=None):
    """Paste nodes bare into a throwaway Blueprint and read back what the editor allocated: {template key: template}."""
    run = "McpProbe" + uuid.uuid4().hex[:8]
    items, keys = [], {}
    for n in nodes:
        key = graphbuild.template_key(n)
        if key not in keys:
            keys[key] = f"{run}_{len(items)}"
            items.append((keys[key], n))
    info = run_json(PY_PROBE_CREATE, {"name": "BP_" + run, "parent_asset": parent_asset, "parent_class": parent_class}, timeout=120)
    if info.get("error"):
        raise RemoteError(info["error"])
    removable = True
    try:
        deadline = time.time() + 20
        win = None
        while win is None and time.time() < deadline:
            win = asset_window("BP_" + run)
            time.sleep(0.3)
        if win is None:
            raise RemoteError("The throwaway Blueprint's window didn't appear.")
        show_event_graph(win, info["widget"])
        text, _ = graphbuild.ensure_end_comment(graphbuild.bare_text(items))
        titles = window_titles()
        # Clearing removes the default events: a pasted copy of one would turn into a custom event.
        paste_into(win, text, *graph_point(win, info["widget"]), clear=True, settle=1.5 + len(items) * 0.03)
        dialogs = new_dialogs(titles)
        if dialogs:
            removable = False       # deleting behind a dialog crashes the editor; the probe isn't saved, so it can stay
            raise RemoteError(f"Reading these nodes opened a dialog: {dialogs}. {DIALOG_OPEN}")
        export = export_mod_t3d(info["asset"])
        arrived = [n for n in bpscan.parse_t3d(export)["nodes"] if n["class"] == "EdGraphNode_Comment" and n.get("graph") == "EventGraph"]
        if not arrived:         # the end comment always pastes, so this is the window and not the nodes
            raise RemoteError("Couldn't paste into the throwaway Blueprint's Event Graph: its window may not use the default layout. "
                              "Reset it with Window > Load Layout > Default Editor Layout, or pass full pins with type 'raw'.")
        found = graphbuild.templates_from_export(export, [name for name, _ in items])
    finally:
        if removable:
            run_json(PY_PROBE_REMOVE, {"asset_path": info["asset"]}, timeout=60, remote_only=True)
    return {key: found.get(name) for key, name in keys.items()}


def templates_for(nodes, parent_asset=None, parent_class=None):
    """({node id: template}, how many nodes needed the editor)."""
    saved = load_templates()
    need = [n for n in nodes if not (graphbuild.is_stable(n) and graphbuild.template_key(n) in saved)]
    fresh = probe_nodes(need, parent_asset, parent_class) if need else {}
    if any(graphbuild.is_stable(n) and fresh.get(graphbuild.template_key(n)) for n in need):
        for n in need:
            key = graphbuild.template_key(n)
            if graphbuild.is_stable(n) and fresh.get(key):
                saved[key] = fresh[key]
        save_templates(saved)
    return {n["id"]: fresh.get(graphbuild.template_key(n)) or saved.get(graphbuild.template_key(n)) for n in nodes}, len(need)


def named_nodes(nodes, asset=None):
    """Copies of the spec nodes, with an id on each. A call with no owner means the Blueprint's own function, and names
    that class outright: a self-context call to a function that isn't there opens a dialog that blocks the editor."""
    out = []
    for i, n in enumerate(nodes):
        n = dict(n, id=n.get("id") or f"n{i}")
        if not n.get("owner") and n.get("function") and n.get("type", "call") == "call":
            if not asset:
                raise graphbuild.SpecError(f"node {n['id']!r}: a call with no owner is the Blueprint's own function: pass asset_path, or give an owner.")
            name = asset.rstrip("/").rsplit("/", 1)[-1]
            n["owner"] = f"{asset}.{name}_C"
        out.append(n)
    return out


def t_discover_pins(args):
    try:
        nodes = named_nodes(args["nodes"], args.get("asset_path"))
        for n in nodes:
            graphbuild.describe(n)
    except graphbuild.SpecError as e:
        return str(e), True
    templates, probed = templates_for(nodes, args.get("asset_path"), args.get("parent_class"))
    out = []
    for n in nodes:
        t = templates.get(n["id"])
        if t is None:
            out.append({"id": n["id"], "error": "The editor refused this node: wrong class for that function, or it doesn't exist."})
        else:
            e = {"id": n["id"], "class": t["class"], "pins": graphbuild.pin_table(t)}
            if t.get("error"):
                e["error"] = t["error"]
            out.append(e)
    return json.dumps({"nodes": out, "asked_editor_for": probed}, indent=2), any("pins" not in e for e in out)


def blocked_calls(text):
    """What the editor's Blueprint filter would block in this clipboard text."""
    nodes = bpscan.parse_t3d(text)["nodes"]
    result = bpscan.check_nodes(nodes, filter_dumps(bpscan.classes_used(nodes)))
    return [{"class": b["parent"], "member": b["member"], "kind": b["kind"]} for b in result["blocked"]]


def t_build_graph(args):
    spec = {}
    if args.get("spec_file"):
        spec = json.loads(Path(args["spec_file"]).read_text(encoding="utf-8-sig"))
    links = args.get("links") or spec.get("links") or []
    if not (args.get("nodes") or spec.get("nodes")):
        return "Pass nodes (and links), or spec_file: a JSON file holding both.", True
    if args.get("paste") and not args.get("asset_path"):
        return "paste=true needs asset_path, the Blueprint to paste into.", True
    prefix = args.get("prefix") or "G" + format(int(time.time()) % 36 ** 6, "x").upper()
    try:
        nodes = named_nodes(args.get("nodes") or spec.get("nodes"), args.get("asset_path"))
        for n in nodes:
            graphbuild.describe(n)
    except graphbuild.SpecError as e:
        return str(e), True
    templates, probed = templates_for(nodes, args.get("asset_path"), args.get("parent_class"))
    built = graphbuild.assemble(nodes, links, templates, prefix)
    report = {"prefix": prefix, "nodes": len(nodes), "links": built["links"], "asked_editor_for": probed,
              "problems": built["problems"], "warnings": built["warnings"]}
    if built["problems"]:
        report["ok"] = False
        return json.dumps(report, indent=2), True
    out = Path(args.get("out_file") or (HERE / "exports" / "graphs" / f"{prefix}.t3d"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(built["text"], encoding="utf-8")
    report.update(file=str(out), node_names=built["names"])
    try:
        report["blocked_by_upload_filter"] = blocked_calls(built["text"])
    except RemoteError as e:                 # the graph is still good; only this check is missing
        report["blocked_by_upload_filter"] = None
        report["filter_note"] = f"Couldn't ask the editor about the upload filter: {e}"
    if not args.get("paste"):
        report["ok"] = True
        return json.dumps(report, indent=2), False
    pasted, is_error = paste_checked(built["text"], args["asset_path"], clear=bool(args.get("clear")),
                                     compile_it=bool(args.get("compile")), save=args.get("save", True))
    report["paste"] = pasted
    report["ok"] = not is_error
    return json.dumps(report, indent=2), is_error


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


def export_mod_t3d(path):
    if path.startswith("/Game/"):
        raise RemoteError("Refusing: T3D-exporting a /Game Blueprint crashed this editor before. Export mod assets only.")
    out = HERE / "exports" / (path.strip("/").replace("/", "_") + ".t3d")
    out.parent.mkdir(parents=True, exist_ok=True)
    r = run_json(PY_EXPORT_T3D, {"asset_path": path, "out_file": str(out).replace("\\", "/")}, timeout=120)
    if not r.get("ok"):
        raise RemoteError("export failed: " + json.dumps(r))
    return out.read_text(encoding="utf-8", errors="replace")


def t_inspect_blueprint(args):
    parsed = bpscan.parse_t3d(export_mod_t3d(args["asset_path"]), include_dead=bool(args.get("include_deleted")))
    nodes = parsed["nodes"]
    if args.get("graph"):
        nodes = [n for n in nodes if n.get("graph") == args["graph"]]
    if args.get("node_prefix"):
        nodes = [n for n in nodes if n["name"].startswith(args["node_prefix"])]
    out = {"blueprint": parsed["blueprint"], "graphs": parsed["graphs"], "node_count": len(nodes), "deleted_or_generated_nodes_not_counted": parsed["dead_nodes"]}
    calls = {}
    for n in nodes:
        for key in ("function", "event", "variable", "macro"):
            r = n.get(key)
            if r:
                calls.setdefault(key, set()).add((r.get("parent") or "self") + "." + r["name"])
    out["references"] = {k: sorted(v) for k, v in calls.items()}
    out["problems"] = [{"node": n["name"], "error": n.get("error"), "orphaned_pins": n.get("orphaned_pins")} for n in nodes if n.get("error") or n.get("orphaned_pins")]
    out["warnings"] = [{"node": n["name"], "warning": n["warning"]} for n in nodes if n.get("warning")]
    if args.get("include_nodes", True):
        slim = []
        for n in nodes[: int(args.get("limit") or 400)]:
            e = {"name": n["name"], "class": n["class"], "graph": n.get("graph")}
            if "live" in n:
                e["live"] = n["live"]
            for key in ("function", "event", "variable", "macro", "customFunctionName", "inputKey", "structType"):
                if n.get(key):
                    e[key] = n[key]
            if args.get("include_pins"):
                e["pins"] = n["pins"]
            slim.append(e)
        out["nodes"] = slim
    return json.dumps(out, indent=2)


_FILTER_DUMPS = {}


def filter_dumps(classes):
    """{class: what the editor's Blueprint filter says about it}, or None for a class it can't find.
    Game and engine classes are remembered: the rules only change when the editor restarts."""
    todo = [c for c in classes if c not in _FILTER_DUMPS]
    found = {}
    if todo:
        mark = log_mark()
        run_json(PY_FILTER_DUMP, {"classes": todo}, timeout=120)
        deadline = time.time() + 6
        while time.time() < deadline:
            time.sleep(0.4)             # the log file is written a moment later
            found = bpscan.parse_filter_dumps(log_since(mark))
            if all(c in found for c in todo):
                break
        for c in todo:
            if c.startswith(("/Script/", "/Game/")) and found.get(c) is not None:
                _FILTER_DUMPS[c] = found[c]
    return {c: _FILTER_DUMPS.get(c, found.get(c)) for c in classes}


def t_blueprint_filter_check(args):
    paths = list(args.get("asset_paths") or [])
    if args.get("mod_root"):
        found = run_json(PY_FIND_ASSETS, {"path": args["mod_root"], "limit": 2000}, timeout=120)
        paths += [a["path"] for a in found["assets"] if a["class"] in ("Blueprint", "WidgetBlueprint", "AnimBlueprint")]
    if not paths:
        return "Pass asset_paths (mod Blueprints) or mod_root.", True
    report = {"rules": None, "checked": [], "blocked": [], "not_checked": []}
    for p in paths:
        parsed = bpscan.parse_t3d(export_mod_t3d(p))
        dumps = filter_dumps(bpscan.classes_used(parsed["nodes"]))
        res = bpscan.check_nodes(parsed["nodes"], dumps)
        report["rules"] = report["rules"] or next((d["rules"] for d in dumps.values() if d), None)
        report["checked"].append({"asset": p, "nodes": len(parsed["nodes"]), "blocked": len(res["blocked"])})
        for b in res["blocked"]:
            b["asset"] = p
            report["blocked"].append(b)
        report["not_checked"] += [c for c in res["not_checked"] if c not in report["not_checked"]]
    report["verdict"] = "BLOCKED REFERENCES" if report["blocked"] else "OK"
    report["note"] = ("The editor's own Blueprint filter rules, applied to the function, event, variable and event dispatcher references of live "
                      "nodes, as its Asset Audit does. not_checked: classes the editor couldn't find. Parent-class override signatures and the "
                      "cloud cook's editor-only checks aren't covered, so run check_upload too.")
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
    return _json(run_json(PY_DELETE_ASSET, args, timeout=60, remote_only=True))


def t_close_window(args):
    win = wininput.find_window(args["window"])
    wininput.close_window(win)
    still = [w["title"] for w in wininput.editor_windows()]
    return f"Sent close to '{win['title']}'. Open now: {still}"


def t_read_editor_log(args):
    path = log_file(args.get("file"))
    if not path.is_file():
        logs = sorted(p.name for p in (SAVED_DIR / "Logs").glob("*.log"))
        return f"No log at {path}. Available: {logs}", True
    rx = re.compile(args["contains"], re.I) if args.get("contains") else None
    since = time.time() - float(args["since_minutes"]) * 60 if args.get("since_minutes") else None
    n = int(args.get("lines", 100))
    lines, whole = tail_log(path, n, rx, since, int(float(args.get("max_scan_mb", 64)) * (1 << 20)))
    note = "" if whole else " (stopped searching at max_scan_mb; older lines not read)"
    return f"{path}: last {len(lines)} matching lines{note}\n" + "\n".join(lines)


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
     "Compile a Blueprint and report the compiler's errors and warnings (ok, errors, warnings). Saves by default, "
     "but not when there are errors unless save_on_error.", S(asset_path=STR, save=BOOL, save_on_error=BOOL), ["asset_path"]),
    ("validate_assets", t_validate_assets,
     "Run Unreal's asset validators on assets or folders. Passing this does NOT mean the upload Asset Audit passes.",
     S(paths={"type": "array", "items": STR}), ["paths"]),
    ("export_t3d", t_export_t3d,
     "Export a MOD asset (Blueprint/widget) to T3D and report live nodes with compile errors or orphaned pins (filter by node_prefix). "
     "The file also holds deleted nodes and graphs the compiler generates; the report ignores them. Refuses /Game assets (that crashed the editor).",
     S(asset_path=STR, out_file=STR, node_prefix=STR), ["asset_path"]),
    ("t3d_errors", t_t3d_errors,
     "Report live nodes with ErrorMsg / orphaned pins in T3D text or a .t3d file.",
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
     "Paste Blueprint clipboard text (T3D nodes) into the graph showing in a Blueprint's window, then read the Blueprint back and "
     "report any nodes or links that didn't arrive, and which graph they landed in. Give asset_path: it opens the Blueprint and enables the "
     "check, and brings the Event Graph forward if nothing arrives (for another graph, open its tab first). Lints first (duplicate pins, broken links) "
     "and adds a comment node at the end, because the editor drops the links of the last pasted node. clear = delete what's in the showing graph first "
     "(needs asset_path). compile = compile if the paste is clean. Paste in passes so custom events exist and are compiled before callers; calls to "
     "missing functions are dropped. Prefer build_graph for new logic.",
     dict(XY, window=WIN, asset_path=STR, text=STR, file=STR, clear=BOOL, settle_seconds=NUM, end_comment=BOOL, compile=BOOL, save=BOOL), []),
    ("open_asset", t_open_asset,
     "Open an asset's editor window (Blueprint, widget, material...) and return its window title for the other window tools. "
     "show_graph brings a Blueprint's Event Graph or a widget's Graph view forward; new ones open on the Viewport or Designer.",
     S(asset_path=STR, show_graph=BOOL, wait_seconds=NUM), ["asset_path"]),
    ("close_asset", t_close_asset,
     "Close every editor window open on an asset. Doesn't save.", S(asset_path=STR), ["asset_path"]),
    ("discover_pins", t_discover_pins,
     "The real pins of Blueprint nodes (name, direction, type, class, default), read from the editor: nodes are pasted into a throwaway "
     "Blueprint that is never saved and is deleted afterwards. Nodes use build_graph's format, e.g. {owner:'/Script/Engine.KismetSystemLibrary', function:'PrintString'}. "
     "Give asset_path to resolve that Blueprint's own functions and variables. Takes focus for a few seconds unless every answer is cached.",
     S(nodes={"type": "array", "items": OBJ}, asset_path=STR, parent_class=STR), ["nodes"]),
    ("build_graph", t_build_graph,
     "Build Blueprint nodes from a list, no T3D writing needed. Every pin name is checked against the pins the editor really allocates, "
     "and a wrong one is an error listing the real names. Returns the file and what the upload filter would block. With paste=true (needs asset_path) "
     "it also pastes and verifies; compile=true compiles a clean paste. Paste custom events and compile before pasting callers.\n"
     "nodes: [{id, type, x?, y?, args?: {pin: literal default}, element?: {cat, obj?} for wildcard pins}]. Types and their fields: "
     "call {owner?, function} (no owner = this Blueprint's own); array_call {function}; call_parent {owner, function}; event {owner, function}; "
     "custom_event {name, params?: [{name, cat, obj?, array?}], rpc?: server|client|multicast, reliable?}; var_get / var_set {name, owner?}; cast {target}; branch; "
     "sequence {outputs?}; self; make_array {count, element}; make_struct / break_struct {struct}; foreach {element}; input_action {name, consume?}; "
     "spawn_actor / add_component {class}; create_delegate {function}; comment {text, width?, height?}; raw {kind, props?, pins?}.\n"
     "links: [['a.then', 'b.execute'], ['c.ReturnValue', 'd.InString']]. Exec pins are usually execute/then; see discover_pins. "
     "Links can't reach nodes already in the graph. spec_file: JSON file with nodes and links, for big graphs.",
     S(nodes={"type": "array", "items": OBJ}, links={"type": "array", "items": {"type": "array", "items": STR}}, spec_file=STR, asset_path=STR,
       parent_class=STR, prefix=STR, out_file=STR, paste=BOOL, clear=BOOL, compile=BOOL, save=BOOL), []),
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
     "reference, nodes with compile errors or orphaned pins, and (by default) the node list. Only live nodes unless include_deleted. Filter by graph or node_prefix.",
     S(asset_path=STR, graph=STR, node_prefix=STR, include_nodes=BOOL, include_pins=BOOL, include_deleted=BOOL, limit=INT), ["asset_path"]),
    ("blueprint_filter_check", t_blueprint_filter_check,
     "Check mod Blueprints against the editor's own Blueprint filter (the rules its upload Asset Audit enforces): lists every blocked "
     "function call, event, variable and event dispatcher with its node. Pass asset_paths or mod_root.",
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
     "Newest lines of the Mod Editor log, optional regex filter and time limit (works with the editor closed). Upload/mod.io errors are under LogModio.",
     S(lines=INT, contains={"type": "string", "description": "Case-insensitive regex."}, since_minutes=NUM, max_scan_mb=NUM, file=STR), []),
]
TOOL_MAP = {t[0]: t for t in TOOLS}

INSTRUCTIONS = (
    "Tools for the official Abiotic Factor Mod Editor (custom Unreal 5.4.4 cooked editor, Python 3.11). Start with editor_status. "
    "Mods are plugins with an AbioticModConfig and per-row assets (e.g. /<Mod>/DataTables/ItemTable_Gear/<Row>) whose data is the 'row' property. "
    "Never edit or save /Game (base content); never T3D-export /Game Blueprints (crash); never delete many assets in one loop (crash). "
    "Python cannot create Blueprint nodes: use build_graph (it checks every pin against the editor), which pastes and verifies; "
    "paste_graph takes T3D you already have. compile_blueprint reports compiler errors. "
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
