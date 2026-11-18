"""Checks that need no editor: the filter rules, T3D parsing, paste checks and the graph builder.

    python -I test_offline.py
"""

import codecs
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
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

    def test_float_params_are_doubles(self):
        props = graphbuild.describe({"id": "x", "type": "custom_event", "name": "Go",
                                     "params": [{"name": "Amount", "cat": "float"}, {"name": "N", "cat": "int"}]})[1]
        self.assertIn('PinType=(PinCategory="real",PinSubCategory="double")', props[1])
        self.assertIn('PinType=(PinCategory="int")', props[2])
        self.assertIn('ResolvedWildcardType=(PinCategory="real",PinSubCategory="double")',
                      graphbuild.describe({"id": "e", "type": "foreach", "element": {"cat": "real"}})[1])

    def test_custom_event_param_needs_name_and_cat(self):
        with self.assertRaises(graphbuild.SpecError):
            graphbuild.describe({"id": "x", "type": "custom_event", "name": "Go", "params": [{"name": "A"}]})

    def test_wildcard_real_gets_double(self):
        tpl = {"class": "K2Node_MakeArray", "props": ["NumInputs=1"], "error": None,
               "pins": [full_pin("[0]", "wildcard"), full_pin("Array", "wildcard", out=True)]}
        n = {"id": "a", "type": "make_array", "count": 1, "element": {"cat": "float"}}
        text = graphbuild.assemble([n], [], {"a": tpl}, "T1")["text"]
        self.assertEqual(text.count('PinType.PinCategory="real",PinType.PinSubCategory="double",'), 2)
        self.assertNotIn('PinSubCategory="",', text)
        # the same when the template's pin text has no sub-category field at all
        bare = tpl["pins"][0].replace('PinType.PinSubCategory="",', "")
        text = graphbuild.assemble([n], [], {"a": dict(tpl, pins=[bare])}, "T1")["text"]
        self.assertIn('PinType.PinCategory="real",PinType.PinSubCategory="double",', text)

    def test_raw_props_object(self):
        as_list = graphbuild.describe({"id": "r", "type": "raw", "kind": "K2Node_CustomEvent",
                                       "props": ['CustomFunctionName="Go"', "FunctionFlags=16448", "bCommentBubbleVisible=False"]})
        as_dict = graphbuild.describe({"id": "r", "type": "raw", "kind": "K2Node_CustomEvent",
                                       "props": {"CustomFunctionName": '"Go"', "FunctionFlags": 16448, "bCommentBubbleVisible": False}})
        self.assertEqual(as_list, as_dict)
        self.assertEqual(as_dict[1], ['CustomFunctionName="Go"', "FunctionFlags=16448", "bCommentBubbleVisible=False"])

    def test_node_class_and_usable_template(self):
        self.assertEqual(graphbuild.node_class(NODES[0]), "K2Node_Event")
        self.assertEqual(graphbuild.node_class({"id": "c", "type": "comment"}), "EdGraphNode_Comment")
        self.assertEqual(graphbuild.node_class({"id": "r", "type": "raw", "kind": "/Script/BlueprintGraph.K2Node_Self"}), "K2Node_Self")
        self.assertTrue(graphbuild.usable_template(EVENT))
        self.assertFalse(graphbuild.usable_template(None))
        self.assertFalse(graphbuild.usable_template(dict(EVENT, error="bad")))

    def test_dropped_event_hint(self):
        text = self.build()["text"]
        gone = {"nodes_found": 1, "missing_nodes": ["T1_0"], "landed_in": {"Fn": 1}}
        self.assertIn("landed in Fn", graphbuild.dropped_event_hint(text, gone))
        self.assertIsNone(graphbuild.dropped_event_hint(text, dict(gone, landed_in={"EventGraph": 1})))
        self.assertIsNone(graphbuild.dropped_event_hint(text, dict(gone, missing_nodes=["T1_1"])))
        self.assertIsNone(graphbuild.dropped_event_hint(text, dict(gone, nodes_found=0, missing_nodes=["T1_0", "T1_1"], landed_in={})))


class CompilerLog(unittest.TestCase):
    LINES = [
        r"[2026.11.07-02.30.16:430][668]LogBlueprint: Warning: [AssetLog] C:\Mods\X\Content\Blueprints\BP_Door.uasset: [Compiler] The structure cannot be broken",
        r"[2026.11.07-02.30.16:431][668]LogBlueprint: Error: [AssetLog] C:\Mods\X\Content\Blueprints\BP_DoorFrame.uasset: [Compiler] Missing pin",
        r"[2026.11.07-02.44.11:081][ 90]LogBlueprint: Warning: [AssetLog] Pak: ..\..\..\AbioticFactor\Content\Maps\Facility.umap: [Compiler] Node  Unknown  is not referencing a valid level actor",
        r"[2026.11.07-02.44.11:081][ 90]LogPython: BP_Door compiled",
    ]

    def test_asset_from_the_path(self):
        self.assertEqual(bpscan.compiler_message(self.LINES[0]), ("BP_Door", "warning", "The structure cannot be broken"))
        self.assertEqual(bpscan.compiler_message(self.LINES[1])[:2], ("BP_DoorFrame", "error"))
        self.assertEqual(bpscan.compiler_message(self.LINES[2])[0], "Facility")
        self.assertIsNone(bpscan.compiler_message(self.LINES[3]))

    def test_filter_by_asset_is_exact(self):
        self.assertEqual([m[2] for m in (bpscan.compiler_message(l, "BP_Door") for l in self.LINES) if m], ["The structure cannot be broken"])
        self.assertEqual([m[2] for m in (bpscan.compiler_message(l, "bp_doorframe") for l in self.LINES) if m], ["Missing pin"])

    def test_line_without_a_path_matches_the_whole_word(self):
        line = "[x]LogBlueprint: Error: BP_Door: [Compiler] Boom"
        self.assertEqual(bpscan.compiler_message(line, "BP_Door"), (None, "error", "Boom"))
        self.assertIsNone(bpscan.compiler_message(line, "BP_Do"))


class GraphBackground(unittest.TestCase):
    def test_greys_of_the_grid_only(self):
        import server
        for rgb in ((38, 38, 38), (53, 53, 53), (36, 36, 36), (51, 51, 51)):
            self.assertTrue(server.is_graph_background(rgb), rgb)
        for rgb in ((24, 24, 24), (21, 21, 21), (183, 183, 183), (109, 23, 22), (42, 62, 71), (75, 75, 75)):
            self.assertFalse(server.is_graph_background(rgb), rgb)


class Menus(unittest.TestCase):
    def test_display_name(self):
        import server
        self.assertEqual(server.display_name("ActivateRagdoll"), "Activate Ragdoll")
        self.assertEqual(server.display_name("InteractWith_A"), "Interact With A")
        self.assertEqual(server.display_name("CanJumpInternal"), "Can Jump Internal")
        self.assertEqual(server.display_name("Tick"), "Tick")

    def test_pin_type_fields(self):
        self.assertEqual(graphbuild.pin_type_fields("float"), 'PinType.PinCategory="real",PinType.PinSubCategory="double",')
        self.assertEqual(graphbuild.pin_type_fields("object", "/Script/Engine.Actor", True),
                         "PinType.PinCategory=\"object\",PinType.PinSubCategoryObject=\"/Script/CoreUObject.Class'/Script/Engine.Actor'\",PinType.ContainerType=Array,")
        self.assertIn("ScriptStruct'/Script/CoreUObject.Vector'", graphbuild.pin_type_fields("struct", "/Script/CoreUObject.Vector"))
        self.assertIn("CoreUObject.Enum'", graphbuild.pin_type_fields("byte", "/Script/Engine.ECollisionChannel"))

    def test_menu_like(self):
        import wininput
        tip = {"rect": [0, 0, 512, 78]}
        menu = {"rect": [0, 0, 204, 170]}
        one_row = {"rect": [0, 0, 268, 62]}
        self.assertFalse(wininput.menu_like(tip))
        self.assertTrue(wininput.menu_like(menu) and wininput.menu_like(one_row))
        self.assertEqual(wininput.largest_menu([tip, menu]), menu)
        self.assertEqual(wininput.largest_menu([tip]), tip)
        self.assertIsNone(wininput.largest_menu([]))

    def test_audit_completion_line(self):
        import server
        m = server.AUDIT_DONE.search("[2026.11.07-05.10.00:000][  1]AssetAudit: Asset audit complete: 2 Error(s), 1 Warning(s), took 123.321ms to complete.")
        self.assertEqual((m.group(1), m.group(2), m.group(3)), ("2", "1", "123.321ms"))


class QuickBatch(unittest.TestCase):
    def test_play_settings_text(self):
        import server
        text = "Begin Object Class=/Script/UnrealEd.LevelEditorPlaySettings Name=\"Default__LevelEditorPlaySettings\"\n   bLaunchSeparateServer=False\n   PlayNetMode=PIE_Client\n   RunUnderOneProcess=True\n   PlayNumberOfClients=2\nEnd Object\n"
        self.assertEqual(server.parse_play_settings(text), {"net_mode": "PIE_Client", "clients": 2, "separate_server": False, "one_process": True})
        self.assertEqual(server.parse_play_settings("x")["net_mode"], "PIE_Standalone")
        self.assertIsNone(server.parse_play_settings(None))
        self.assertEqual(server.PLAY_NET_MODES, {"standalone": 0, "listen_server": 1, "client": 2})

    def test_version_mismatch(self):
        import server
        self.assertEqual(server.lists_version(), "1.5")
        self.assertIsNone(server.version_mismatch("1.5.0.31876", "1.5"))
        self.assertIn("1.6.0.1", server.version_mismatch("1.6.0.1", "1.5"))
        self.assertIsNone(server.version_mismatch(None, "1.5"))

    def test_check_upload_ignore(self):
        import server
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Mine.uplugin").write_text(json.dumps({"FriendlyName": "Mine", "VersionName": "1.0"}), encoding="utf-8")
            (root / "Content").mkdir()
            (root / "Content" / "BP_Thing.uasset").write_bytes(b"\x00/Game/Nowhere/NotAPackage\x00/Game/Blueprints/Characters/Abiotic_PlayerCharacter\x00")
            plain = server.check_upload(root)
            self.assertTrue(any("Nowhere" in w for w in plain["warnings"]), plain)
            self.assertFalse(any("Abiotic_PlayerCharacter" in w for w in plain["warnings"] + plain["errors"]), plain)
            quiet = server.check_upload(root, ["/Game/Nowhere/"])
            self.assertFalse(any("Nowhere" in w for w in quiet["warnings"]), quiet)
            self.assertTrue(any("Nowhere" in w for w in quiet["ignored"]), quiet)

    def test_read_only_annotations(self):
        import server
        tools = {t["name"]: t for t in server.handle({"method": "tools/list", "params": {}})["tools"]}
        self.assertTrue(tools["editor_status"]["annotations"]["readOnlyHint"])
        self.assertNotIn("annotations", tools["set_row"])
        self.assertNotIn("annotations", tools["build_graph"])
        self.assertIn("close_editor", tools)
        self.assertIn("restart_editor", tools)


class Templates(unittest.TestCase):
    """server.py's template bookkeeping, with the editor faked out."""

    def setUp(self):
        import server
        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (server.TEMPLATES, server.run_json, server.probe_nodes)
        server.TEMPLATES = Path(self.tmp.name) / "node-templates.json"

    def tearDown(self):
        self.server.TEMPLATES, self.server.run_json, self.server.probe_nodes = self.saved
        self.tmp.cleanup()

    def fake_probe(self, result, log):
        def probe(nodes, *args):
            log.append([n["id"] for n in nodes])
            return {graphbuild.template_key(n): result for n in nodes}
        return probe

    def test_event_of_a_class_the_parent_lacks_is_refused_before_probing(self):
        s, probed = self.server, []
        s.run_json = lambda body, args=None, **kw: {"parent": "/Script/UMG.UserWidget",
                                                  "events": {"begin": {"owner_found": True, "interface": False, "is_ancestor": False}}}
        s.probe_nodes = self.fake_probe(PRINT, probed)
        templates, asked = s.templates_for(s.named_nodes(NODES), None, "/Script/UMG.UserWidget")
        self.assertIn("doesn't descend", templates["begin"]["error"])
        self.assertEqual(templates["say"], PRINT)
        self.assertEqual((probed, asked), ([["say"]], 1))
        self.assertEqual(list(json.loads(s.TEMPLATES.read_text())["nodes"].values()), [PRINT])

    def test_interface_and_unknown_owners_go_to_the_probe(self):
        s, probed = self.server, []
        s.run_json = lambda body, args=None, **kw: {"parent": "/Script/Engine.Actor", "events": {
            "begin": {"owner_found": True, "interface": True, "is_ancestor": False}}}
        s.probe_nodes = self.fake_probe(EVENT, probed)
        templates, _ = s.templates_for(s.named_nodes(NODES[:1]))
        self.assertEqual(templates["begin"], EVENT)
        self.assertEqual(probed, [["begin"]])

    def test_a_probe_result_with_an_error_is_returned_but_not_kept(self):
        s, probed = self.server, []
        s.run_json = lambda *a, **k: {"parent": "/Script/Engine.Actor", "events": {}}
        s.probe_nodes = self.fake_probe(dict(PRINT, error="the editor turned this K2Node_CallFunction into a K2Node_CustomEvent"), probed)
        templates, _ = s.templates_for(s.named_nodes(NODES[1:]))
        self.assertIn("turned this", templates["say"]["error"])
        self.assertFalse(s.TEMPLATES.exists())

    def test_loader_drops_what_older_versions_cached(self):
        s = self.server
        s.TEMPLATES.write_text(json.dumps({"build": s.editor_build(), "nodes": {
            "good": PRINT, "turned": dict(EVENT, **{"class": "K2Node_CustomEvent"}), "flagged": dict(PRINT, error="x"), "refused": None}}))
        self.assertEqual(list(s.load_templates()), ["good"])

MERGE_EXPORT = "\n".join([
    'Begin Object Class=/Script/Engine.Blueprint Name="BP_M" ExportPath="x"',
    '   Begin Object Class=/Script/Engine.EdGraph Name="EventGraph" ExportPath="x"',
    '      Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="E_0" ExportPath="x"',
    "      End Object",
    '      Begin Object Class=/Script/UMGEditor.K2Node_CreateWidget Name="C_1" ExportPath="x"',
    "      End Object",
    "   End Object",
    '   Begin Object Class=/Script/Engine.EdGraph Name="Fn" ExportPath="x"',
    '      Begin Object Class=/Script/BlueprintGraph.K2Node_FunctionEntry Name="FE_0" ExportPath="x"',
    "      End Object",
    '      Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="FC_1" ExportPath="x"',
    "      End Object",
    "   End Object",
    '   Begin Object Name="EventGraph" ExportPath="x"',
    '      Begin Object Name="E_0" ExportPath="x"',
    '         EventReference=(MemberParent="/Script/CoreUObject.Class\'/Script/Engine.Actor\'",MemberName="ReceiveBeginPlay")',
    "         bOverrideFunction=True",
    "         NodePosX=100",
    "         NodePosY=200",
    "         NodeGuid=AAAA",
    '         ErrorMsg="stale"',
    "         ErrorType=1",
    "         " + pin("AA", "then", out=True, links="C_1 BB,"),
    "      End Object",
    '      Begin Object Name="C_1" ExportPath="x"',
    '         FunctionReference=(MemberParent="/Script/CoreUObject.Class\'/Script/Engine.KismetSystemLibrary\'",MemberName="PrintString")',
    "         NodePosX=500",
    "         NodePosY=200",
    "         " + pin("BB", "execute", links="E_0 AA,"),
    "         " + pin("CC", "then", out=True),
    "         " + pin("DD", "InString", "string"),
    "      End Object",
    "      Nodes(0)=\"/Script/BlueprintGraph.K2Node_Event'E_0'\"",
    "      Nodes(1)=\"/Script/UMGEditor.K2Node_CreateWidget'C_1'\"",
    "   End Object",
    '   Begin Object Name="Fn" ExportPath="x"',
    '      Begin Object Name="FE_0" ExportPath="x"',
    "         " + pin("EE", "then", out=True, links="FC_1 FF,"),
    "      End Object",
    '      Begin Object Name="FC_1" ExportPath="x"',
    '         FunctionReference=(MemberParent="/Script/CoreUObject.Class\'/Script/Engine.Actor\'",MemberName="K2_DestroyActor")',
    "         " + pin("FF", "execute", links="FE_0 EE,"),
    "         " + pin("GG", "then", out=True),
    "      End Object",
    "      Nodes(0)=\"/Script/BlueprintGraph.K2Node_FunctionEntry'FE_0'\"",
    "      Nodes(1)=\"/Script/BlueprintGraph.K2Node_CallFunction'FC_1'\"",
    "   End Object",
    "   UbergraphPages(0)=\"/Script/Engine.EdGraph'EventGraph'\"",
    "   FunctionGraphs(0)=\"/Script/Engine.EdGraph'Fn'\"",
    "End Object",
])


class Merging(unittest.TestCase):
    def existing(self, graph="EventGraph", text=MERGE_EXPORT):
        return graphbuild.existing_nodes(bpscan.parse_t3d(text, keep_body=True), graph)

    def merge(self, links, nodes=None, graph="EventGraph"):
        nodes = [NODES[1]] if nodes is None else nodes
        return graphbuild.assemble(nodes, links, {"say": PRINT}, "M", existing=self.existing(graph))

    def test_existing_nodes_split(self):
        ev = self.existing()
        self.assertEqual(([n["name"] for n in ev["paste"]], ev["kept"]), (["E_0", "C_1"], []))
        fn = self.existing("Fn")
        self.assertEqual(([n["name"] for n in fn["paste"]], [n["name"] for n in fn["kept"]]), (["FC_1"], ["FE_0"]))
        self.assertEqual(self.existing("Nope"), {"graph": "Nope", "paste": [], "kept": []})

    def test_timeline_cannot_be_merged(self):
        text = MERGE_EXPORT.replace("K2Node_CreateWidget", "K2Node_Timeline")
        with self.assertRaises(graphbuild.SpecError):
            self.existing(text=text)

    def test_new_node_linked_to_an_existing_one(self):
        r = self.merge([["C_1.then", "say.execute"]])
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["renamed"], {"E_0": "M_x0", "C_1": "M_x1"})
        self.assertEqual(r["names"], {"say": "M_0"})
        parsed = bpscan.parse_t3d(r["text"])["nodes"]
        self.assertEqual([n["name"] for n in parsed], ["M_x0", "M_x1", "M_0"])
        self.assertEqual(bpscan.links_of(parsed), {bpscan.link_key(("M_x0", "then"), ("M_x1", "execute")),
                                                   bpscan.link_key(("M_x1", "then"), ("M_0", "execute"))})
        self.assertEqual(graphbuild.lint(r["text"]), {"errors": [], "warnings": [], "nodes": 3})
        self.assertIn('Class=/Script/UMGEditor.K2Node_CreateWidget Name="M_x1"', r["text"])
        self.assertIn("EventReference=", r["text"])
        self.assertNotIn("ErrorMsg", r["text"])
        self.assertNotIn("ExportPath", r["text"])
        self.assertIn("NodeGuid=AAAA", r["text"])                     # the old node's own, kept as it was
        self.assertRegex(r["text"], r'Name="M_0"[\s\S]*NodePosX=100\nNodePosY=520')    # below the old nodes, at their left

    def test_old_paste_markers_are_dropped(self):
        text = MERGE_EXPORT.replace('Begin Object Class=/Script/UMGEditor.K2Node_CreateWidget Name="C_1"',
                                    'Begin Object Class=/Script/UnrealEd.EdGraphNode_Comment Name="McpPasteEnd_1"')
        text = text.replace("Nodes(1)=\"/Script/UMGEditor.K2Node_CreateWidget'C_1'\"", "Nodes(1)=\"/Script/UnrealEd.EdGraphNode_Comment'McpPasteEnd_1'\"")
        text = text.replace('Begin Object Name="C_1"', 'Begin Object Name="McpPasteEnd_1"')
        self.assertEqual([n["name"] for n in self.existing(text=text)["paste"]], ["E_0"])

    def test_placeholder_events_are_dropped(self):
        ghost = MERGE_EXPORT.replace("bOverrideFunction=True", "bOverrideFunction=True" + chr(10) + "         EnabledState=Disabled")
        self.assertEqual([n["name"] for n in self.existing(text=ghost)["paste"]], ["C_1"])
        user_disabled = ghost.replace("EnabledState=Disabled", "EnabledState=Disabled" + chr(10) + "         bUserSetEnabledState=True")
        self.assertEqual([n["name"] for n in self.existing(text=user_disabled)["paste"]], ["E_0", "C_1"])

    def test_existing_links_survive_with_no_new_nodes(self):
        r = graphbuild.assemble([], [], {}, "B", existing=self.existing())
        parsed = bpscan.parse_t3d(r["text"])["nodes"]
        self.assertEqual(bpscan.links_of(parsed), {("B_x0", "then", "B_x1", "execute")})
        self.assertEqual(graphbuild.lint(r["text"])["warnings"], [])

    def test_links_to_a_kept_node_are_reported_and_stripped(self):
        r = self.merge([["FC_1.then", "say.execute"]], graph="Fn")
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["rewire"], [{"from": "M_x0.execute", "to": "FE_0.then", "was": "FC_1.execute"}])
        self.assertNotIn("FE_0", r["text"])
        self.assertEqual(graphbuild.lint(r["text"])["warnings"], [])

    def test_link_to_a_kept_node_is_a_problem(self):
        r = self.merge([["FE_0.then", "say.execute"]], graph="Fn")
        self.assertIn("wire that one in the editor", r["problems"][0])

    def test_unknown_existing_name(self):
        r = self.merge([["Nope.then", "say.execute"]])
        self.assertIn("no node with id 'Nope'", r["problems"][0])
        self.assertIn("inspect_blueprint", r["problems"][0])

    def test_bad_pin_on_an_existing_node_lists_its_pins(self):
        r = self.merge([["C_1.exec", "say.execute"]])
        self.assertIn("no pin 'exec'", r["problems"][0])
        self.assertIn("execute", r["problems"][0])

    def test_spec_id_clashing_with_an_existing_name(self):
        r = graphbuild.assemble([dict(NODES[1], id="C_1")], [], {"C_1": PRINT}, "M", existing=self.existing())
        self.assertIn("same name as a spec node id", r["problems"][0])

    def test_without_existing_nothing_changes(self):
        r = graphbuild.assemble(NODES, LINKS, TEMPLATES, "T1")
        self.assertEqual((r["renamed"], r["rewire"], r["problems"]), ({}, [], []))
        self.assertEqual([n["name"] for n in bpscan.parse_t3d(r["text"])["nodes"]], ["T1_0", "T1_1"])


# ---- 2.7.0: keys, event names, numbered packages, and the guards -------------------------------

class FastClock:
    """`time` for a function under test: sleeping moves the clock on instead of waiting."""

    def __init__(self):
        self.now = time.time()

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def __getattr__(self, name):
        return getattr(time, name)


class Keys(unittest.TestCase):
    def test_punctuation_is_its_own_key_not_its_character_code(self):
        import wininput
        self.assertEqual(wininput.key_codes("."), [0xBE])
        self.assertEqual(wininput.key_codes("["), [0xDB])
        self.assertEqual(wininput.key_codes("\\"), [0xDC])
        self.assertEqual(wininput.key_codes("ctrl+-"), [0x11, 0xBD])
        self.assertEqual(wininput.key_codes("ctrl+shift+s"), [0x11, 0x10, ord("S")])
        self.assertEqual(wininput.key_codes("Delete"), [0x2E])
        self.assertEqual(wininput.key_codes("period"), wininput.key_codes("."))
        # What the character codes of these would press: Delete, Insert, PrintScreen, Help, Right, both Windows keys, Apps, Numpad 0.
        wrong = {0x2E, 0x2D, 0x2C, 0x2F, 0x27, 0x5B, 0x5C, 0x5D, 0x60}
        for ch in ";=,-./`[\\]'":
            self.assertFalse(wrong & set(wininput.key_codes(ch)), ch)

    def test_unknown_keys_are_refused(self):
        import wininput
        for bad in ("ctrl++", "é", "!", "enterr", ""):
            with self.assertRaises(wininput.WinError, msg=bad):
                wininput.key_codes(bad)

    def test_keys_reach_only_the_window_expected(self):
        import wininput
        sent = []

        class Keyboard:
            def keybd_event(self, vk, scan, flags, extra):
                sent.append(vk)

        saved = (wininput.user32, wininput.foreground, wininput.foreground_is_editor)
        wininput.user32, wininput.foreground_is_editor = Keyboard(), (lambda: True)
        wininput.foreground = lambda: {"hwnd": 7, "title": "Facility - Unreal Editor", "editor": True}
        try:
            with self.assertRaises(wininput.WinError) as refused:
                wininput.keys("delete", 0, expect={"hwnd": 9, "title": "BP_Thing"})
            self.assertIn("Facility - Unreal Editor", str(refused.exception))
            self.assertEqual(sent, [])
            wininput.keys("delete", 0, expect={"hwnd": 7, "title": "Facility - Unreal Editor"})
            self.assertEqual(sent, [0x2E, 0x2E])                    # down, up
            wininput.keys("a", 0)                                   # no window named: any editor window will do
            wininput.keys("down", 0, expect={"hwnd": 3, "title": "<popup 1>", "popup": True})   # a menu never has the focus
            wininput.foreground_is_editor = lambda: False
            with self.assertRaises(wininput.WinError):
                wininput.keys("a", 0)
        finally:
            wininput.user32, wininput.foreground, wininput.foreground_is_editor = saved


def event_text(name="Charge", node="N_0"):
    return "\n".join([f'Begin Object Class=/Script/BlueprintGraph.K2Node_CustomEvent Name="{node}"', f'CustomFunctionName="{name}"',
                      "NodePosX=0", "NodePosY=0", pin("C1", "then", out=True), "End Object", ""])


class EventNames(unittest.TestCase):
    def test_a_probe_pastes_a_custom_event_under_its_own_name(self):
        spec = {"id": "e", "type": "custom_event", "name": "Charge", "params": [{"name": "Amount", "cat": "float"}]}
        text = graphbuild.bare_text([("McpProbeAB_0", spec)])
        self.assertIn('CustomFunctionName="McpProbeAB_0"', text)
        self.assertNotIn("Charge", text)
        self.assertIn('PinName="Amount"', text)
        self.assertEqual(spec["name"], "Charge")
        raw = {"id": "r", "type": "raw", "kind": "K2Node_CustomEvent", "props": ['CustomFunctionName="Pulse"', "bCommentBubbleVisible=False"]}
        text = graphbuild.bare_text([("McpProbeAB_1", raw)])
        self.assertIn('CustomFunctionName="McpProbeAB_1"', text)
        self.assertIn("bCommentBubbleVisible=False", text)
        self.assertEqual(raw["props"][0], 'CustomFunctionName="Pulse"')
        as_dict = dict(raw, props={"CustomFunctionName": '"Pulse"'})
        self.assertIn('CustomFunctionName="McpProbeAB_2"', graphbuild.bare_text([("McpProbeAB_2", as_dict)]))
        call = {"id": "c", "owner": "/Script/Engine.KismetSystemLibrary", "function": "PrintString"}
        self.assertIs(graphbuild.probe_copy(call, "X"), call)

    def test_the_spec_names_the_event_whatever_the_probe_called_it(self):
        node = {"id": "ev", "type": "custom_event", "name": "Charge"}
        tpl = {"class": "K2Node_CustomEvent", "error": None, "props": ['CustomFunctionName="McpProbeAB_0"', "bOverrideFunction=False"],
               "pins": [full_pin("OutputDelegate", "delegate", out=True), full_pin("then", out=True)]}
        text = graphbuild.assemble([node], [], {"ev": tpl}, "T")["text"]
        self.assertIn('CustomFunctionName="Charge"', text)
        self.assertNotIn("McpProbeAB_0", text)
        self.assertEqual(text.count("CustomFunctionName="), 1)
        self.assertIn("bOverrideFunction=False", text)          # what else the editor gave the node stays

    def test_a_renamed_event_fails_the_check(self):
        r = graphbuild.verify(event_text("Charge"), event_text("CustomEvent"))
        self.assertFalse(r["ok"])
        self.assertEqual(r["renamed_events"], [{"node": "N_0", "sent": "Charge", "now": "CustomEvent"}])
        self.assertIn("Charge (now CustomEvent)", graphbuild.renamed_event_hint(r))
        same = graphbuild.verify(event_text("Charge"), event_text("Charge"))
        self.assertTrue(same["ok"])
        self.assertIsNone(graphbuild.renamed_event_hint(same))

    def test_an_enum_parameter_is_written_as_an_enum(self):
        enum = graphbuild._user_pin({"name": "Mode", "cat": "byte", "obj": "/Script/Engine.ECollisionChannel"})
        self.assertIn("/Script/CoreUObject.Enum'/Script/Engine.ECollisionChannel'", enum)
        self.assertIn("ScriptStruct'/Script/CoreUObject.Vector'", graphbuild._user_pin({"name": "At", "cat": "struct", "obj": "/Script/CoreUObject.Vector"}))
        self.assertIn("CoreUObject.Class'/Script/Engine.Actor'", graphbuild._user_pin({"name": "Who", "cat": "object", "obj": "/Script/Engine.Actor"}))
        wild = 'PinId=AA,PinName="Element",PinType.PinCategory="wildcard",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,'
        self.assertIn("CoreUObject.Enum'/Script/Engine.ECollisionChannel'", graphbuild._retype(wild, {"cat": "byte", "obj": "/Script/Engine.ECollisionChannel"}))


class NumberedNames(unittest.TestCase):
    def test_a_numbered_package_is_found_by_the_part_before_its_number(self):
        import server
        retail = {"AbioticFactor/Content/Tex/T_Noise_1.uasset", "AbioticFactor/Content/Tex/T_Plain.uasset",
                  "AbioticFactor/Content/Tex/T_Both_2.uasset", "AbioticFactor/Content/Tex/T_Padded_01.uasset"}
        editor = retail | {"AbioticFactor/Content/Dev/T_Debug_3.uasset", "AbioticFactor/Content/Tex/T_Both.uasset"}
        saved, kept = server.asset_list, dict(server._NUMBERED)
        server.asset_list = lambda name: retail if name == "retail" else editor
        server._NUMBERED.clear()
        try:
            self.assertEqual(sorted(server.numbered_packages("retail")), ["AbioticFactor/Content/Tex/T_Both", "AbioticFactor/Content/Tex/T_Noise"])
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / "Mine.uplugin").write_text(json.dumps({"FriendlyName": "Mine", "VersionName": "1.0"}), encoding="utf-8")
                (root / "Content").mkdir()
                (root / "Content" / "M_Thing.uasset").write_bytes(
                    b"\x00/Game/Tex/T_Noise\x00/Game/Dev/T_Debug\x00/Game/Tex/T_Both\x00/Game/Tex/T_Nowhere\x00/Game/Tex/T_Padded_01\x00")
                r = server.check_upload(root)
            said = r["errors"] + r["warnings"]
            self.assertFalse(any("T_Noise" in line or "T_Padded" in line for line in said), r)
            self.assertTrue(any("T_Debug" in line and "/Game/Dev/T_Debug_3" in line and "only in the editor" in line for line in r["errors"]), r)
            self.assertTrue(any("T_Both" in line and "either" in line and "/Game/Tex/T_Both_2" in line for line in r["warnings"]), r)
            self.assertTrue(any("T_Nowhere" in line and "not in the retail or editor" in line for line in r["warnings"]), r)
            self.assertEqual(r["verdict"], "FAIL")
        finally:
            server.asset_list = saved
            server._NUMBERED.clear()
            server._NUMBERED.update(kept)


def log_line(seconds_ago, text, now=None):
    stamp = time.strftime("%Y.%m.%d-%H.%M.%S", time.gmtime((now or time.time()) - seconds_ago))
    return f"[{stamp}:000][  1]{text}"


class Closing(unittest.TestCase):
    """close_editor against a fake editor: which windows it has, what its log says, whether it answers."""

    CLEAN = {"dirty_packages": {"content": [], "maps": []}, "open_mods": [{"root": "/MyMod"}]}
    MAIN = "Facility - Unreal Editor"

    def setUp(self):
        import server
        import wininput
        self.s, self.w = server, wininput
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "AbioticFactor.log"
        self.saved = (wininput.editor_windows, wininput.close_main_window, wininput.close_window, wininput.editor_pids,
                      server.run_json, server.log_file, server.time)
        server.time = FastClock()
        server.log_file = lambda name=None: self.log
        self.sent = []

    def tearDown(self):
        (self.w.editor_windows, self.w.close_main_window, self.w.close_window, self.w.editor_pids,
         self.s.run_json, self.s.log_file, self.s.time) = self.saved
        self.tmp.cleanup()

    def editor(self, titles, new=(), status=None):
        """These windows until the main one is asked to close; then `new` ones join them; with no new one it is gone
        after a few looks. With no window at all it is already on its way out."""
        state = {"looks": 0, "closing": not titles}

        def gone():
            return not new and state["closing"] and state["looks"] > 2

        def window(hwnd, title):
            return {"hwnd": hwnd, "title": title, "pid": 1, "rect": [0, 0, 1600, 900], "minimized": False}

        def windows(popups=False):
            if state["closing"]:
                state["looks"] += 1
            if gone():
                return []
            return ([window(10 + i, t) for i, t in enumerate(titles)]
                    + ([window(90 + i, t) for i, t in enumerate(new)] if state["closing"] else []))

        def asked(*a, **k):
            if isinstance(status, Exception):
                raise status
            return status or self.CLEAN

        self.w.editor_windows = windows
        self.w.close_main_window = lambda w: (self.sent.append(w["title"]), state.update(closing=True))
        self.w.close_window = lambda w: self.sent.append("close " + w["title"])
        self.w.editor_pids = lambda: [] if gone() else [1]
        self.s.run_json = asked

    def test_closes_when_nothing_is_in_the_way(self):
        self.editor([self.MAIN])
        r = self.s.close_editor(wait=30)
        self.assertTrue(r["closed"], r)
        self.assertEqual(self.sent, [self.MAIN])

    def test_an_open_upload_window_keeps_it_open(self):
        self.editor([self.MAIN, "Upload Mod"])
        r = self.s.close_editor(wait=30)
        self.assertFalse(r["closed"])
        self.assertIn("Upload Mod window", r["error"])
        self.assertEqual(self.sent, [])

    def test_an_upload_in_the_log_keeps_it_open_until_it_ends(self):
        self.editor([self.MAIN])
        self.log.write_text("\n".join([log_line(400, "LogModio: Uploaded with status 0"),
                                       log_line(20, "LogModio: Display: Beginning submission of mod source 1234567"),
                                       log_line(5, "LogHttp: something else")]) + "\n", encoding="utf-8")
        r = self.s.close_editor(wait=30)
        self.assertFalse(r["closed"])
        self.assertIn("upload that began", r["error"])
        self.assertEqual(self.sent, [])
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(log_line(1, "LogModio: ModManagementEvent Uploaded with status 0") + "\n")
        self.assertTrue(self.s.close_editor(wait=30)["closed"])

    def test_an_upload_begun_long_ago_does_not_count(self):
        self.editor([self.MAIN])
        self.log.write_text(log_line(2 * 3600, "LogModio: Display: Beginning submission of mod source 1") + "\n", encoding="utf-8")
        self.assertTrue(self.s.close_editor(wait=30)["closed"])

    def test_force_closes_during_an_upload(self):
        self.editor([self.MAIN, "Upload Mod"])
        self.assertTrue(self.s.close_editor(wait=30, force=True)["closed"])
        self.assertEqual(self.sent, [self.MAIN])

    def test_an_editor_that_does_not_answer_is_left_open(self):
        self.editor([self.MAIN], status=self.s.RemoteError("no editor answered"))
        r = self.s.close_editor(wait=30)
        self.assertFalse(r["closed"])
        self.assertIn("isn't answering", r["error"])
        self.assertEqual(self.sent, [])
        forced = self.s.close_editor(wait=30, force=True)
        self.assertTrue(forced["closed"], forced)
        self.assertIn("closing anyway", forced["note"])

    def test_unsaved_packages_are_never_forced(self):
        self.editor([self.MAIN], status={"dirty_packages": {"content": ["/MyMod/BP_X"], "maps": []}, "open_mods": []})
        r = self.s.close_editor(wait=30, force=True)
        self.assertFalse(r["closed"])
        self.assertIn("Unsaved packages", r["error"])
        self.assertEqual(self.sent, [])

    def test_an_editor_already_on_its_way_out_is_waited_for(self):
        self.editor([], status=self.s.RemoteError("no editor answered"))
        r = self.s.close_editor(wait=30)
        self.assertTrue(r["closed"], r)
        self.assertEqual(self.sent, [])

    def test_a_window_with_save_in_its_name_is_not_a_save_dialog(self):
        self.editor([self.MAIN, "BP_SaveStation", "SaveSlotWidget"])
        r = self.s.close_editor(wait=30)
        self.assertTrue(r["closed"], r)

    def test_a_save_dialog_that_opens_stops_the_close(self):
        self.editor([self.MAIN], new=["Save Content"])
        r = self.s.close_editor(wait=30)
        self.assertFalse(r["closed"])
        self.assertEqual(r["dialog"], "Save Content")

    def test_upload_in_progress_reads_the_newest_line(self):
        now = time.time()
        self.log.write_text(log_line(50, "LogModio: Beginning submission of mod 7", now) + "\n", encoding="utf-8")
        self.assertIn("50 s ago", self.s.upload_in_progress([], now))
        self.assertIsNone(self.s.upload_in_progress([], now + self.s.UPLOAD_WINDOW))
        self.log.write_text(log_line(50, "LogModio: Beginning submission of mod 7", now) + "\n"
                            + log_line(9, "LogModio: Mod upload canceled", now) + "\n", encoding="utf-8")
        self.assertIsNone(self.s.upload_in_progress([], now))
        self.log.unlink()
        self.assertIsNone(self.s.upload_in_progress([], now))


def blueprint_export(graphs):
    """A Blueprint export holding {graph: [(class path, node name, [body lines])]}: declarations, then definitions."""
    lines = ['Begin Object Class=/Script/Engine.Blueprint Name="BP_T" ExportPath="x"']
    for graph, nodes in graphs.items():
        lines.append(f'   Begin Object Class=/Script/Engine.EdGraph Name="{graph}" ExportPath="x"')
        for cls, name, _ in nodes:
            lines += [f'      Begin Object Class={cls} Name="{name}" ExportPath="x"', "      End Object"]
        lines.append("   End Object")
    for graph, nodes in graphs.items():
        lines.append(f'   Begin Object Name="{graph}" ExportPath="x"')
        for cls, name, body in nodes:
            lines += [f'      Begin Object Name="{name}" ExportPath="x"'] + ["         " + b for b in body] + ["      End Object"]
        lines += [f"      Nodes({i})=\"{cls}'{name}'\"" for i, (cls, name, _) in enumerate(nodes)]
        lines.append("   End Object")
    lines.append("   UbergraphPages(0)=\"/Script/Engine.EdGraph'EventGraph'\"")
    lines += [f"   FunctionGraphs({i})=\"/Script/Engine.EdGraph'{g}'\"" for i, g in enumerate(g for g in graphs if g != "EventGraph")]
    return "\n".join(lines + ["End Object"])


class ClearingGraphs(unittest.TestCase):
    """paste_checked against a fake Blueprint window: a paste lands in the graph tab that is showing."""

    BG = "/Script/BlueprintGraph."

    def setUp(self):
        import server
        self.s = server
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.file = root / "BP_T.uasset"
        self.file.write_bytes(b"saved state")
        self.graphs = {"EventGraph": [(self.BG + "K2Node_Event", "OLD_0", ['EventReference=(MemberName="ReceiveBeginPlay")', "NodePosX=0", "NodePosY=0"])],
                       "Fn": [(self.BG + "K2Node_FunctionEntry", "FE_0", []), (self.BG + "K2Node_CallFunction", "FC_1", ["NodePosX=300", "NodePosY=0"])]}
        self.showing, self.cleared, self.stuck, self.rename = "EventGraph", [], False, False
        names = ("BACKUPS", "open_asset_window", "window_titles", "empty_graph_point", "paste_into", "clear_showing_graph",
                 "export_mod_t3d", "compile_blueprint", "editor_memory", "run_json", "show_event_graph")
        self.saved = {n: getattr(server, n) for n in names}
        server.BACKUPS = root / "backups"
        server.open_asset_window = lambda path, wait=20.0: ({"hwnd": 1, "title": "BP_T", "rect": [0, 0, 1600, 900]}, {"widget": False, "name": "BP_T"})
        server.window_titles = lambda: set()
        server.empty_graph_point = lambda win, widget=False, radius=20: (100, 100, "usual")
        server.paste_into = self.paste
        server.clear_showing_graph = self.clear
        server.export_mod_t3d = lambda path: blueprint_export(self.graphs)
        server.compile_blueprint = lambda path, save=True, save_on_error=False: {"ok": True, "errors": [], "warnings": [], "saved": save}
        server.editor_memory = lambda: {}
        server.run_json = lambda body, args=None, **kw: {"package": "/M/BP_T", "file": str(self.file), "unsaved": True}
        server.show_event_graph = lambda win, widget=False: setattr(self, "showing", "EventGraph")

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(self.s, n, v)
        self.tmp.cleanup()

    def paste(self, win, text, x, y, clear=False, settle=3.0):
        for n in bpscan.parse_t3d(text, keep_body=True)["nodes"]:
            body = [b.strip() for b in n["body"] if b.strip()]
            if self.rename:
                body = [re.sub(r'^CustomFunctionName="[^"]*"', 'CustomFunctionName="CustomEvent"', b) for b in body]
            self.graphs[self.showing].append((n["class_path"], n["name"], body))

    def clear(self, win):
        self.cleared.append(self.showing)
        if not self.stuck:
            self.graphs[self.showing] = [n for n in self.graphs[self.showing] if n[0].endswith("K2Node_FunctionEntry")]

    def names(self, graph):
        return [name for _, name, _ in self.graphs[graph] if not name.startswith("McpPasteEnd_")]

    def test_clear_refuses_when_another_graph_is_showing(self):
        self.showing = "Fn"
        report, error = self.s.paste_checked(event_text(), "/M/BP_T", clear=True)
        self.assertTrue(error)
        self.assertEqual((report["ok"], report["pasted"], report["showing"]), (False, False, "Fn"))
        self.assertIn("nothing was deleted", report["note"])
        self.assertEqual(self.cleared, [])
        self.assertEqual((self.names("Fn"), self.names("EventGraph")), (["FE_0", "FC_1"], ["OLD_0"]))

    def test_clear_empties_the_event_graph_after_copying_it_aside(self):
        report, error = self.s.paste_checked(event_text(), "/M/BP_T", clear=True)
        self.assertFalse(error, report)
        self.assertEqual((report["cleared"], self.cleared), ("EventGraph", ["EventGraph"]))
        self.assertEqual((self.names("EventGraph"), self.names("Fn")), (["N_0"], ["FE_0", "FC_1"]))
        backup = report["backup"]
        self.assertEqual(Path(backup["file"]).read_bytes(), b"saved state")
        self.assertIn("unsaved changes", backup["file_note"])
        self.assertEqual(backup["graph_nodes"], 1)
        self.assertIn("ReceiveBeginPlay", Path(backup["graph_text"]).read_text(encoding="utf-8"))
        self.assertIn("restore_asset", backup["restore"])
        self.assertIn("compile_after_clear", report)

    def test_clear_of_a_named_function_graph(self):
        self.showing = "Fn"
        report, error = self.s.paste_checked(event_text(), "/M/BP_T", clear=True, graph="Fn")
        self.assertFalse(error, report)
        self.assertEqual(report["cleared"], "Fn")
        self.assertEqual((self.names("Fn"), self.names("EventGraph")), (["FE_0", "N_0"], ["OLD_0"]))

    def test_a_delete_that_removed_nothing_stops_before_the_paste(self):
        self.stuck = True
        report, error = self.s.paste_checked(event_text(), "/M/BP_T", clear=True)
        self.assertTrue(error)
        self.assertEqual((report["pasted"], report["not_cleared"]), (False, ["OLD_0"]))
        self.assertEqual(self.names("EventGraph"), ["OLD_0"])

    def test_a_paste_that_lands_in_another_graph_than_the_one_named(self):
        self.showing = "Fn"
        report, error = self.s.paste_checked(event_text(), "/M/BP_T", graph="EventGraph", compile_it=True)
        self.assertTrue(error)
        self.assertIn("landed in Fn, not EventGraph", report["hint"])
        self.assertNotIn("compile", report)
        plain, error = self.s.paste_checked(event_text(node="N_1"), "/M/BP_T")       # no graph named: where it lands is reported, as before
        self.assertFalse(error, plain)
        self.assertEqual(plain["landed_in"], {"Fn": 2})

    def test_old_copies_are_removed(self):
        folder = self.s.BACKUPS
        folder.mkdir(parents=True)
        for i in range(5):
            f = folder / f"BP_T-{i}.uasset"
            f.write_bytes(b"x")
            os.utime(f, (1000 + i, 1000 + i))
        self.s.prune_backups(keep=2)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["BP_T-3.uasset", "BP_T-4.uasset"])

    def test_a_renamed_custom_event_is_an_error(self):
        self.rename = True
        report, error = self.s.paste_checked(event_text(), "/M/BP_T", compile_it=True)
        self.assertTrue(error)
        self.assertEqual(report["renamed_events"], [{"node": "N_0", "sent": "Charge", "now": "CustomEvent"}])
        self.assertIn("renamed 1 custom event", report["hint"])
        self.assertNotIn("compile", report)


class Worlds(unittest.TestCase):
    def setUp(self):
        import server
        self.s = server
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.saved = (server.WORLDS, server.WORLD_BACKUPS, server.WORLD_AUTO_KEEP, server.editor_process_running, server.run_json)
        server.WORLDS, server.WORLD_BACKUPS = root / "Editor" / "Worlds", root / "copies"
        server.editor_process_running = lambda: False
        self.world = server.WORLDS / "Cascade"
        (self.world / "Players").mkdir(parents=True)
        (self.world / "WorldSave_Facility.sav").write_bytes(b"facility 1")
        (self.world / "Players" / "Player_1.sav").write_bytes(b"player 1")

    def tearDown(self):
        (self.s.WORLDS, self.s.WORLD_BACKUPS, self.s.WORLD_AUTO_KEEP, self.s.editor_process_running, self.s.run_json) = self.saved
        self.tmp.cleanup()

    def play(self, n):
        (self.world / "WorldSave_Facility.sav").write_bytes(b"facility after play %d" % n)
        (self.world / f"Extra_{n}.sav").write_bytes(b"x")

    def test_backup_then_restore_brings_the_world_back(self):
        s = self.s
        first = s.backup_world("Cascade", auto=True)
        self.assertTrue(first["new"])
        self.assertEqual((first["files"], first["hash"]), (2, s.folder_hash(self.world)))
        again = s.backup_world("Cascade", auto=True)
        self.assertEqual((again["new"], again["name"]), (False, first["name"]))
        self.assertEqual(s.world_status()["worlds"][0]["same_as_backup"], first["name"])
        self.play(1)
        self.assertIsNone(s.world_status("Cascade")["worlds"][0]["same_as_backup"])
        r = s.restore_world("Cascade", first["name"])
        self.assertTrue(r["hash_checked"], r)
        self.assertEqual(s.folder_hash(self.world), first["hash"])
        self.assertFalse((self.world / "Extra_1.sav").exists())
        self.assertEqual(sorted(p.name for p in s.WORLDS.parent.iterdir()), ["Worlds"])        # no half-way folders left
        kept = next(b for b in s.world_backups("Cascade") if b["name"] == r["replaced_state_kept_as"])
        self.assertEqual((Path(kept["folder"]) / "Extra_1.sav").read_bytes(), b"x")
        self.assertIn("already equals", s.restore_world("Cascade", first["name"])["note"])

    def test_a_copy_before_play_only_when_the_world_changed(self):
        s = self.s
        first = s.auto_backup_worlds()
        self.assertEqual((first[0]["world"], first[0]["new"], first[0]["note"]), ("Cascade", True, "First copy of this world."))
        self.assertFalse(s.auto_backup_worlds()[0]["new"])
        self.play(1)
        third = s.auto_backup_worlds()[0]
        self.assertTrue(third["new"])
        self.assertIn("someone has played", third["note"])
        self.assertEqual(len(s.world_backups("Cascade")), 2)

    def test_old_automatic_copies_go_and_named_ones_stay(self):
        s = self.s
        s.WORLD_AUTO_KEEP = 2
        named = s.backup_world("Cascade", label="before tests: 0.7!")
        self.assertEqual(named["name"].split("-", 2)[2], "before-tests-0.7")
        for n in range(4):
            self.play(n)
            s.backup_world("Cascade", auto=True)
        backups = s.world_backups("Cascade")
        self.assertEqual(len([b for b in backups if b["auto"]]), 2)
        self.assertIn(named["name"], [b["name"] for b in backups])
        self.assertEqual(backups[0]["hash"], s.folder_hash(self.world))        # newest first

    def test_restore_refuses_during_play_and_for_an_unknown_copy(self):
        s = self.s
        first = s.backup_world("Cascade")
        with self.assertRaises(s.RemoteError) as unknown:
            s.restore_world("Cascade", "nope")
        self.assertIn(first["name"], str(unknown.exception))
        self.play(1)
        s.editor_process_running = lambda: True
        s.run_json = lambda *a, **k: {"playing": True}
        with self.assertRaises(s.RemoteError) as playing:
            s.restore_world("Cascade", first["name"])
        self.assertIn("Play In Editor is running", str(playing.exception))
        self.assertTrue((self.world / "Extra_1.sav").exists())

    def test_the_tool(self):
        s = self.s
        made = json.loads(s.t_test_world({"action": "backup", "label": "mine"}))
        self.assertTrue(made["new"])
        status = json.loads(s.t_test_world({}))
        self.assertEqual(status["worlds"][0]["backups"][0]["label"], "mine")
        text, error = s.t_test_world({"action": "restore"})
        self.assertTrue(error)
        self.assertIn(made["name"], text)


class Sharing(unittest.TestCase):
    def other_process(self, name):
        code = (f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parent)!r}); import wininput; "
                f"print(wininput.InputLock({name!r}).acquire(0.2))")
        return subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120).stdout.strip()

    def test_one_process_drives_at_a_time(self):
        import wininput
        name = "Local\\abf-mcp-test-" + uuid.uuid4().hex
        lock = wininput.InputLock(name)
        self.assertTrue(lock.acquire(0))
        self.assertTrue(lock.acquire(0))                # a tool that calls another tool
        self.assertEqual(self.other_process(name), "False")
        lock.release()
        self.assertEqual(self.other_process(name), "False")
        lock.release()
        self.assertEqual(self.other_process(name), "True")

    def test_who_drove_last(self):
        import server
        saved = server.DRIVER
        with tempfile.TemporaryDirectory() as d:
            server.DRIVER = Path(d) / "sub" / "driver.json"
            try:
                self.assertIsNone(server.read_driver())
                server.write_driver("paste_graph", 100.0, 110.0)
                mine = server.read_driver(now=130.0)
                self.assertEqual((mine["tool"], mine["seconds_ago"], mine["this_chat"], mine["server_alive"]), ("paste_graph", 20, True, True))
                self.assertIsNone(server.other_chat_note(mine))
                other = dict(mine, this_chat=False, server_pid=4242)
                self.assertIn("process 4242", server.other_chat_note(other))
                self.assertIsNone(server.other_chat_note(dict(other, server_alive=False)))
                self.assertIsNone(server.other_chat_note(dict(other, seconds_ago=server.OTHER_CHAT_WINDOW + 1)))
                self.assertIsNone(server.other_chat_note(None))
            finally:
                server.DRIVER = saved

    def test_a_driving_tool_holds_the_lock_and_adds_the_note_as_its_own_item(self):
        import server
        import wininput
        saved = (server.DRIVER, server.LOCK, server.read_driver, dict(server.TOOL_MAP))
        seen = []

        def click(args):
            seen.append(server.LOCK.held)
            if args.get("boom"):
                raise wininput.WinError("no such window")
            return json.dumps({"clicked": True})

        with tempfile.TemporaryDirectory() as d:
            server.DRIVER = Path(d) / "driver.json"
            server.LOCK = wininput.InputLock("Local\\abf-mcp-test-" + uuid.uuid4().hex)
            server.TOOL_MAP["editor_click"] = ("editor_click", click, "", {}, [])
            server.read_driver = lambda now=None: {"server_pid": 4242, "tool": "build_graph", "seconds_ago": 12, "this_chat": False, "server_alive": True}
            try:
                out = server.handle({"method": "tools/call", "params": {"name": "editor_click", "arguments": {}}})
                self.assertEqual(json.loads(out["content"][0]["text"]), {"clicked": True})
                self.assertIn("another chat's server (process 4242)", out["content"][1]["text"])
                self.assertEqual((seen, server.LOCK.held), ([1], 0))
                self.assertEqual(json.loads(server.DRIVER.read_text())["tool"], "editor_click")
                failed = server.handle({"method": "tools/call", "params": {"name": "editor_click", "arguments": {"boom": True}}})
                self.assertTrue(failed["isError"])
                self.assertEqual(server.LOCK.held, 0)
            finally:
                server.DRIVER, server.LOCK, server.read_driver = saved[:3]
                server.TOOL_MAP.clear()
                server.TOOL_MAP.update(saved[3])

    def test_tool_list(self):
        import server
        tools = {t["name"]: t for t in server.handle({"method": "tools/list", "params": {}})["tools"]}
        self.assertEqual(len(tools), 46)
        self.assertIn("force", tools["close_editor"]["inputSchema"]["properties"])
        self.assertIn("graph", tools["paste_graph"]["inputSchema"]["properties"])
        self.assertIn("backup_world", tools["play_in_editor"]["inputSchema"]["properties"])
        self.assertEqual(tools["restore_asset"]["inputSchema"]["required"], ["asset_path", "backup_file"])
        self.assertNotIn("annotations", tools["test_world"])
        self.assertTrue(server.DRIVING_TOOLS <= set(tools))
        self.assertFalse(server.DRIVING_TOOLS & server.READ_ONLY_TOOLS)


class Memory(unittest.TestCase):
    def test_when_to_say_restart(self):
        import server
        self.assertIsNone(server.memory_note(6.0, 30.0, 48.0))
        self.assertIn("restart it", server.memory_note(30.0, 12.0, 48.0))
        self.assertIn("3.0 GB left", server.memory_note(6.0, 3.0, 48.0))
        self.assertIsNone(server.memory_note(0.0, 3.0, 48.0))          # no editor running

    def test_readers_on_this_process(self):
        import wininput
        in_ram, committed = wininput.process_memory(os.getpid())
        self.assertGreater(in_ram, 1 << 20)
        self.assertGreater(committed, 1 << 20)
        limit, free, ram = wininput.system_memory()
        self.assertGreater(limit, free)
        self.assertGreater(ram, 1 << 30)
        self.assertTrue(wininput.pid_alive(os.getpid()))
        self.assertFalse(wininput.pid_alive(0x7FFFFFF0))


if __name__ == "__main__":
    unittest.main()
