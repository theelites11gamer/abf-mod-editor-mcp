"""Offline Blueprint helpers: parse T3D exports and check references against the
Blueprint filter the upload Asset Audit and the mod.io cloud cook enforce.

Filter format (cdn.abfmods.com/blueprint-filter/1.5/BlueprintFilter.txt):
    Allow|Block /Script/Module
    Allow|Block /Script/Module.Class          (or /Game/Path/Asset.Asset_C)
    Allow|Block /Script/Module.Class Member   (function or variable)
The most specific rule wins. Modules not listed at all are reported as "unlisted";
/Game assets with no rule are treated as allowed, since the filter lists only exceptions.
"""
import re
import time
import urllib.request
from pathlib import Path

FILTER_URL = "https://cdn.abfmods.com/blueprint-filter/1.5/BlueprintFilter.txt"
HERE = Path(__file__).resolve().parent
FILTER_FILE = HERE / "data" / "BlueprintFilter.txt"


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
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("Allow", "Block"):
            key = parts[1] + (" " + parts[2] if len(parts) > 2 else "")
            rules[key] = parts[0]
    meta["rule_count"] = len(rules)
    return rules, meta


def resolve(rules, parent, member=None):
    """(verdict, matched_rule) for a reference to `member` on class/asset path `parent`."""
    if member and f"{parent} {member}" in rules:
        return rules[f"{parent} {member}"], f"{parent} {member}"
    if parent in rules:
        return rules[parent], parent
    module = parent.split(".")[0]
    if module in rules:
        return rules[module], module
    if parent.startswith("/Game/") or parent.startswith("/Engine/"):
        return "Allow", None                          # the filter lists only exceptions for content
    return "Unlisted", None


# ---- T3D parsing ------------------------------------------------------------
# A Blueprint export lists each object twice under its graph: first a bare declaration
# (`Begin Object Class=... Name=...` / `End Object`), then a definition (`Begin Object Name=...`)
# holding the properties. Both are merged by name here.

_BEGIN = re.compile(r'^(\s*)Begin Object(?: Class=(\S+))? Name="([^"]+)"')
_PATHREF = re.compile(r"""(?:Class|BlueprintGeneratedClass|WidgetBlueprintGeneratedClass|AnimBlueprintGeneratedClass|Function|ScriptStruct|Enum|UserDefinedEnum|UserDefinedStruct|Object|Package)'"?(/[^'"]+)'?"?""")


def _member_ref(body, kind):
    m = re.search(kind + r'=\((?:MemberParent="?([^",)]*)"?,)?(?:MemberScope="[^"]*",)?MemberName="([^"]+)"', body)
    if not m:
        return None
    parent = m.group(1) or ""
    pm = _PATHREF.search(parent) if parent else None
    return {"parent": pm.group(1) if pm else (parent or None), "name": m.group(2)}


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
        node["error"] = err.group(1)[:300]
    orphans = re.findall(r'PinName="([^"]+)"[^\n]*bOrphanedPin=True', btxt)
    if orphans:
        node["orphaned_pins"] = orphans
    pins = []
    for pin in re.findall(r"^\s*CustomProperties Pin \((.*)\)\s*$", btxt, re.M):
        name = re.search(r'PinName="([^"]*)"', pin)
        cat = re.search(r'PinType\.PinCategory="([^"]*)"', pin)
        obj = re.search(r"PinType\.PinSubCategoryObject=([^,]*)", pin)
        linked = re.findall(r"LinkedTo=\(([^)]*)\)", pin)
        entry = {"name": name.group(1) if name else "?", "type": cat.group(1) if cat else "?",
                 "dir": "out" if 'Direction="EGPD_Output"' in pin else "in"}
        if obj and obj.group(1) not in ("None", ""):
            pm = _PATHREF.search(obj.group(1))
            entry["object"] = pm.group(1) if pm else obj.group(1).strip('"')
        if linked and linked[0]:
            entry["linked"] = [l.split(" ")[0] for l in linked[0].split(",") if l]
        default = re.search(r'DefaultValue="([^"]*)"', pin)
        if default and default.group(1):
            entry["default"] = default.group(1)[:80]
        pins.append(entry)
    node["pins"] = pins
    node["object_refs"] = sorted({r for r in _PATHREF.findall(btxt) if r.startswith(("/Script/", "/Game/", "/Engine/"))})


def parse_t3d(text):
    """Return {'blueprint': {...}, 'graphs': {graph: node_count}, 'nodes': [...]}."""
    lines = text.splitlines()
    objects = {}          # name -> {'class':..., 'graph':..., 'body': [lines]}
    order = []
    stack = []            # names of enclosing objects
    bp = {}
    for line in lines:
        m = _BEGIN.match(line)
        if m:
            cls, name = (m.group(2) or ""), m.group(3)
            graph = stack[-1] if stack else None
            if name not in objects:
                objects[name] = {"class": "", "graph": graph, "body": []}
                order.append(name)
            if cls:
                objects[name]["class"] = cls
            stack.append(name)
            continue
        if line.strip() == "End Object":
            if stack:
                stack.pop()
            continue
        if stack:
            objects[stack[-1]]["body"].append(line)
    nodes, graphs = [], {}
    for name in order:
        o = objects[name]
        short = o["class"].split(".")[-1]
        btxt = "\n".join(o["body"])
        if not stack and not bp and "Blueprint" in short and o["graph"] is None:
            bp = {"class": short, "name": name}
            pc = re.search(r"^\s*ParentClass=(.*)$", btxt, re.M)
            if pc:
                pm = _PATHREF.search(pc.group(1))
                bp["parent_class"] = pm.group(1) if pm else pc.group(1).strip()
            bp["variables"] = re.findall(r'NewVariables\(\d+\)=\(VarName="([^"]+)"', btxt)
            bp["interfaces"] = [_PATHREF.search(x).group(1) for x in re.findall(r"ImplementedInterfaces\(\d+\)=\((.*)\)", btxt) if _PATHREF.search(x)]
            bp["components"] = re.findall(r'Begin Object Class=\S+ Name="([^"]+)"', btxt)
        if "Node" in short:
            node = {"name": name, "class": short, "graph": o["graph"]}
            _parse_node_props(node, btxt)
            nodes.append(node)
            graphs[o["graph"] or "?"] = graphs.get(o["graph"] or "?", 0) + 1
    return {"blueprint": bp, "graphs": graphs, "nodes": nodes}


def check_nodes(nodes, rules):
    """Classify every function/variable/object reference in parsed nodes. Returns dict with blocked/unlisted lists."""
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
            verdict, rule = resolve(rules, parent, member)
            key = (parent, member)
            if verdict == "Block":
                blocked.append({"node": n["name"], "graph": n.get("graph"), "kind": kind, "parent": parent, "member": member, "rule": rule})
            elif verdict == "Unlisted" and key not in seen:
                seen.add(key)
                unlisted.append({"parent": parent, "member": member, "first_node": n["name"]})
    return {"blocked": blocked, "unlisted": unlisted}
