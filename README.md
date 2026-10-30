# abf-mod-editor-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI coding assistant (Claude Code, Codex, Cursor, anything that speaks MCP) drive the official **Abiotic Factor Mod Editor** — the free Unreal-based editor Deep Field Games ships through the Epic Games Launcher.

With it, the assistant can open a mod, read and edit item/recipe/buff rows, create new rows copied from base-game ones, paste Blueprint graphs, compile, look at the editor through per-window screenshots, click and type in it, load a map, run Play In Editor, read the player's state, and check a mod against the same Blueprint filter the upload audit enforces — all from a chat.

Pure Python, no packages, Windows only (the editor is Windows only). Not affiliated with Deep Field Games, Playstack or mod.io.

New to this? There's a step-by-step setup and usage guide on mod.io: [Use an AI assistant with the Mod Editor (MCP setup guide)](https://mod.io/g/abiotic-factor/r/use-an-ai-assistant-with-the-mod-editor-mcp-setup-guide).

## Requirements

- Windows 10/11
- The Abiotic Factor Mod Editor installed from the Epic Games Launcher
- Python 3.9 or newer on the PC (the editor's own Python is used inside the editor; this one runs the server)
- An MCP client. Claude Code is registered automatically; others get a command to paste.

## Install

```bash
git clone https://github.com/theelites11gamer/abf-mod-editor-mcp.git
cd abf-mod-editor-mcp
python install.py
```

`install.py`:

1. Finds the Mod Editor through the Epic Games Launcher's install manifests (every install has a different random folder suffix, so nothing is hardcoded). If it can't, set `ABF_EDITOR_ROOT` or put `{"editor_root": "..."}` in a `config.json` next to `server.py`.
2. Puts a small startup hook in `Documents\UnrealEngine\Python\` (`init_unreal.py` + `abf_mcp_startup.py`). Every Unreal editor runs that file at startup; the hook checks the engine build tag and does nothing outside the Mod Editor. If you already have an `init_unreal.py`, the hook is appended as a marked block and your file is backed up.
3. Registers the server with Claude Code at user scope (through the `claude` CLI when it's on PATH, otherwise by adding it to `~/.claude.json` with a backup). `--no-register` skips this and just prints the command for other clients.

Then **restart the Mod Editor** and start a **new** Claude Code session. Ask it to call `editor_status`.

Remove everything with `python install.py --uninstall`.

## What the hook does

The editor's Python plugin has a "remote execution" mode (UDP discovery on 239.0.0.1:6766, loopback only, then a TCP command channel the editor opens back to the client). It's compiled into the Mod Editor but off by default, and the setting doesn't survive in the editor's config file. The hook switches it on at runtime for that session and also starts a tiny **file bridge** (`%LOCALAPPDATA%\abf-mod-editor-mcp\bridge`) as a fallback for when a modal dialog blocks remote execution or another client holds its connection.

The server uses remote execution first and the bridge second, opens a fresh connection per command (so it can share the editor with other tools), and reports a command that was sent but got no reply instead of retrying it.

## Tools (33)

| Group | Tools |
| --- | --- |
| Status and launch | `editor_status` (call first), `launch_editor`, `open_mod` (drives the picker's Browse… dialog, since the command line can't open a mod) |
| Python | `run_python`, `evaluate_python`, `run_python_file` (`args` → `ARGS` inside the script) |
| Mod data | `list_mods`, `list_mod_templates`, `find_assets`, `get_row`, `set_row` (plain values, Python expressions, or whole export text), `create_row_asset` (any of the 72 `*ModRowAsset` classes, optionally copied from a base-game row or another mod row), `delete_asset`, `save_assets`, `compile_blueprint`, `validate_assets` |
| Base-game data | `get_game_row` — list or read any row the game exposes through its `<Kind>HandleFunctionLibrary` (gear_item, weapon_item, recipe, buff_debuff, npc, pet, trait, …) |
| Blueprints | `paste_graph` (clipboard T3D → graph in one step), `inspect_blueprint` (parent, variables, graphs, every reference, errors, nodes and pins), `export_t3d`, `t3d_errors`, `blueprint_filter_check` |
| Window | `editor_windows`, `editor_screenshot` (one editor window, returned as an image, works in the background), `editor_click`, `editor_keys`, `close_editor_window` |
| Map and play | `load_map`, `play_in_editor` start/stop, `pie_info` (players, pawns, locations, chosen pawn properties), `console_command` (editor or game world) |
| Release | `check_upload` (offline pre-upload check), `read_editor_log` |

### Safety rails built in

- Refuses to edit, save, delete or T3D-export anything under `/Game` (base content). Exporting a base-game Blueprint has crashed the editor.
- Screenshots capture one editor window, never the desktop.
- Keys and text are only sent while the editor is the foreground window.
- `delete_asset` deletes one asset per call: a delete loop crashed the editor.
- Nothing uploads to mod.io. You do that in the editor.

### `check_upload` and `blueprint_filter_check`

The editor's **Upload Mod** zips the whole project folder and mod.io rejects some of what ends up inside (for instance any `.md` file, reported only as "Unknown ApiError"). The cloud cook then recompiles Blueprints without the editor-only assets, so a reference to something that exists only in the editor fails there. `check_upload` scans the folder for both, using the file lists in `data/` (names only, from game/editor version 1.5; see `data/make_editor_list.py`).

The upload's Asset Audit checks Blueprint calls and variables against a filter list Deep Field publishes. `blueprint_filter_check` downloads that list (cached a day) and applies the same rules to a mod's Blueprints, naming each blocked call and the node it's on — before you find out one upload at a time.

Neither replaces the editor's own checks; they catch the common failures early.

## Working notes

- Mod row assets are `PrimaryDataAsset`s; `row_name` defaults to the asset name and the game adds the mod prefix at runtime.
- Python can't create Blueprint nodes in this editor. Generate clipboard text (T3D) and use `paste_graph`, in passes: custom events must exist and be compiled before anything calls them, or the calls are dropped silently. Use a fresh node-name prefix per paste; deleted nodes linger in exports.
- A new mod project can't be created from Python (the template system has no entry point). Do it in the picker: ITEMS tile → template → name → Create. `editor_screenshot` + `editor_click`/`editor_keys` can drive it.
- Loading a map opens the Message Log over the viewport; `close_editor_window("Message Log")` before Play In Editor.
- The editor runs commands on its game thread: keep scripts short, and never open a modal dialog from one.

## Testing

`python test_client.py` speaks MCP to the server over stdio and exercises the read-only tools against the open editor. `--bridge` forces the file-bridge transport. `--row <mod row asset> <mod folder>` also edits and restores a row, creates and deletes a row asset, and runs the two checkers. Use a throwaway copy of a mod for that.

## Limits

- Built against Mod Editor 1.5.0 (engine `5.4.4-1050002+++DF+ABF`, Python 3.11). A future editor update may change the Python API; the file lists in `data/` are versioned and will need regenerating after a game update.
- Windows only.
- A mod made with these tools still has to pass the editor's Asset Audit and mod.io's cloud cook; the checkers here reduce surprises, they don't guarantee anything.

## License

MIT. The Blueprint filter list belongs to Deep Field Games and is downloaded from their CDN at run time, not shipped here.
