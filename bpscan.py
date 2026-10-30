"""Offline Blueprint helpers: parse T3D exports, and check their references against what the editor's
Blueprint filter blocks.

The rules come from the editor itself. Its console command ABF.BlueprintFilter.DumpClass <class> prints, per class:

    # /Script/Engine.PlayerController: 68 functions, 26 variables, 0 event dispatchers, 1 blocked by <rules file>
    /Script/Engine.PlayerController GetHUD
    ...
    # Blocked by the current rules
    # /Script/Engine.PlayerController SetMouseLocation

so class inheritance and rule order are the editor's own, and they're the rules its Asset Audit uses.
"""
import re


# ---- filter ---------------------------------------------------------------------

_DUMP_HEAD = re.compile(r"^# (/\S+): \d+ functions, \d+ variables, \d+ event dispatchers, \d+ blocked by (.+)$")
_DUMP_BLOCKED_TITLE = "# Blocked by the current rules"
_DUMP_MISSING = re.compile(r"DumpClass: no class found for '([^']+)'")


def parse_filter_dumps(lines):
    """{class: {'blocked': set, 'listed': set, 'rules': file}} from the log lines DumpClass wrote.
    A class it couldn't find has the value None."""
    dumps, current, in_blocked = {}, None, False
    for line in lines:
        missing = _DUMP_MISSING.search(line)
        if missing:
            dumps[missing.group(1)] = None
            current = None
            continue
        head = _DUMP_HEAD.match(line)
        if head:
            current = dumps.setdefault(head.group(1), {"blocked": set(), "listed": set(), "rules": head.group(2)})
            current["path"] = head.group(1)
            in_blocked = False
            continue
        if current is None:
            continue
        if line.startswith(_DUMP_BLOCKED_TITLE):
            in_blocked = True
            continue
        path = current["path"]
        if line.startswith(f"# {path} ") and in_blocked:
            current["blocked"].add(line[len(path) + 3:].strip())
        elif line.startswith(f"{path} "):
            current["listed"].add(line[len(path) + 1:].strip())
        elif line.startswith("["):       # the next ordinary log line: this dump is over
            current = None
    return dumps


# ---- T3D parsing ------------------------------------------------------------
# A Blueprint export lists each object twice under its graph: first a bare declaration
# (`Begin Object Class=... Name=...` / `End Object`), then a definition (`Begin Object Name=...`)
# holding the properties. Both are merged by name here. Clipboard text has one block per node.
#
# An export also holds nodes nobody placed: deleted ones (kept for undo), and whole graphs a compile generates
# (ExecuteUbergraph_X, event stubs, Spawn Actor expanded into Set*PropertyByName calls). A node is live when its graph's
# Nodes(i)= lines list it, and that graph is one the Blueprint itself lists.

_BEGIN = re.compile(r'^(\s*)Begin Object(?: Class=(\S+))? Name="([^"]+)"')
_PATHREF = re.compile(r"""(?:Class|BlueprintGeneratedClass|WidgetBlueprintGeneratedClass|AnimBlueprintGeneratedClass|Function|ScriptStruct|Enum|UserDefinedEnum|UserDefinedStruct|Object|Package)'"?(/[^'"]+)'?"?""")
_GRAPH_NODE = re.compile(r"""^\s*Nodes\(\d+\)="?[^'"\n]*'([^'"\n]+)'""", re.M)
_REAL_GRAPH = re.compile(r"""^\s*(?:UbergraphPages|FunctionGraphs|MacroGraphs|DelegateSignatureGraphs)\(\d+\)="?[^'"\n]*'([^'"\n]+)'""", re.M)
_INTERFACE_LINE = re.compile(r"^\s*ImplementedInterfaces\(\d+\)=(.*)$", re.M)


def _member_ref(body, kind):
    m = re.search(kind + r'=\((?:MemberParent="?([^",)]*)"?,)?(?:MemberScope="[^"]*",)?MemberName="([^"]+)"', body)
    if not m:
        return None
    parent = m.group(1) or ""
    pm = _PATHREF.search(parent) if parent else None
    return {"parent": pm.group(1) if pm else (parent or None), "name": m.group(2)}


def parse_pin(pin):
    """Fields of the text inside `CustomProperties Pin (...)`."""
    name = re.search(r'PinName="([^"]*)"', pin)
    pid = re.search(r"PinId=([0-9A-Fa-f]+)", pin)
    cat = re.search(r'PinType\.PinCategory="([^"]*)"', pin)
    obj = re.search(r"PinType\.PinSubCategoryObject=([^,]*)", pin)
    linked = re.findall(r"LinkedTo=\(([^)]*)\)", pin)
    entry = {"name": name.group(1) if name else "?", "type": cat.group(1) if cat else "?",
             "dir": "out" if 'Direction="EGPD_Output"' in pin else "in", "id": pid.group(1) if pid else None}
    if obj and obj.group(1) not in ("None", ""):
        pm = _PATHREF.search(obj.group(1))
        entry["object"] = pm.group(1) if pm else obj.group(1).strip('"')
    if "PinType.ContainerType=Array" in pin:
        entry["array"] = True
    if linked and linked[0]:
        pairs = [l.strip().split(" ") for l in linked[0].split(",") if l.strip()]
        entry["linked"] = [p[0] for p in pairs]
        entry["linked_ids"] = [(p[0], p[1]) for p in pairs if len(p) > 1]
    default = re.search(r'DefaultValue="((?:[^"\\]|\\.)*)"', pin)
    if default and default.group(1):
        entry["default"] = default.group(1)[:80]
    if "bHidden=True" in pin:
        entry["hidden"] = True
    if "bOrphanedPin=True" in pin:
        entry["orphaned"] = True
    return entry


def _parse_node_props(node, btxt):
    for kind, key in (("FunctionReference", "function"), ("EventReference", "event"), ("VariableReference", "variable"),
                      ("DelegateReference", "delegate"), ("MacroGraphReference", "macro")):
        ref = _member_ref(btxt, kind)
        if ref:
            node[key] = ref
    for key in ("CustomFunctionName", "InputKey", "StructType", "TargetType", "ProxyFactoryClass", "ProxyClass", "NodeComment"):
        mm = re.search(r"^\s*" + key + r"=(.*)$", btxt, re.M)
        if mm:
            node[key[0].lower() + key[1:]] = mm.group(1).strip().strip('"')[:160]
    err = re.search(r'ErrorMsg="(.*)"', btxt)
    if err:
        severity = re.search(r"^\s*ErrorType=(\d+)", btxt, re.M)      # EMessageSeverity: 1 is an error, the rest are warnings and notes
        node["warning" if severity and severity.group(1) != "1" else "error"] = err.group(1)[:300]
    pins = [parse_pin(pin) for pin in re.findall(r"^\s*CustomProperties Pin \((.*)\)\s*$", btxt, re.M)]
    orphans = [p["name"] for p in pins if p.get("orphaned")]
    if orphans:
        node["orphaned_pins"] = orphans
    node["pins"] = pins
    node["object_refs"] = sorted({r for r in _PATHREF.findall(btxt) if r.startswith(("/Script/", "/Game/", "/Engine/"))})


def parse_t3d(text, include_dead=False, keep_body=False):
    """Return {'blueprint': {...}, 'graphs': {graph: node_count}, 'nodes': [...], 'dead_nodes': n}.

    Works on exports and on clipboard text. Dead nodes are left out unless include_dead (each node then has 'live').
    keep_body adds each node's raw lines, for reuse as a template.
    """
    objects = {}          # (outer key, name) -> {'class':..., 'outer':..., 'body': [lines]}
    order = []
    stack = []            # keys of enclosing objects
    bp = {}
    for line in text.splitlines():
        m = _BEGIN.match(line)
        if m:
            cls, name = (m.group(2) or ""), m.group(3)
            outer = stack[-1] if stack else None
            key = (outer, name)
            if key not in objects:
                objects[key] = {"class": "", "outer": outer, "name": name, "body": []}
                order.append(key)
            if cls:
                objects[key]["class"] = cls
            stack.append(key)
            continue
        if line.strip() == "End Object":
            if stack:
                stack.pop()
            continue
        if stack:
            objects[stack[-1]]["body"].append(line)
    nodes, graphs, dead = [], {}, 0
    graph_nodes = {}      # graph key -> names its Nodes(i)= lines list
    bp_key, real_graphs = None, set()
    for key in order:
        o = objects[key]
        short = o["class"].split(".")[-1]
        btxt = "\n".join(o["body"])
        outer = objects.get(o["outer"]) if o["outer"] else None
        if not bp and "Blueprint" in short and outer is None:
            bp = {"class": short, "name": o["name"]}
            bp_key = key
            real_graphs = set(_REAL_GRAPH.findall(btxt))
            for line in _INTERFACE_LINE.findall(btxt):
                real_graphs.update(re.findall(r"EdGraph'([^']+)'", line))
            pc = re.search(r"^\s*ParentClass=(.*)$", btxt, re.M)
            if pc:
                pm = _PATHREF.search(pc.group(1))
                bp["parent_class"] = pm.group(1) if pm else pc.group(1).strip()
            bp["variables"] = re.findall(r'NewVariables\(\d+\)=\(VarName="([^"]+)"', btxt)
            bp["interfaces"] = [_PATHREF.search(x).group(1) for x in re.findall(r"ImplementedInterfaces\(\d+\)=\((.*)\)", btxt) if _PATHREF.search(x)]
        if "Node" not in short:
            continue
        graph = outer["name"] if outer else None
        live = True
        if outer is not None and "Graph" in outer["class"].split(".")[-1]:
            if o["outer"] not in graph_nodes:
                graph_nodes[o["outer"]] = set(_GRAPH_NODE.findall("\n".join(outer["body"])))
            live = o["name"] in graph_nodes[o["outer"]]
            if outer["outer"] == bp_key and real_graphs and outer["name"] not in real_graphs:
                live = False
        if not live:
            dead += 1
            if not include_dead:
                continue
        node = {"name": o["name"], "class": short, "graph": graph}
        if include_dead:
            node["live"] = live
        _parse_node_props(node, btxt)
        if keep_body:
            node["body"] = list(o["body"])
        nodes.append(node)
        if live:
            graphs[graph or "?"] = graphs.get(graph or "?", 0) + 1
    return {"blueprint": bp, "graphs": graphs, "nodes": nodes, "dead_nodes": dead}


def links_of(nodes):
    """Every link between the given parsed nodes as (node, pin, node, pin), each pair once."""
    pin_names = {(n["name"], p["id"]): p["name"] for n in nodes for p in n["pins"] if p.get("id")}
    out = set()
    for n in nodes:
        for p in n["pins"]:
            for target, target_id in p.get("linked_ids", ()):
                a = (n["name"], p["name"])
                b = (target, pin_names.get((target, target_id), "?" + target_id))
                out.add((a + b) if a <= b else (b + a))
    return out


def member_refs(node):
    """(class, member, kind) for what the audit checks on a node: function calls, events, variables and event
    dispatchers. Cast targets and class pins aren't checked. A self-context reference names no class and is skipped."""
    refs = []
    for key in ("function", "event", "variable", "delegate"):
        r = node.get(key)
        if r and r.get("parent") and "." in r["parent"]:     # a bound event's parent can be a package, not a class
            refs.append((r["parent"], r["name"], key))
    return refs


def classes_used(nodes):
    """Class paths the filter would look at for these nodes."""
    return sorted({parent for n in nodes for parent, _, _ in member_refs(n)})


def check_nodes(nodes, dumps):
    """Classify the references of parsed nodes against parse_filter_dumps output."""
    blocked, not_checked, seen = [], [], set()
    for n in nodes:
        for parent, member, kind in member_refs(n):
            dump = dumps.get(parent)
            if dump is None:
                if parent not in seen:
                    seen.add(parent)
                    not_checked.append(parent)
            elif member in dump["blocked"]:
                blocked.append({"node": n["name"], "graph": n.get("graph"), "kind": kind, "parent": parent, "member": member})
    return {"blocked": blocked, "not_checked": not_checked}
