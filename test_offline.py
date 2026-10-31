"""Checks that need no editor: the filter rules, T3D parsing, paste checks and the graph builder.

    python -I test_offline.py
"""

import codecs
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpscan        # noqa: E402
import graphbuild    # noqa: E402

DUMP_LOG = [
    "[2026.10.30-23.20.14:663][ 19]LogPython: world: <Object>",
    "[2026.10.30-23.20.14:674][ 19]AbioticFactorEditorCookedLog: Display: ",
    "# /Script/Engine.AudioComponent: 40 functions, 33 variables, 6 event dispatchers, 0 blocked by ../../Build/BlueprintFilter.txt",
    "",
    "# Audio | Components | Audio",
    "/Script/Engine.AudioComponent FadeIn",
    "/Script/Engine.AudioComponent FadeOut",
    "# Variables | Sound",
    "/Script/Engine.AudioComponent Sound",
    "[2026.10.30-23.20.14:680][ 19]AbioticFactorEditorCookedLog: Display: ",
    "# /Script/Engine.PlayerController: 68 functions, 26 variables, 0 event dispatchers, 2 blocked by ../../Build/BlueprintFilter.txt",
    "",
    "# Game | Player",
    "/Script/Engine.PlayerController GetHUD",
    "# Blocked by the current rules",
    "# /Script/Engine.PlayerController SetMouseLocation",
    "# /Script/Engine.PlayerController Some Spaced Name",
    "[2026.10.30-23.20.14:690][ 19]LogSomething: # /Script/Engine.PlayerController NotInTheDump",
    "[2026.10.30-23.20.14:690][ 19]AbioticFactorEditorCookedLog: ABF.BlueprintFilter.DumpClass: no class found for '/Game/Gone.Gone_C'.",
]


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
    def setUp(self):
        self.dumps = bpscan.parse_filter_dumps(DUMP_LOG)

    def test_blocked_section(self):
        pc = self.dumps["/Script/Engine.PlayerController"]
        self.assertEqual(pc["blocked"], {"SetMouseLocation", "Some Spaced Name"})
        self.assertEqual(pc["listed"], {"GetHUD"})
        self.assertTrue(pc["rules"].endswith("BlueprintFilter.txt"))

    def test_nothing_blocked(self):
        audio = self.dumps["/Script/Engine.AudioComponent"]
        self.assertEqual(audio["blocked"], set())
        self.assertEqual(audio["listed"], {"FadeIn", "FadeOut", "Sound"})

    def test_class_not_found(self):
        self.assertIsNone(self.dumps["/Game/Gone.Gone_C"])

    def nodes(self, *refs):
        return [{"name": f"N{i}", "graph": "EventGraph", kind: {"parent": parent, "name": member}} for i, (kind, parent, member) in enumerate(refs)]

    def test_check_nodes(self):
        nodes = self.nodes(("function", "/Script/Engine.PlayerController", "SetMouseLocation"),
                           ("function", "/Script/Engine.PlayerController", "GetHUD"),
                           ("function", "/Script/Engine.AudioComponent", "FadeOut"),
                           ("variable", "/Script/Engine.PlayerController", "Some Spaced Name"),
                           ("event", "/Game/Gone.Gone_C", "Whatever"))
        r = bpscan.check_nodes(nodes, self.dumps)
        self.assertEqual([(b["member"], b["kind"]) for b in r["blocked"]], [("SetMouseLocation", "function"), ("Some Spaced Name", "variable")])
        self.assertEqual(r["not_checked"], ["/Game/Gone.Gone_C"])

    def test_only_class_references_count(self):
        node = {"name": "N", "function": {"parent": None, "name": "SelfCall"}, "event": {"parent": "/Script/UMG", "name": "Sig"},
                "targetType": "x", "pins": []}
        self.assertEqual(bpscan.member_refs(node), [])

    def test_classes_used(self):
        nodes = self.nodes(("function", "/Script/B.B", "x"), ("variable", "/Script/A.A", "y"), ("delegate", "/Script/A.A", "z"))
        self.assertEqual(bpscan.classes_used(nodes), ["/Script/A.A", "/Script/B.B"])


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

    def test_line_separator_in_text_keeps_the_pin(self):
        for char in ("\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c"):
            line = pin("CC", "InString", "string").replace('PinType.PinSubCategory=""', f'DefaultValue="a{char}b",PinType.PinSubCategory=""')
            text = clipboard().replace("End Object\n", line + "\nEnd Object\n", 1)
            node = bpscan.parse_t3d(text)["nodes"][0]
            self.assertEqual([p["name"] for p in node["pins"]], ["then", "InString"], repr(char))
            self.assertEqual(node["pins"][1]["default"], f"a{char}b")


class ReadingFiles(unittest.TestCase):
    TEXT = EXPORT.replace('ErrorMsg="Something is wrong"', 'ErrorMsg="Rotate 15\u00b0 \u00b7 Scale x2"')

    def read(self, raw, **kwargs):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.t3d"
            path.write_bytes(raw)
            return bpscan.read_t3d(path, **kwargs)

    def test_utf16_with_bom_parses(self):
        for bom, codec in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            text = self.read(bom + self.TEXT.encode(codec))
            self.assertEqual(text, self.TEXT)
            self.assertEqual([n["error"] for n in bpscan.parse_t3d(text)["nodes"] if n.get("error")], ["Rotate 15\u00b0 \u00b7 Scale x2"])

    def test_plain_ascii_and_utf8(self):
        self.assertEqual(self.read(EXPORT.encode("ascii")), EXPORT)
        self.assertEqual(self.read(self.TEXT.encode("utf-8")), self.TEXT)
        self.assertEqual(self.read(codecs.BOM_UTF8 + self.TEXT.encode("utf-8")), self.TEXT)

    def test_newlines_match_a_text_mode_read(self):
        windows = self.TEXT.replace("\n", "\r\n")
        self.assertEqual(self.read(codecs.BOM_UTF16_LE + windows.encode("utf-16-le")), self.TEXT)
        self.assertEqual(self.read(windows.encode("utf-8")), self.TEXT)

    def test_characters_outside_the_bmp(self):
        self.assertEqual(self.read(codecs.BOM_UTF16_LE + "x \U0001F600".encode("utf-16-le")), "x \U0001F600")

    def test_empty_file(self):
        self.assertEqual(self.read(b""), "")

    def test_bad_bytes(self):
        self.assertEqual(self.read(b"caf\xe9"), "caf\ufffd")
        with self.assertRaises(UnicodeDecodeError):
            self.read(b"caf\xe9", errors="strict")


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


def sent_graph(*nodes):
    """Clipboard text. Each node is (name, pin line, ...)."""
    out = []
    for name, *pins in nodes:
        out += [f'Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="{name}"', *pins, "End Object"]
    return "\n".join(out) + "\n"


def export_of(*nodes):
    """An export holding these nodes, as (name, pin line, ...), in the shape the editor writes."""
    head = ['Begin Object Class=/Script/Engine.Blueprint Name="BP"', '  Begin Object Class=/Script/Engine.EdGraph Name="EventGraph"']
    for name, *_ in nodes:
        head += [f'    Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="{name}"', "    End Object"]
    head.append("  End Object")
    body = ['  Begin Object Name="EventGraph"']
    for name, *pins in nodes:
        body += [f'    Begin Object Name="{name}"', *["      " + p for p in pins], "    End Object"]
    body += [f"    Nodes({i})=\"/Script/BlueprintGraph.K2Node_CallFunction'{n[0]}'\"" for i, n in enumerate(nodes)]
    return "\n".join(head + body + ["  End Object", "End Object"])


class PureNodes(unittest.TestCase):
    """A paste that gives a pure function exec pins: the editor takes them off and joins the chain around the node."""
    DATA = pin("K3", "ReturnValue", "object", out=True)

    def sent_chain(self):
        return sent_graph(("E", pin("A1", "then", out=True, links="K A2,")),
                          ("K", pin("A2", "execute", links="E A1,"), pin("A3", "then", out=True, links="N A4,"), self.DATA),
                          ("N", pin("A4", "execute", links="K A3,")))

    def test_chain_joined_around_it(self):
        export = export_of(("E", pin("B1", "then", out=True, links="N B4,")), ("K", self.DATA), ("N", pin("B4", "execute", links="E B1,")))
        r = graphbuild.verify(self.sent_chain(), export)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["missing_links"], [])
        self.assertEqual(r["pure_nodes"], ["K"])
        self.assertEqual(r["joined_links"], [{"from": "E.then", "to": "N.execute"}])
        self.assertEqual(r["links_sent"], 2)

    def test_chain_not_joined_is_still_a_failure(self):
        export = export_of(("E", pin("B1", "then", out=True)), ("K", self.DATA), ("N", pin("B4", "execute")))
        r = graphbuild.verify(self.sent_chain(), export)
        self.assertFalse(r["ok"])
        self.assertEqual(r["missing_links"], [{"from": "E.then", "to": "N.execute"}])
        self.assertEqual(r["joined_links"], [])

    def test_exec_pins_kept_but_links_gone_is_a_failure(self):
        export = export_of(("E", pin("B1", "then", out=True)), ("K", pin("B2", "execute"), pin("B3", "then", out=True), self.DATA), ("N", pin("B4", "execute")))
        r = graphbuild.verify(self.sent_chain(), export)
        self.assertFalse(r["ok"])
        self.assertEqual(r["pure_nodes"], [])
        self.assertEqual(r["missing_links"], [{"from": "E.then", "to": "K.execute"}, {"from": "K.then", "to": "N.execute"}])

    def test_orphaned_exec_pins_are_not_pure(self):
        orphan = pin("B2", "execute").replace("bOrphanedPin=False", "bOrphanedPin=True")
        export = export_of(("E", pin("B1", "then", out=True, links="N B4,")), ("K", orphan), ("N", pin("B4", "execute", links="E B1,")))
        r = graphbuild.verify(self.sent_chain(), export)
        self.assertFalse(r["ok"])
        self.assertEqual(r["pure_nodes"], [])

    def test_two_in_a_row(self):
        sent = sent_graph(("E", pin("A1", "then", out=True, links="P A2,")),
                          ("P", pin("A2", "execute", links="E A1,"), pin("A3", "then", out=True, links="K A4,")),
                          ("K", pin("A4", "execute", links="P A3,"), pin("A5", "then", out=True, links="N A6,")),
                          ("N", pin("A6", "execute", links="K A5,")))
        export = export_of(("E", pin("B1", "then", out=True, links="N B6,")), ("P",), ("K",), ("N", pin("B6", "execute", links="E B1,")))
        r = graphbuild.verify(sent, export)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["pure_nodes"], ["K", "P"])
        self.assertEqual(r["joined_links"], [{"from": "E.then", "to": "N.execute"}])

    def test_several_lead_into_it(self):
        sent = sent_graph(("E1", pin("A1", "then", out=True, links="K A3,")), ("E2", pin("A2", "then", out=True, links="K A3,")),
                          ("K", pin("A3", "execute", links="E1 A1,E2 A2,"), pin("A4", "then", out=True, links="N A5,")),
                          ("N", pin("A5", "execute", links="K A4,")))
        export = export_of(("E1", pin("B1", "then", out=True, links="N B5,")), ("E2", pin("B2", "then", out=True, links="N B5,")), ("K",),
                           ("N", pin("B5", "execute", links="E1 B1,E2 B2,")))
        r = graphbuild.verify(sent, export)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["joined_links"], [{"from": "E1.then", "to": "N.execute"}, {"from": "E2.then", "to": "N.execute"}])
        one_side = export.replace('LinkedTo=(N B5,),', "", 1).replace("E1 B1,", "")
        self.assertEqual(graphbuild.verify(sent, one_side)["missing_links"], [{"from": "E1.then", "to": "N.execute"}])

    def test_then_to_two_nodes_only_the_first_is_joined(self):
        sent = sent_graph(("E", pin("A1", "then", out=True, links="K A2,")),
                          ("K", pin("A2", "execute", links="E A1,"), pin("A3", "then", out=True, links="N1 A4,N2 A5,")),
                          ("N1", pin("A4", "execute", links="K A3,")), ("N2", pin("A5", "execute", links="K A3,")))
        export = export_of(("E", pin("B1", "then", out=True, links="N1 B4,")), ("K",), ("N1", pin("B4", "execute", links="E B1,")), ("N2", pin("B5", "execute")))
        r = graphbuild.verify(sent, export)
        self.assertFalse(r["ok"])
        self.assertEqual(r["joined_links"], [{"from": "E.then", "to": "N1.execute"}])
        self.assertEqual(r["missing_links"], [{"from": "E.then", "to": "N2.execute"}])

    def test_end_of_the_chain(self):
        sent = sent_graph(("E", pin("A1", "then", out=True, links="K A2,")), ("K", pin("A2", "execute", links="E A1,"), pin("A3", "then", out=True)))
        r = graphbuild.verify(sent, export_of(("E", pin("B1", "then", out=True)), ("K",)))
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["pure_nodes"], r["joined_links"]), (["K"], []))

    def test_start_of_the_chain(self):
        sent = sent_graph(("K", pin("A2", "execute"), pin("A3", "then", out=True, links="N A4,")), ("N", pin("A4", "execute", links="K A3,")))
        r = graphbuild.verify(sent, export_of(("K",), ("N", pin("B4", "execute"))))
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["pure_nodes"], r["joined_links"]), (["K"], []))

    def test_link_declared_on_one_side_only(self):
        sent = sent_graph(("E", pin("A1", "then", out=True, links="K A2,")), ("K", pin("A2", "execute"), pin("A3", "then", out=True, links="N A4,")),
                          ("N", pin("A4", "execute")))
        export = export_of(("E", pin("B1", "then", out=True, links="N B4,")), ("K",), ("N", pin("B4", "execute", links="E B1,")))
        r = graphbuild.verify(sent, export)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["pure_nodes"], ["K"])

    def test_next_node_outside_the_paste(self):
        sent = sent_graph(("E", pin("A1", "then", out=True, links="K A2,")),
                          ("K", pin("A2", "execute", links="E A1,"), pin("A3", "then", out=True, links="Elsewhere ZZ,")))
        r = graphbuild.verify(sent, export_of(("E", pin("B1", "then", out=True)), ("K",)))
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["missing_links"], r["joined_links"]), ([], []))

    def test_exec_wired_to_a_data_pin_still_fails(self):
        sent = sent_graph(("E", pin("A1", "then", out=True, links="C A2,")), ("C", pin("A2", "InInt", "int", links="E A1,")))
        r = graphbuild.verify(sent, export_of(("E", pin("B1", "then", out=True)), ("C", pin("B2", "InInt", "int"))))
        self.assertFalse(r["ok"])
        self.assertEqual(r["pure_nodes"], [])
        self.assertEqual(r["missing_links"], [{"from": "C.InInt", "to": "E.then"}])

    def test_data_link_of_a_pure_node_still_counts(self):
        sent = sent_graph(("E", pin("A1", "then", out=True, links="K A2,")),
                          ("K", pin("A2", "execute", links="E A1,"), pin("A3", "ReturnValue", "object", out=True, links="L A4,")),
                          ("L", pin("A4", "TargetArray", "object", links="K A3,")))
        export = export_of(("E", pin("B1", "then", out=True)), ("K", pin("B3", "ReturnValue", "object", out=True)), ("L", pin("B4", "TargetArray", "object")))
        r = graphbuild.verify(sent, export)
        self.assertFalse(r["ok"])
        self.assertEqual(r["pure_nodes"], ["K"])
        self.assertEqual(r["missing_links"], [{"from": "K.ReturnValue", "to": "L.TargetArray"}])

    def test_ordinary_paste_has_no_pure_nodes(self):
        sent = sent_graph(("P_0", pin("A1", "then", out=True, links="P_1 B1,")), ("P_1", pin("B1", "execute", links="P_0 A1,")))
        r = graphbuild.verify(sent, export_of(("P_0", pin("C1", "then", out=True, links="P_1 D1,")), ("P_1", pin("D1", "execute", links="P_0 C1,"))))
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["pure_nodes"], r["joined_links"]), ([], []))


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
