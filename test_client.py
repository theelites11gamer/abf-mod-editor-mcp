"""End-to-end check: start server.py over stdio, speak MCP to it, call tools.

    python -I test_client.py                       # read-only checks against the open editor
    python -I test_client.py --bridge              # same, forcing the file-bridge transport
    python -I test_client.py --row <asset> <mod_dir>   # also edit+restore a row, export T3D, check_upload
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

    c.p.stdin.close()
    c.p.wait(timeout=10)
    print("\nFAILURES:", failures or "none")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
