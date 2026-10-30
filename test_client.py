"""End-to-end check: start server.py over stdio, speak MCP to it, call tools.

    python -I test_client.py                       # read-only checks against the open editor
    python -I test_client.py --bridge              # same, forcing the file-bridge transport
    python -I test_client.py --row <asset> <mod_dir>   # also edit+restore a row, export T3D, check_upload
    python -I test_client.py --graph <mod root>        # also build, paste, compile and check a throwaway Blueprint

--graph takes the keyboard and mouse for a minute. Use a throwaway copy of a mod; it deletes its Blueprint afterwards.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


class Client:
    def __init__(self, env=None):
        self.p = subprocess.Popen([sys.executable, "-I", str(HERE / "server.py")], env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr)
        self.next_id = 0

    def request(self, method, params=None):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.p.stdin.write((json.dumps(msg) + "\n").encode())
        self.p.stdin.flush()
        reply = json.loads(self.p.stdout.readline())
        assert reply.get("id") == self.next_id, reply
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply["result"]

    def call(self, _tool, **args):
        name = _tool
        res = self.request("tools/call", {"name": name, "arguments": args})
        kinds = [c["type"] for c in res["content"]]
        text = "\n".join(c.get("text", f"<{c['type']} {len(c.get('data', ''))} b64 chars>") for c in res["content"])
        print(f"\n=== {name} {json.dumps(args)[:140]}  isError={res['isError']} content={kinds}\n{text[:1500]}")
        return res, text


KSL = "/Script/Engine.KismetSystemLibrary"
ACTOR = "/Script/Engine.Actor"
PAWN = "/Script/Engine.Pawn"
GS = "/Script/Engine.GameplayStatics"
VECTOR = "/Script/CoreUObject.Vector"


def graph_checks(check, mod_root, failures):
    root = mod_root.rstrip("/")
    bp = root + "/McpGraphTest/BP_McpGraphTest"
    widget = root + "/McpGraphTest/W_McpGraphTest"

    def create(name, factory, cls):
        check("run_python", contains="created", code=(
            "import unreal\n"
            f"f = unreal.{factory}()\n"
            f"f.set_editor_property('parent_class', unreal.{'UserWidget' if 'Widget' in factory else 'Actor'}.static_class())\n"
            f"a = unreal.AssetToolsHelpers.get_asset_tools().create_asset('{name}', '{root}/McpGraphTest', unreal.{cls}, f)\n"
            "unreal.EditorAssetLibrary.save_loaded_asset(a, False)\n"
            "print('created', a.get_path_name())\n"))

    def need(label, condition, detail=""):
        print(("  ok  " if condition else "  BAD ") + label, detail if not condition else "")
        if not condition:
            failures.append(label)

    create("BP_McpGraphTest", "BlueprintFactory", "Blueprint")
    create("W_McpGraphTest", "WidgetBlueprintFactory", "WidgetBlueprint")
    try:
        check("open_asset", contains="BP_McpGraphTest", asset_path=bp)
        check("discover_pins", contains='"InString"', nodes=[{"owner": KSL, "function": "PrintString"}])
        check("discover_pins", expect_error=True, contains="refused", nodes=[{"owner": KSL, "function": "NoSuchFunction"}])

        begin = {"id": "begin", "type": "event", "owner": ACTOR, "function": "ReceiveBeginPlay"}
        say = {"id": "say", "owner": KSL, "function": "PrintString", "args": {"InString": "Hello from build_graph", "Duration": 2.5}}
        check("build_graph", expect_error=True, contains="no pin", nodes=[begin, say], links=[["begin.then", "say.exec"]])

        # One of each common node type. clear also removes the greyed-out default events.
        nodes = [begin, say,
                 {"id": "yes", "type": "branch", "args": {"Condition": True}},
                 {"id": "seq", "type": "sequence", "outputs": 4},
                 {"id": "self", "type": "self"},
                 {"id": "cast", "type": "cast", "target": PAWN},
                 {"id": "boss", "owner": ACTOR, "function": "GetOwner"},
                 {"id": "loc", "owner": ACTOR, "function": "K2_GetActorLocation"},
                 {"id": "brk", "type": "break_struct", "struct": VECTOR},
                 {"id": "mk", "type": "make_struct", "struct": VECTOR, "args": {"X": 1.5}},
                 {"id": "arr", "type": "make_array", "count": 2, "element": {"cat": "object", "obj": ACTOR}},
                 {"id": "len", "type": "array_call", "function": "Array_Length", "element": {"cat": "object", "obj": ACTOR}},
                 {"id": "each", "type": "foreach", "element": {"cat": "object", "obj": ACTOR}},
                 {"id": "name", "owner": KSL, "function": "GetDisplayName"},
                 {"id": "say2", "owner": KSL, "function": "PrintString"},
                 {"id": "txt", "owner": KSL, "function": "PrintText", "args": {"InText": "A text default"}},
                 {"id": "all", "owner": GS, "function": "GetAllActorsOfClass", "args": {"ActorClass": PAWN}},
                 {"id": "spawn", "type": "spawn_actor", "class": PAWN},
                 {"id": "xf", "owner": ACTOR, "function": "GetTransform"},
                 {"id": "add", "type": "add_component", "class": "/Script/Engine.BoxComponent"},
                 {"id": "tags", "type": "var_get", "owner": ACTOR, "name": "Tags"},
                 {"id": "key", "type": "input_action", "name": "InteractA"},
                 {"id": "server", "type": "custom_event", "name": "McpServerThing", "rpc": "server",
                  "params": [{"name": "Amount", "cat": "int"}, {"name": "Who", "cat": "object", "obj": ACTOR}]},
                 {"id": "note", "type": "comment", "text": "build_graph test", "width": 300, "height": 80}]
        links = [["begin.then", "say.execute"], ["say.then", "yes.execute"], ["yes.then", "seq.execute"],
                 ["seq.then_0", "cast.execute"], ["self.self", "boss.self"], ["boss.ReturnValue", "cast.Object"],
                 ["self.self", "loc.self"], ["loc.ReturnValue", "brk.Vector"],
                 ["self.self", "arr.[0]"], ["arr.Array", "each.Array"], ["seq.then_1", "each.Exec"],
                 ["arr.Array", "len.TargetArray"],
                 ["each.Array Element", "name.Object"], ["name.ReturnValue", "say2.InString"], ["each.LoopBody", "say2.execute"],
                 ["seq.then_2", "txt.execute"], ["txt.then", "all.execute"],
                 ["seq.then_3", "spawn.execute"], ["xf.ReturnValue", "spawn.SpawnTransform"], ["spawn.then", "add.execute"],
                 ["self.self", "xf.self"]]
        _, text = check("build_graph", contains='"nodes_found"', asset_path=bp, nodes=nodes, links=links, paste=True, clear=True, compile=True)
        report = json.loads(text)
        paste = report.get("paste", {})
        # The list already ends with a comment, so no end comment is added.
        need("every node arrived", not paste.get("missing_nodes") and paste.get("nodes_found") == len(nodes), paste.get("missing_nodes"))
        need("every link arrived", not paste.get("missing_links") and paste.get("links_sent") == len(links), paste.get("missing_links"))
        need("no node changed class", not paste.get("changed_class"), paste.get("changed_class"))
        need("all in the Event Graph", paste.get("landed_in") == {"EventGraph": len(nodes)}, paste.get("landed_in"))
        need("compiled clean", paste.get("compile", {}).get("ok") is True, paste.get("compile"))
        need("saved", paste.get("compile", {}).get("saved") is True, paste.get("compile"))
        need("nothing blocked", report.get("blocked_by_upload_filter") == [], report.get("blocked_by_upload_filter"))

        # The defaults reached the real pins, and the default events are gone.
        _, text = check("export_t3d", contains='"deleted_or_generated_nodes_ignored"', asset_path=bp)
        export = Path(json.loads(text)["file"]).read_text(encoding="utf-8", errors="replace")
        need("export reports no problems", json.loads(text)["problems"] == [], json.loads(text)["problems"])
        need("string default kept", 'DefaultValue="Hello from build_graph"' in export)
        need("number default kept", 'DefaultValue="2.5' in export)
        need("bool default kept", 'PinName="Condition"' in export and 'DefaultValue="true"' in export)
        need("text default kept", "A text default" in export)
        need("class default kept", 'DefaultObject="/Script/Engine.Pawn"' in export)
        _, text = check("inspect_blueprint", contains="ReceiveBeginPlay", asset_path=bp, include_nodes=False)
        info = json.loads(text)
        need("inspect sees no problems", info["problems"] == [], info["problems"])
        need("default events cleared", not any("ReceiveTick" in r for r in info["references"].get("event", [])), info["references"].get("event"))

        # Second pass: a call to the custom event that now exists, its pins resolved through the Blueprint itself.
        _, text = check("build_graph", contains='"nodes_found"', asset_path=bp, paste=True, compile=True,
                        nodes=[{"id": "e", "type": "custom_event", "name": "McpSecondPass"},
                               {"id": "call", "function": "McpServerThing", "args": {"Amount": 5}},
                               {"id": "v", "type": "var_set", "name": "CustomTimeDilation", "args": {"CustomTimeDilation": 1.0}}],
                        links=[["e.then", "call.execute"], ["call.then", "v.execute"]])
        report = json.loads(text)
        need("self call resolved and linked", report.get("ok") is True and not report.get("paste", {}).get("missing_links"), report)
        need("self call compiled", report.get("paste", {}).get("compile", {}).get("ok") is True, report.get("paste"))

        # A pasted copy of an event already in the graph becomes a custom event. That must be reported.
        _, text = check("build_graph", expect_error=True, contains="changed_class", asset_path=bp, paste=True,
                        nodes=[{"id": "again", "type": "event", "owner": ACTOR, "function": "ReceiveBeginPlay"}], links=[])
        report = json.loads(text)
        need("event conflict reported", report.get("paste", {}).get("changed_class", [{}])[0].get("now") == "K2Node_CustomEvent", report.get("paste"))

        # Clipboard text through paste_graph: window only, so the asset comes from the title. Pasting it twice is noted.
        graph_text = ('Begin Object Class=/Script/BlueprintGraph.K2Node_CustomEvent Name="McpRaw_0"\n'
                      'CustomFunctionName="McpRawEvent"\nNodePosX=0\nNodePosY=2000\nEnd Object\n')
        _, text = check("paste_graph", contains='"verified": true', window="BP_McpGraphTest", text=graph_text)
        need("asset found from the window title", json.loads(text).get("ok") is True, text[:300])
        _, text = check("paste_graph", expect_error=None, contains="already in the Blueprint", asset_path=bp, text=graph_text)
        check("paste_graph", expect_error=True, contains="lint_errors", asset_path=bp, text="this is not a graph")

        # A self-context call to a function that isn't there opens a blocking dialog. It has to be reported, and closing it
        # has to leave the editor working. (build_graph avoids this by naming the class.)
        no_such = ('Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="McpDlg_0"\n'
                   'FunctionReference=(MemberName="McpNoSuchFunction",bSelfContext=True)\nNodePosX=0\nNodePosY=3000\nEnd Object\n')
        _, text = check("paste_graph", expect_error=None, asset_path=bp, text=no_such)
        report = json.loads(text)
        print("  paste of a call to a missing function:", {k: report.get(k) for k in ("ok", "dialogs", "nodes_found", "hint")})
        for title in report.get("dialogs", []):
            check("close_editor_window", expect_error=None, window=title)
        check("evaluate_python", contains="+++DF+ABF", expression="unreal.SystemLibrary.get_engine_version()")
        _, text = check("build_graph", expect_error=True, contains="refused", asset_path=bp,
                        nodes=[{"id": "gone", "function": "McpNoSuchFunction"}], links=[])
        need("own missing function refused without a dialog", "refused" in text, text[:300])

        # A real compile error comes back, and the Blueprint isn't saved.
        _, text = check("build_graph", contains='"nodes_found"', asset_path=bp, paste=True,
                        nodes=[{"id": "e", "type": "custom_event", "name": "McpBroken"},
                               {"id": "bad", "type": "cast", "target": PAWN}],
                        links=[["e.then", "bad.execute"]])
        _, text = check("compile_blueprint", expect_error=True, contains='"errors"', asset_path=bp)
        result = json.loads(text)
        need("compile error reported", result.get("ok") is False and bool(result.get("errors")), result)
        need("broken Blueprint not saved", result.get("saved") is False, result)

        check("validate_assets", contains='"checked"', expect_error=None, paths=[bp])
        check("blueprint_filter_check", contains='"verdict"', expect_error=None, asset_paths=[bp])
        check("read_editor_log", contains="ABF_MCP", lines=3, since_minutes=10)

        # A widget: it opens on the Designer, and its default events are in the way until cleared.
        check("open_asset", contains="WidgetBlueprint", asset_path=widget)
        _, text = check("build_graph", contains='"nodes_found"', asset_path=widget, paste=True, clear=True, compile=True,
                        nodes=[{"id": "go", "type": "event", "owner": "/Script/UMG.UserWidget", "function": "Construct"},
                               {"id": "self", "type": "self"},
                               {"id": "vis", "owner": "/Script/UMG.Widget", "function": "SetVisibility", "args": {"InVisibility": "Collapsed"}}],
                        links=[["go.then", "vis.execute"], ["self.self", "vis.self"]])
        report = json.loads(text)
        need("widget graph built", report.get("ok") is True, report.get("paste"))
        check("open_asset", contains="BP_McpGraphTest", asset_path=bp, show_graph=True)
        check("editor_screenshot", window="BP_McpGraphTest", max_width=1600, save_as="graph_test.png")
    finally:
        for path in (bp, widget):
            check("close_asset", expect_error=None, asset_path=path)
            check("delete_asset", expect_error=None, asset_path=path)


def main():
    env = dict(os.environ)
    if "--bridge" in sys.argv:
        env["ABF_MCP_TRANSPORT"] = "bridge"
    c = Client(env)
    init = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test_client", "version": "1"}})
    print("initialize ->", init["serverInfo"])
    c.p.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    c.p.stdin.flush()
    print("tools:", len(c.request("tools/list")["tools"]))

    failures = []

    def check(_tool, expect_error=False, contains=None, **args):
        res, text = c.call(_tool, **args)
        if (expect_error is not None and res["isError"] != expect_error) or (contains and contains not in text):
            failures.append(_tool)
        return res, text

    check("editor_status", contains='"engine_version"')
    check("evaluate_python", contains="+++DF+ABF", expression="unreal.SystemLibrary.get_engine_version()")
    check("run_python", contains="line 2\n3", code="for i in range(3):\n    print('line', i)\nprint(3)")
    check("run_python", expect_error=True, contains="expected test error", code="raise ValueError('expected test error')")
    check("run_python", contains="truncated", code="print('x' * 200000)")
    check("list_mods")
    check("find_assets", contains="ItemTable_Gear", path="/Game/Blueprints/Items", class_name="DataTable", limit=20)
    check("get_row", contains="row_count", asset_path="/Game/Blueprints/Items/ItemTable_Gear")
    check("set_row", expect_error=True, contains="refusing", asset_path="/Game/Blueprints/Items/ItemTable_Gear", values={"x": 1})
    check("export_t3d", expect_error=True, asset_path="/Game/Blueprints/Items/Abiotic_Item_ParentBP")
    check("list_mod_templates", contains="TP_MeleeWeapon")
    check("read_editor_log", lines=3, contains="ABF_MCP")
    check("editor_windows", contains="hwnd")
    res, _ = check("editor_screenshot", max_width=1000)
    if not any(x["type"] == "image" for x in res["content"]):
        failures.append("screenshot-image")
    check("get_game_row", contains="armor_helmet_hardhat", kind="gear_item", name_contains="hardhat")
    check("get_game_row", contains="Weight=7", kind="gear_item", name="armor_helmet_hardhat")
    check("get_game_row", expect_error=True, contains="unknown row kind", kind="bogus_kind")
    check("delete_asset", expect_error=True, contains="refusing", asset_path="/Game/Blueprints/Items/ItemTable_Gear")
    check("console_command", contains='"ran"', command="stat none")

    if "--row" in sys.argv:
        asset, mod_dir = sys.argv[sys.argv.index("--row") + 1:][:2]
        mod_root = "/" + asset.strip("/").split("/")[0]
        _, before = check("get_row", asset_path=asset)
        weight = json.loads(before).get("export_text", "")
        check("set_row", contains='"saved": true', asset_path=asset, values={"weight": 0.75})
        check("get_row", contains="Weight=0.75", asset_path=asset)
        check("set_row", contains='"saved": true', asset_path=asset, import_text=weight)
        tmp = mod_root + "/Test/McpTest_CopyHelmet"
        check("create_row_asset", contains='"saved": true', class_name="GearItemModRowAsset", asset_path=tmp,
              copy_from_kind="gear_item", copy_from_name="armor_helmet_hardhat")
        check("create_row_asset", expect_error=True, contains="holds a", class_name="RecipeModRowAsset", asset_path=mod_root + "/Test/McpTest_Bad",
              copy_from_kind="gear_item", copy_from_name="armor_helmet_hardhat")
        check("delete_asset", contains='"deleted": true', asset_path=tmp)
        check("blueprint_filter_check", expect_error=None, contains='"verdict"', mod_root=mod_root)
        check("check_upload", expect_error=None, mod_dir=mod_dir)

    if "--graph" in sys.argv:
        graph_checks(check, sys.argv[sys.argv.index("--graph") + 1], failures)

    c.p.stdin.close()
    c.p.wait(timeout=10)
    print("\nFAILURES:", failures or "none")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
