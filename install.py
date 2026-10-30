"""Install or remove the abf-mod-editor-mcp server.

    python install.py                 install the editor hook and register with Claude Code
    python install.py --no-register   install the editor hook only (prints config for any MCP client)
    python install.py --uninstall     remove the hook, the Claude Code entry and the bridge folder

Run it with the Python you want the server to use (3.9 or newer); that interpreter's path is
written into the MCP registration. Restart the Mod Editor afterwards; new Claude Code sessions
see the tools.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402

SERVER_NAME = "abiotic-mod-editor"
SERVER_PY = HERE / "server.py"
BEGIN, END = f"# >>> {paths.HOOK_MARKER} >>>", f"# <<< {paths.HOOK_MARKER} <<<"


def say(msg):
    print(msg, flush=True)


# ---- editor hook ------------------------------------------------------------

def install_hook():
    hook_dir = paths.hook_dir()
    hook_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "editor" / "abf_mcp_startup.py", hook_dir / "abf_mcp_startup.py")
    block = (HERE / "editor" / "init_unreal.py").read_text(encoding="utf-8")
    init = hook_dir / "init_unreal.py"
    if not init.is_file():
        init.write_text(block, encoding="utf-8")
        say(f"Hook installed: {init}")
        return
    text = init.read_text(encoding="utf-8", errors="replace")
    if BEGIN in text and END in text:
        start, stop = text.index(BEGIN), text.index(END) + len(END)
        new = text[:start] + block.strip("\n") + text[stop:]
        say(f"Hook updated in place: {init}")
    else:
        bak = init.with_suffix(".py.before-abf-mod-editor-mcp.bak")
        if not bak.exists():
            shutil.copy2(init, bak)
        new = text.rstrip("\n") + "\n\n" + block
        say(f"Hook appended to your existing {init} (backup: {bak.name})")
    init.write_text(new, encoding="utf-8")


def uninstall_hook():
    hook_dir = paths.hook_dir()
    startup = hook_dir / "abf_mcp_startup.py"
    if startup.is_file():
        startup.unlink()
        say(f"Removed {startup}")
    init = hook_dir / "init_unreal.py"
    if init.is_file():
        text = init.read_text(encoding="utf-8", errors="replace")
        if BEGIN in text and END in text:
            start, stop = text.index(BEGIN), text.index(END) + len(END)
            rest = (text[:start] + text[stop:]).strip()
            if rest:
                init.write_text(rest + "\n", encoding="utf-8")
                say(f"Removed the hook block from {init} (your other content was kept)")
            else:
                init.unlink()
                say(f"Removed {init}")
    bridge = paths.bridge_dir()
    if bridge.is_dir():
        shutil.rmtree(bridge, ignore_errors=True)
        say(f"Removed {bridge}")


# ---- MCP client registration -------------------------------------------------

def server_command():
    return [sys.executable, "-I", str(SERVER_PY)]


def claude_cli():
    return shutil.which("claude")


def register_claude():
    cmd = server_command()
    cli = claude_cli()
    if cli:
        subprocess.run([cli, "mcp", "remove", "--scope", "user", SERVER_NAME], capture_output=True)
        r = subprocess.run([cli, "mcp", "add", "--scope", "user", SERVER_NAME, "--"] + cmd, capture_output=True, text=True)
        if r.returncode == 0:
            say(f"Registered with Claude Code (user scope) via the claude CLI: {SERVER_NAME}")
            return
        say("claude CLI failed (" + (r.stderr or r.stdout).strip()[:200] + "); writing the config file instead.")
    cfg = Path.home() / ".claude.json"
    data = {}
    if cfg.is_file():
        bak = cfg.with_name(f".claude.json.before-{SERVER_NAME}-{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(cfg, bak)
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except ValueError:
            say(f"{cfg} is not valid JSON; not touching it. Add the server by hand (see below). Backup: {bak.name}")
            return
    data.setdefault("mcpServers", {})[SERVER_NAME] = {"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": {}}
    cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    say(f"Registered with Claude Code (user scope) in {cfg}")


def unregister_claude():
    cli = claude_cli()
    if cli:
        subprocess.run([cli, "mcp", "remove", "--scope", "user", SERVER_NAME], capture_output=True)
    cfg = Path.home() / ".claude.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except ValueError:
            return
        if SERVER_NAME in data.get("mcpServers", {}):
            shutil.copy2(cfg, cfg.with_name(f".claude.json.before-{SERVER_NAME}-uninstall.bak"))
            del data["mcpServers"][SERVER_NAME]
            cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            say(f"Removed {SERVER_NAME} from {cfg}")


def print_other_clients():
    cmd = server_command()
    quoted = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    say("\nFor other MCP clients, the server command is:\n    " + quoted)
    say("  Codex:      codex mcp add " + SERVER_NAME + " -- " + quoted)
    say("  JSON form:  " + json.dumps({SERVER_NAME: {"command": cmd[0], "args": cmd[1:]}}))


# ---- main -------------------------------------------------------------------

def main(argv):
    if sys.platform != "win32":
        say("The Mod Editor only runs on Windows, and so does this server.")
        return 1
    if sys.version_info < (3, 9):
        say("Python 3.9 or newer is needed.")
        return 1
    if "--uninstall" in argv:
        uninstall_hook()
        unregister_claude()
        say("Done. Restart the Mod Editor if it is open.")
        return 0
    root, how = paths.find_editor_root(explain=True)
    if root:
        say(f"Mod Editor found via {how}: {root}")
    else:
        say("Mod Editor NOT found (looked at: " + "; ".join(f"{k}={v}" for k, v in how) + ").")
        say("Install it from the Epic Games Launcher, or set ABF_EDITOR_ROOT / config.json editor_root later. Continuing.")
    install_hook()
    if "--no-register" not in argv:
        register_claude()
    print_other_clients()
    say("\nNext: restart the Mod Editor (the hook runs at startup), then open a NEW Claude Code session "
        "and ask it to call editor_status.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
