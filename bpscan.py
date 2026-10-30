"""Offline Blueprint helpers: parse T3D exports and check references against the
Blueprint filter the upload Asset Audit and the mod.io cloud cook enforce.

Filter format (cdn.abfmods.com/blueprint-filter/1.5/BlueprintFilter.txt):
    Allow|Block /Script/Module
    Allow|Block /Script/Module.Class          (or /Game/Path/Asset.Asset_C)
    Allow|Block /Script/Module.Class Member   (function or variable)
Rules inherit down the class tree (see resolve). Modules not listed at all are reported as "unlisted".
"""
import re
import time
import urllib.request
from pathlib import Path

FILTER_URL = "https://cdn.abfmods.com/blueprint-filter/1.5/BlueprintFilter.txt"
HERE = Path(__file__).resolve().parent
FILTER_FILE = HERE / "data" / "BlueprintFilter.txt"
OBJECT = "/Script/CoreUObject.Object"


# ---- filter -----------------------------------------------------------------

def load_filter(max_age_hours=24):
    """Return (rules, meta). Downloads a fresh copy when the cached one is older than max_age_hours."""
    meta = {"source": str(FILTER_FILE)}
    stale = not FILTER_FILE.is_file() or time.time() - FILTER_FILE.stat().st_mtime > max_age_hours * 3600
    if stale:
        try:
            with urllib.request.urlopen(FILTER_URL, timeout=15) as r:
                data = r.read()
            if data.startswith(b"# ABF-BlueprintFilter"):
                FILTER_FILE.parent.mkdir(exist_ok=True)
                FILTER_FILE.write_bytes(data)
                meta["downloaded"] = True
        except Exception as e:                       # offline: fall back to the cached copy
            meta["download_error"] = str(e)
    if not FILTER_FILE.is_file():
        raise RuntimeError("No Blueprint filter available (download failed and no cached copy).")
    rules = {}
    for line in FILTER_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# ContentVersion"):
            meta["content_version"] = line.split()[-1]
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0] in ("Allow", "Block"):
            key = parts[1] + (" " + parts[2].strip() if len(parts) > 2 else "")
            rules[key] = parts[0]
    meta["rule_count"] = len(rules)
    return rules, meta


def resolve(rules, parent, member=None, ancestors=()):
    """(verdict, matched_rule) for a reference to `member` on class/asset path `parent`.

    The filter is written for inheritance: it blocks /Script/Engine, allows all of Actor and ActorComponent,
    and lists exceptions by name (Allow GameCommandSubsystem exists because Block Subsystem would catch it).
    So the class and its ancestors are tried first, nearest first, member rule before class rule; then their
    modules, nearest first. Object is left out of the first pass: everything derives from it, so its Block
    would otherwise beat every module Allow. `ancestors` is the class's parents, nearest first, from the editor;
    without them only the class's own rules and module apply.
    """
    chain = [parent] + [a for a in ancestors if a != parent]
    for cls in chain:
        if cls == OBJECT and cls != parent:
            continue
        if member and f"{cls} {member}" in rules:
            return rules[f"{cls} {member}"], f"{cls} {member}"
        if cls in rules:
            return rules[cls], cls
    for cls in chain:
        module = cls.split(".")[0]
        if module in rules:
            return rules[module], module
    if parent.startswith("/Game/") or parent.startswith("/Engine/"):
        return "Allow", None                          # no parents known: the filter lists only exceptions for content
    return "Unlisted", None


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


def classes_used(nodes):
    """Class paths the filter would look at for these nodes: what check_nodes needs ancestors for."""
    used = set()
    for n in nodes:
        for key in ("function", "variable", "delegate"):
            r = n.get(key)
            if r and r.get("parent"):
                used.add(r["parent"])
        for key in ("targetType", "proxyClass", "proxyFactoryClass"):
            v = n.get(key)
            if v:
                pm = _PATHREF.search(v)
                used.add(pm.group(1) if pm else v)
    return sorted(used)


def check_nodes(nodes, rules, ancestors=None):
    """Classify every function/variable/object reference in parsed nodes. Returns dict with blocked/unlisted lists.
    ancestors: {class path: [parents, nearest first]} from the editor."""
    ancestors = ancestors or {}
    blocked, unlisted, seen = [], [], set()
    for n in nodes:
        refs = []
        for key in ("function", "variable", "delegate"):
            r = n.get(key)
            if r and r.get("parent"):
                refs.append((r["parent"], r["name"], key))
        # Classes the node USES (cast target, spawn/construct class, async proxy), not pin types:
        # the audit checks calls and variables, and parameter/struct types never trip it.
        for key in ("targetType", "proxyClass", "proxyFactoryClass"):
            v = n.get(key)
            if v:
                pm = _PATHREF.search(v)
                refs.append((pm.group(1) if pm else v, None, key))
        for pin in n.get("pins", []):
            if pin.get("type") == "class" and pin.get("default", "").startswith("/Script/"):
                refs.append((pin["default"].split("'")[-2] if "'" in pin["default"] else pin["default"], None, "class pin " + pin["name"]))
        for parent, member, kind in refs:
            verdict, rule = resolve(rules, parent, member, ancestors.get(parent, ()))
            key = (parent, member)
            if verdict == "Block":
                blocked.append({"node": n["name"], "graph": n.get("graph"), "kind": kind, "parent": parent, "member": member, "rule": rule})
            elif verdict == "Unlisted" and key not in seen:
                seen.add(key)
                unlisted.append({"parent": parent, "member": member, "first_node": n["name"]})
    return {"blocked": blocked, "unlisted": unlisted}
