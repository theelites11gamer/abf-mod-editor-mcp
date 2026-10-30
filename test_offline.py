"""Checks that need no editor: the filter rules, T3D parsing, paste checks and the graph builder.

    python -I test_offline.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpscan        # noqa: E402
import graphbuild    # noqa: E402

OBJ = bpscan.OBJECT
RULES = {
    "/Script/CoreUObject": "Block", "/Script/Engine": "Block", OBJ: "Block",
    "/Script/Engine.ActorComponent": "Allow", "/Script/Engine.Actor": "Allow",
    "/Script/AbioticFactor": "Allow", "/Script/UMG": "Allow",
    "/Script/Engine.Subsystem": "Block", "/Script/Engine.GameStateBase": "Block", "/Script/Engine.GameModeBase": "Block",
    "/Script/AbioticFactor.KeybindSubsystem": "Allow",
    "/Script/Engine.PlayerController SetMouseLocation": "Block",
    "/Script/AbioticFactor.AbioticGameMode": "Block",
    "/Game/Meta/Mode.Mode_C SpawnItem": "Allow",
    "/Game/Libs/GoodLib.GoodLib_C": "Allow",
}
COMPONENT = ["/Script/Engine.SceneComponent", "/Script/Engine.ActorComponent", OBJ]
LIBRARY = ["/Script/Engine.BlueprintFunctionLibrary", OBJ]
GAME_MODE = ["/Script/AbioticFactor.AbioticGameMode", "/Script/Engine.GameModeBase", "/Script/Engine.Info", "/Script/Engine.Actor", OBJ]


def pin(pid, name, cat="exec", out=False, links=""):
    direction = 'Direction="EGPD_Output",' if out else ""
    linked = f"LinkedTo=({links})," if links else ""
    return (f'CustomProperties Pin (PinId={pid},PinName="{name}",{direction}PinType.PinCategory="{cat}",PinType.PinSubCategory="",'
            f"PinType.PinSubCategoryObject=None,PinType.ContainerType=None,{linked}bHidden=False,bOrphanedPin=False,)")


EXPORT = "\n".join([
    'Begin Object Class=/Script/Engine.Blueprint Name="BP_T" ExportPath="x"',
    '   Begin Object Class=/Script/Engine.EdGraph Name="EventGraph" ExportPath="x"',
    '      Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="A_0" ExportPath="x"',
    "      End Object",
    '      Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="A_1" ExportPath="x"',
    "      End Object",
    '      Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="OLD_0" ExportPath="x"',
    "      End Object",
    "   End Object",
    '   Begin Object Class=/Script/Engine.EdGraph Name="Fn" ExportPath="x"',
    '      Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="A_1" ExportPath="x"',
    "      End Object",
    "   End Object",
    '   Begin Object Class=/Script/Engine.EdGraph Name="ExecuteUbergraph_BP_T" ExportPath="x"',
    '      Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="GEN_0" ExportPath="x"',
    "      End Object",
    "   End Object",
    '   Begin Object Name="EventGraph" ExportPath="x"',
    '      Begin Object Name="A_0" ExportPath="x"',
    '         EventReference=(MemberParent="/Script/CoreUObject.Class\'/Script/Engine.Actor\'",MemberName="ReceiveBeginPlay")',
    "         " + pin("AA", "then", out=True, links="A_1 BB,"),
    "      End Object",
    '      Begin Object Name="A_1" ExportPath="x"',
    '         FunctionReference=(MemberParent="/Script/CoreUObject.Class\'/Script/Engine.AudioComponent\'",MemberName="FadeOut")',
    '         ErrorMsg="Something is wrong"',
    "         " + pin("BB", "execute", links="A_0 AA,"),
    "      End Object",
    '      Begin Object Name="OLD_0" ExportPath="x"',
    '         FunctionReference=(MemberParent="/Script/CoreUObject.Class\'/Script/Engine.GameStateBase\'",MemberName="Deleted")',
    "      End Object",
    "      Nodes(0)=\"/Script/BlueprintGraph.K2Node_Event'A_0'\"",
    "      Nodes(1)=\"/Script/BlueprintGraph.K2Node_CallFunction'A_1'\"",
    "   End Object",
    '   Begin Object Name="Fn" ExportPath="x"',
    '      Begin Object Name="A_1" ExportPath="x"',
    '         FunctionReference=(MemberParent="/Script/CoreUObject.Class\'/Script/Engine.Actor\'",MemberName="K2_DestroyActor")',
    "      End Object",
    "      Nodes(0)=\"/Script/BlueprintGraph.K2Node_CallFunction'A_1'\"",
    "   End Object",
    '   Begin Object Name="ExecuteUbergraph_BP_T" ExportPath="x"',
    '      Begin Object Name="GEN_0" ExportPath="x"',
    '         FunctionReference=(MemberParent="/Script/CoreUObject.Class\'/Script/CoreUObject.Object\'",MemberName="ExecuteUbergraph")',
    "      End Object",
    "      Nodes(0)=\"/Script/BlueprintGraph.K2Node_CallFunction'GEN_0'\"",
    "   End Object",
    "   UbergraphPages(0)=\"/Script/Engine.EdGraph'EventGraph'\"",
    "   FunctionGraphs(0)=\"/Script/Engine.EdGraph'Fn'\"",
    "End Object",
])


def clipboard(last_links=True):
    return "\n".join([
        'Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="P_0"',
        "NodePosX=0", "NodePosY=0",
        pin("A1", "then", out=True, links="P_1 B1,"),
        "End Object",
        'Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="P_1"',
        "NodePosX=400", "NodePosY=0",
        pin("B1", "execute", links="P_0 A1,"),
        "End Object", ""])


class Filter(unittest.TestCase):
    def verdict(self, *a):
        return bpscan.resolve(RULES, *a)[0]

    def test_inherited_allow(self):
        self.assertEqual(self.verdict("/Script/Engine.AudioComponent", "FadeOut", COMPONENT), "Allow")
        self.assertEqual(self.verdict("/Script/Engine.Pawn", "IsLocallyControlled", ["/Script/Engine.Actor", OBJ]), "Allow")

    def test_block_nearer_than_allow(self):
        self.assertEqual(self.verdict("/Script/Engine.GameStateBase", "X", ["/Script/Engine.Info", "/Script/Engine.Actor", OBJ]), "Block")

    def test_member_block_under_allowed_class(self):
        chain = ["/Script/Engine.Controller", "/Script/Engine.Actor", OBJ]
        self.assertEqual(self.verdict("/Script/Engine.PlayerController", "SetMouseLocation", chain), "Block")
        self.assertEqual(self.verdict("/Script/Engine.PlayerController", "GetHUD", chain), "Allow")

    def test_inherited_member_rule(self):
        chain = ["/Script/Engine.PlayerController", "/Script/Engine.Controller", "/Script/Engine.Actor", OBJ]
        self.assertEqual(self.verdict("/Script/AbioticFactor.MyController", "SetMouseLocation", chain), "Block")

    def test_module_allow_beats_object_block(self):
        self.assertEqual(self.verdict("/Script/AbioticFactor.GlobalItemHandleFunctionLibrary", "Conv", LIBRARY), "Allow")

    def test_ancestor_class_rule_beats_own_module(self):
        chain = ["/Script/Engine.Subsystem", OBJ]
        self.assertEqual(self.verdict("/Script/AbioticFactor.OtherSubsystem", "X", chain), "Block")
        self.assertEqual(self.verdict("/Script/AbioticFactor.KeybindSubsystem", "X", chain), "Allow")

    def test_engine_library_with_no_rule(self):
        self.assertEqual(self.verdict("/Script/Engine.ImportanceSamplingLibrary", "X", LIBRARY), "Block")

    def test_game_class(self):
        mode = "/Game/Meta/Mode.Mode_C"
        self.assertEqual(self.verdict(mode, "SpawnItem", GAME_MODE), "Allow")
        self.assertEqual(self.verdict(mode, "SomethingElse", GAME_MODE), "Block")
        self.assertEqual(self.verdict("/Game/Libs/GoodLib.GoodLib_C", "Fn", LIBRARY), "Allow")
        self.assertEqual(self.verdict("/Game/Libs/OtherLib.OtherLib_C", "Fn", LIBRARY), "Block")

    def test_no_parents_known(self):
        self.assertEqual(self.verdict("/Game/Unknown.Unknown_C", "Fn"), "Allow")
        self.assertEqual(self.verdict("/Script/Engine.Unknown", "Fn"), "Block")
        self.assertEqual(self.verdict("/Script/NotListed.Thing", "Fn"), "Unlisted")

    def test_check_nodes_uses_parents(self):
        nodes = bpscan.parse_t3d(EXPORT)["nodes"]
        self.assertEqual(bpscan.check_nodes(nodes, RULES)["blocked"][0]["member"], "FadeOut")
        self.assertEqual(bpscan.check_nodes(nodes, RULES, {"/Script/Engine.AudioComponent": COMPONENT})["blocked"], [])


class Parsing(unittest.TestCase):
    def test_live_nodes_only(self):
        p = bpscan.parse_t3d(EXPORT)
        self.assertEqual(p["dead_nodes"], 2)        # a deleted node, and one in a graph the compiler generated
        self.assertEqual(p["graphs"], {"EventGraph": 2, "Fn": 1})
        self.assertNotIn("GEN_0", [n["name"] for n in p["nodes"]])
        self.assertNotIn("OLD_0", [n["name"] for n in p["nodes"]])
        self.assertIn("OLD_0", [n["name"] for n in bpscan.parse_t3d(EXPORT, include_dead=True)["nodes"]])

    def test_same_name_in_two_graphs_stays_apart(self):
        funcs = sorted(n["function"]["name"] for n in bpscan.parse_t3d(EXPORT)["nodes"] if n["name"] == "A_1")
        self.assertEqual(funcs, ["FadeOut", "K2_DestroyActor"])

    def test_error_found_in_definition_block(self):
        self.assertEqual([n["error"] for n in bpscan.parse_t3d(EXPORT)["nodes"] if n.get("error")], ["Something is wrong"])

    def test_warning_is_not_an_error(self):
        text = EXPORT.replace('ErrorMsg="Something is wrong"', 'ErrorType=3\n         ErrorMsg="Would always fail"')
        node = next(n for n in bpscan.parse_t3d(text)["nodes"] if n.get("warning"))
        self.assertEqual(node["warning"], "Would always fail")
        self.assertNotIn("error", node)
        text = EXPORT.replace('ErrorMsg="Something is wrong"', 'ErrorType=1\n         ErrorMsg="Broken"')
        self.assertEqual([n["error"] for n in bpscan.parse_t3d(text)["nodes"] if n.get("error")], ["Broken"])

    def test_links(self):
        self.assertEqual(bpscan.links_of(bpscan.parse_t3d(clipboard())["nodes"]), {("P_0", "then", "P_1", "execute")})

    def test_long_filter_member_name(self):
        rules, _ = bpscan.load_filter(max_age_hours=10 ** 6)
        self.assertTrue(any(k.endswith("Find Existing Player Placed") or " Find Existing" in k for k in rules))


class PasteChecks(unittest.TestCase):
    def test_comment_added_once(self):
        text, added = graphbuild.ensure_end_comment(clipboard())
        self.assertTrue(added)
        self.assertEqual(bpscan.parse_t3d(text)["nodes"][-1]["class"], "EdGraphNode_Comment")
        again, added = graphbuild.ensure_end_comment(text)
        self.assertFalse(added)
        self.assertEqual(again, text)

    def test_comment_keeps_links_of_real_nodes(self):
        text, _ = graphbuild.ensure_end_comment(clipboard())
        self.assertEqual(bpscan.links_of(bpscan.parse_t3d(text)["nodes"]), bpscan.links_of(bpscan.parse_t3d(clipboard())["nodes"]))

    def test_lint_empty(self):
        self.assertTrue(graphbuild.lint("not a graph")["errors"])

    def test_lint_clean(self):
        r = graphbuild.lint(clipboard())
        self.assertEqual((r["errors"], r["warnings"]), ([], []))

    def test_lint_phantom_pin(self):
        text = clipboard().replace('End Object\n', pin("C1", "then", out=True) + "\nEnd Object\n", 1)
        self.assertIn("appears twice", graphbuild.lint(text)["errors"][0])

    def test_lint_link_outside_paste(self):
        text = clipboard().replace("P_1 B1,", "Elsewhere_0 ZZ,")
        self.assertTrue(any("isn't in this paste" in w for w in graphbuild.lint(text)["warnings"]))

    def test_lint_link_to_missing_pin(self):
        text = clipboard().replace("P_1 B1,", "P_1 ZZ,")
        self.assertTrue(any("no such pin" in e for e in graphbuild.lint(text)["errors"]))

    def test_verify_clean_and_dropped(self):
        sent = clipboard()
        node = lambda pins: "\n".join(pins)
        export = "\n".join([
            'Begin Object Class=/Script/Engine.Blueprint Name="BP"',
            '  Begin Object Class=/Script/Engine.EdGraph Name="EventGraph"',
            '    Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="P_0"',
            "    End Object",
            '    Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="P_1"',
            "    End Object",
            "  End Object",
            '  Begin Object Name="EventGraph"',
            '    Begin Object Name="P_0"', node(["      " + pin("C1", "then", out=True, links="P_1 D1,")]), "    End Object",
            '    Begin Object Name="P_1"', node(["      " + pin("D1", "execute", links="P_0 C1,")]), "    End Object",
            "    Nodes(0)=\"/Script/BlueprintGraph.K2Node_Event'P_0'\"",
            "    Nodes(1)=\"/Script/BlueprintGraph.K2Node_CallFunction'P_1'\"",
            "  End Object",
            "End Object"])
        r = graphbuild.verify(sent, export)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["landed_in"], {"EventGraph": 2})
        dropped = export.replace('LinkedTo=(P_1 D1,),', "").replace('LinkedTo=(P_0 C1,),', "")
        r = graphbuild.verify(sent, dropped)
        self.assertFalse(r["ok"])
        self.assertEqual(r["missing_links"], [{"from": "P_0.then", "to": "P_1.execute"}])
        gone = export.replace("Nodes(1)=\"/Script/BlueprintGraph.K2Node_CallFunction'P_1'\"", "")
        self.assertEqual(graphbuild.verify(sent, gone)["missing_nodes"], ["P_1"])
        custom = export.replace("K2Node_Event Name", "K2Node_CustomEvent Name")
        self.assertEqual(graphbuild.verify(sent, custom)["changed_class"][0]["now"], "K2Node_CustomEvent")


def full_pin(name, cat="exec", out=False, default=None):
    direction = 'Direction="EGPD_Output",' if out else ""
    extra = f'DefaultValue="{default}",' if default else ""
    return (f'PinId=00000000000000000000000000000000,PinName="{name}",{direction}PinType.PinCategory="{cat}",PinType.PinSubCategory="",'
            f"PinType.PinSubCategoryObject=None,PinType.PinSubCategoryMemberReference=(),PinType.PinValueType=(),PinType.ContainerType=None,"
            f"PinType.bIsReference=False,{extra}PersistentGuid=00000000000000000000000000000000,bHidden=False,bNotConnectable=False,"
            "bDefaultValueIsReadOnly=False,bDefaultValueIsIgnored=False,bAdvancedView=False,bOrphanedPin=False,")


PRINT = {"class": "K2Node_CallFunction", "props": ['FunctionReference=(MemberName="PrintString")'], "error": None,
         "pins": [full_pin("execute"), full_pin("then", out=True), full_pin("InString", "string", default="Hello"), full_pin("Duration", "real")]}
EVENT = {"class": "K2Node_Event", "props": ['EventReference=(MemberName="ReceiveBeginPlay")', "bOverrideFunction=True"], "error": None,
         "pins": [full_pin("OutputDelegate", "delegate", out=True), full_pin("then", out=True)]}
NODES = [{"id": "begin", "type": "event", "owner": "/Script/Engine.Actor", "function": "ReceiveBeginPlay"},
         {"id": "say", "owner": "/Script/Engine.KismetSystemLibrary", "function": "PrintString", "args": {"InString": 'He said "hi"', "Duration": 2.5}}]
LINKS = [["begin.then", "say.execute"]]
TEMPLATES = {"begin": EVENT, "say": PRINT}


class Builder(unittest.TestCase):
    def build(self, nodes=NODES, links=LINKS, templates=TEMPLATES):
        return graphbuild.assemble(nodes, links, templates, "T1")

    def test_builds_and_reads_back(self):
        r = self.build()
        self.assertEqual(r["problems"], [])
        parsed = bpscan.parse_t3d(r["text"])["nodes"]
        self.assertEqual([n["name"] for n in parsed], ["T1_0", "T1_1"])
        self.assertEqual(bpscan.links_of(parsed), {("T1_0", "then", "T1_1", "execute")})
        self.assertEqual(graphbuild.lint(r["text"]), {"errors": [], "warnings": [], "nodes": 2})
        self.assertEqual(r["names"], {"begin": "T1_0", "say": "T1_1"})

    def test_pin_ids_are_new(self):
        self.assertNotIn("PinId=00000000000000000000000000000000", self.build()["text"])

    def test_defaults(self):
        text = self.build()["text"]
        self.assertIn('DefaultValue="He said \\"hi\\""', text)
        self.assertIn('DefaultValue="2.5"', text)
        self.assertNotIn('DefaultValue="Hello"', text)

    def test_bad_pin_lists_the_real_ones(self):
        r = self.build(links=[["begin.then", "say.exec"]])
        self.assertIsNone(r["text"])
        self.assertIn("no pin 'exec'", r["problems"][0])
        self.assertIn("execute", r["problems"][0])

    def test_bad_default_pin(self):
        nodes = [NODES[0], dict(NODES[1], args={"Nope": 1})]
        self.assertIn("no pin 'Nope'", self.build(nodes=nodes)["problems"][0])

    def test_refused_node(self):
        r = self.build(templates={"begin": EVENT, "say": None})
        self.assertIn("refused", r["problems"][0])

    def test_two_inputs(self):
        tpl = dict(TEMPLATES, other=PRINT)
        nodes = NODES + [{"id": "other", "owner": "/Script/Engine.KismetSystemLibrary", "function": "PrintString"}]
        r = self.build(nodes=nodes, links=[["say.execute", "other.execute"]], templates=tpl)
        self.assertIn("both pins are inputs", r["problems"][0])

    def test_exec_to_data(self):
        r = self.build(links=[["begin.then", "say.InString"]])
        self.assertIn("exec pin", r["problems"][0])

    def test_type_mismatch_warns(self):
        tpl = dict(TEMPLATES, other=PRINT)
        nodes = NODES + [{"id": "other", "owner": "/Script/Engine.KismetSystemLibrary", "function": "PrintString"}]
        other = {"class": "K2Node_CallFunction", "props": [], "error": None, "pins": [full_pin("Out", "int", out=True)]}
        r = self.build(nodes=nodes, links=[["other.Out", "say.InString"]], templates=dict(tpl, other=other))
        self.assertEqual(r["problems"], [])
        self.assertIn("conversion", r["warnings"][0])

    def test_link_order_does_not_matter(self):
        self.assertEqual(bpscan.links_of(bpscan.parse_t3d(self.build(links=[["say.execute", "begin.then"]])["text"])["nodes"]),
                         {("T1_0", "then", "T1_1", "execute")})

    def test_positions(self):
        r = self.build()
        self.assertRegex(r["text"], r"Name=\"T1_1\"[\s\S]*NodePosX=360")
        placed = [NODES[0], dict(NODES[1], x=1000, y=-50)]
        self.assertIn("NodePosX=1000", self.build(nodes=placed)["text"])

    def test_sequence_outputs_are_starter_pins(self):
        pins = graphbuild.describe({"id": "s", "type": "sequence", "outputs": 4})[2]
        self.assertEqual([bpscan.parse_pin(p.split("(", 1)[1])["name"] for p in pins], ["then_0", "then_1", "then_2", "then_3"])
        self.assertEqual(len(graphbuild.describe({"id": "s", "type": "sequence"})[2]), 2)
        self.assertNotEqual(graphbuild.template_key({"type": "sequence", "outputs": 4}), graphbuild.template_key({"type": "sequence"}))

    def test_wildcard_gets_element_type(self):
        tpl = {"class": "K2Node_MakeArray", "props": ["NumInputs=1"], "error": None,
               "pins": [full_pin("[0]", "wildcard"), full_pin("Array", "wildcard", out=True)]}
        n = {"id": "a", "type": "make_array", "count": 1, "element": {"cat": "object", "obj": "/Script/Engine.Actor"}}
        pins = bpscan.parse_t3d(graphbuild.assemble([n], [], {"a": tpl}, "T1")["text"])["nodes"][0]["pins"]
        self.assertEqual({p["type"] for p in pins}, {"object"})
        self.assertEqual({p["object"] for p in pins}, {"/Script/Engine.Actor"})

    def test_element_leaves_other_pins_alone(self):
        tpl = {"class": "K2Node_MacroInstance", "props": [], "error": None,
               "pins": [full_pin("Exec"), full_pin("Array", "wildcard"), full_pin("Array Index", "int", out=True)]}
        n = {"id": "e", "type": "foreach", "element": {"cat": "object", "obj": "/Script/Engine.Actor"}}
        node = bpscan.parse_t3d(graphbuild.assemble([n], [], {"e": tpl}, "T1")["text"])["nodes"][0]
        self.assertEqual([(p["name"], p["type"], p.get("object")) for p in node["pins"]],
                         [("Exec", "exec", None), ("Array", "object", "/Script/Engine.Actor"), ("Array Index", "int", None)])

    def test_spec_props_survive_a_template_that_lost_them(self):
        tpl = {"class": "K2Node_MacroInstance", "props": ["MacroGraphReference=(x)"], "error": None, "pins": [full_pin("Exec")]}
        n = {"id": "e", "type": "foreach", "element": {"cat": "int"}}
        text = graphbuild.assemble([n], [], {"e": tpl}, "T1")["text"]
        self.assertIn('ResolvedWildcardType=(PinCategory="int")', text)
        self.assertEqual(text.count("MacroGraphReference="), 1)

    def test_variable_with_owner_and_no_target_warns(self):
        tpl = {"class": "K2Node_VariableSet", "props": [], "error": None, "pins": [full_pin("self", "object")]}
        r = graphbuild.assemble([{"id": "v", "type": "var_set", "owner": "/Script/Engine.Actor", "name": "X"}], [], {"v": tpl}, "T1")
        self.assertIn("self pin", r["warnings"][0])

    def test_unknown_type(self):
        with self.assertRaises(graphbuild.SpecError):
            graphbuild.describe({"id": "x", "type": "nope"})

    def test_missing_field(self):
        with self.assertRaises(graphbuild.SpecError):
            graphbuild.describe({"id": "x", "type": "cast"})

    def test_rpc_flags_match_known_server_event(self):
        props = graphbuild.describe({"id": "x", "type": "custom_event", "name": "Go", "rpc": "server"})[1]
        self.assertIn("FunctionFlags=2097344", props)

    def test_template_key_ignores_random_ids(self):
        n = {"id": "x", "type": "spawn_actor", "class": "/Script/Engine.Actor"}
        self.assertEqual(graphbuild.template_key(n), graphbuild.template_key(n))

    def test_templates_skip_orphaned_pins(self):
        export = "\n".join(['Begin Object Class=/Script/BlueprintGraph.K2Node_SpawnActorFromClass Name="S_0"',
                            pin("A1", "Class", "class"), pin("B1", "Class", "class").replace("bOrphanedPin=False", "bOrphanedPin=True"), "End Object"])
        self.assertEqual(len(graphbuild.templates_from_export(export, ["S_0"])["S_0"]["pins"]), 1)

    def test_templates_from_export(self):
        t = graphbuild.templates_from_export(EXPORT, ["A_0", "OLD_0", "Nope"])
        self.assertEqual(set(t), {"A_0"})
        self.assertEqual(len(t["A_0"]["pins"]), 1)
        self.assertTrue(t["A_0"]["props"][0].startswith("EventReference="))


if __name__ == "__main__":
    unittest.main()
