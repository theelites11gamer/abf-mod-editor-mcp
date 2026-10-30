"""Blueprint clipboard text (T3D): checks on text about to be pasted, and a builder that turns a small node
list into clipboard text.

The builder never guesses pins. It pastes each node bare into a throwaway Blueprint, reads back the pins the
editor allocated, and clones those (server.py does the pasting). A wrong pin name is then an error here, with
the real names listed, not a link the editor silently drops. No editor access in this file.
"""
import hashlib
import json
import re
import uuid

import bpscan

BG = "/Script/BlueprintGraph."
COMMENT = "/Script/UnrealEd.EdGraphNode_Comment"
FOREACH = ('MacroGraphReference=(MacroGraph="/Script/Engine.EdGraph\'/Engine/EditorBlueprintResources/StandardMacros.StandardMacros:ForEachLoop\'",'
           'GraphBlueprint="/Script/Engine.Blueprint\'/Engine/EditorBlueprintResources/StandardMacros.StandardMacros\'",GraphGuid=99DBFD5540A796041F72A5A9DA655026)')
# FunctionFlags of a custom event: FUNC_Net + where it runs + (optional) FUNC_NetReliable
RPC_FLAGS = {"server": 0x40 | 0x200000, "client": 0x40 | 0x1000000, "multicast": 0x40 | 0x4000}
RELIABLE = 0x80

PIN_LINE = re.compile(r"^\s*CustomProperties Pin \((.*)\)\s*$")
SKIP_PROP = re.compile(r"^\s*(NodePosX|NodePosY|NodeGuid|bHasCompilerMessage|ErrorType|ErrorMsg)=")


class SpecError(ValueError):
    pass


def new_id():
    return uuid.uuid4().hex.upper()


def _cls(path):
    return "\"/Script/CoreUObject.Class'" + path + "'\""


def _struct(path):
    prefix = "/Script/Engine.UserDefinedStruct" if path.startswith("/Game/") else "/Script/CoreUObject.ScriptStruct"
    return '"' + prefix + "'" + path + "'\""


def _member(key, owner, name):
    if owner:
        return f'{key}=(MemberParent={_cls(owner)},MemberName="{name}")'
    return f'{key}=(MemberName="{name}",bSelfContext=True)'


def _need(n, *keys):
    for k in keys:
        if n.get(k) in (None, ""):
            raise SpecError(f"node {n.get('id')!r} (type {n.get('type')}) needs {k!r}")


def _user_pin(p):
    """A custom event parameter: {name, cat, obj?, array?}."""
    obj = p.get("obj")
    sub = ""
    if obj:
        kind = "/Script/CoreUObject.Class" if p.get("cat") in ("object", "class", "softobject", "softclass") else "/Script/CoreUObject.ScriptStruct"
        sub = f",PinSubCategoryObject=\"{kind}'{obj}'\""
    container = ",ContainerType=Array" if p.get("array") else ""
    return (f'CustomProperties UserDefinedPin (PinName="{p["name"]}",PinType=(PinCategory="{p["cat"]}"{sub}{container}),'
            'DesiredPinDirection=EGPD_Output)')


def describe(n):
    """(node class, property lines, starter pin lines) for one spec node."""
    t = n.get("type") or ("call" if n.get("function") else None)
    if t == "call":
        _need(n, "function")
        return BG + "K2Node_CallFunction", [_member("FunctionReference", n.get("owner"), n["function"])], []
    if t == "array_call":
        _need(n, "function")
        return BG + "K2Node_CallArrayFunction", [_member("FunctionReference", n.get("owner", "/Script/Engine.KismetArrayLibrary"), n["function"])], []
    if t == "call_parent":
        _need(n, "owner", "function")
        return BG + "K2Node_CallParentFunction", [_member("FunctionReference", n["owner"], n["function"])], []
    if t == "event":
        _need(n, "owner", "function")
        return BG + "K2Node_Event", [_member("EventReference", n["owner"], n["function"]), "bOverrideFunction=True"], []
    if t == "custom_event":
        _need(n, "name")
        props = [f'CustomFunctionName="{n["name"]}"']
        if n.get("rpc"):
            if n["rpc"] not in RPC_FLAGS:
                raise SpecError(f"node {n.get('id')!r}: rpc must be one of {sorted(RPC_FLAGS)}")
            props.append(f"FunctionFlags={RPC_FLAGS[n['rpc']] | (RELIABLE if n.get('reliable', True) else 0)}")
        props += [_user_pin(p) for p in n.get("params", ())]
        return BG + "K2Node_CustomEvent", props, []
    if t in ("var_get", "var_set"):
        _need(n, "name")
        kind = "K2Node_VariableGet" if t == "var_get" else "K2Node_VariableSet"
        return BG + kind, [_member("VariableReference", n.get("owner"), n["name"])], []
    if t == "cast":
        _need(n, "target")
        return BG + "K2Node_DynamicCast", ["TargetType=" + _cls(n["target"])], []
    if t == "branch":
        return BG + "K2Node_IfThenElse", [], []
    if t == "sequence":
        # The editor builds a sequence's outputs from the ones it's given, so a bare one would have none.
        return BG + "K2Node_ExecutionSequence", [], [
            f'CustomProperties Pin (PinId={new_id()},PinName="then_{k}",Direction="EGPD_Output",PinType.PinCategory="exec",)'
            for k in range(max(2, int(n.get("outputs", 2))))]
    if t == "self":
        return BG + "K2Node_Self", [], []
    if t == "make_array":
        return BG + "K2Node_MakeArray", [f"NumInputs={int(n.get('count', 1))}"], []
    if t in ("make_struct", "break_struct"):
        _need(n, "struct")
        kind = "K2Node_MakeStruct" if t == "make_struct" else "K2Node_BreakStruct"
        return BG + kind, ["StructType=" + _struct(n["struct"])], []
    if t == "foreach":
        _need(n, "element")
        return BG + "K2Node_MacroInstance", [FOREACH, "ResolvedWildcardType=" + _wild_type(n["element"])], []
    if t == "input_action":
        _need(n, "name")
        return BG + "K2Node_InputAction", [f'InputActionName="{n["name"]}"', f"bConsumeInput={bool(n.get('consume', False))}"], []
    if t in ("spawn_actor", "add_component"):
        _need(n, "class")
        kind, base = (("K2Node_SpawnActorFromClass", "/Script/Engine.Actor") if t == "spawn_actor"
                      else ("K2Node_AddComponentByClass", "/Script/Engine.ActorComponent"))
        # The class pin is what makes the editor allocate the exposed pins, so it has to be there from the start,
        # with the real pin's type: otherwise the value stays behind on an orphaned copy.
        pin = (f'CustomProperties Pin (PinId={new_id()},PinName="Class",PinType.PinCategory="class",'
               f"PinType.PinSubCategoryObject=\"/Script/CoreUObject.Class'{base}'\",DefaultObject=\"{n['class']}\",)")
        return BG + kind, [], [pin]
    if t == "create_delegate":
        _need(n, "function")
        return BG + "K2Node_CreateDelegate", [f'SelectedFunctionName="{n["function"]}"'], []
    if t == "comment":
        return COMMENT, [f'NodeComment={json.dumps(str(n.get("text", "")))}', f"NodeWidth={int(n.get('width', 400))}", f"NodeHeight={int(n.get('height', 100))}"], []
    if t == "raw":
        _need(n, "kind")
        kind = n["kind"] if "/" in n["kind"] else BG + n["kind"]
        props = n.get("props", [])
        return kind, [props] if isinstance(props, str) else list(props), list(n.get("pins", ()))
    raise SpecError(f"node {n.get('id')!r}: unknown type {t!r}. Types: call, array_call, call_parent, event, custom_event, var_get, var_set, "
                    "cast, branch, sequence, self, make_array, make_struct, break_struct, foreach, input_action, spawn_actor, add_component, "
                    "create_delegate, comment, raw.")


def _wild_type(el):
    sub = ""
    if el.get("obj"):
        kind = "/Script/CoreUObject.Class" if el.get("cat") in ("object", "class", "softobject", "softclass") else "/Script/CoreUObject.ScriptStruct"
        sub = f",PinSubCategoryObject=\"{kind}'{el['obj']}'\""
    return f'(PinCategory="{el["cat"]}"{sub})'


def template_key(n):
    kind, props, pins = describe(n)
    return hashlib.sha1("\n".join([kind] + props + [re.sub(r"PinId=[0-9A-F]+", "", p) for p in pins]).encode("utf-8")).hexdigest()


def is_stable(n):
    """Pins that depend only on the game or engine, so a template can be kept between runs."""
    if n.get("type") in ("branch", "sequence", "self", "make_array", "foreach", "input_action", "comment"):
        return True
    for key in ("owner", "target", "struct", "class"):
        v = n.get(key)
        if isinstance(v, str) and v.startswith(("/Script/", "/Game/")):
            return True
    return False


def bare_text(items):
    """Clipboard text with each (name, spec node) bare: what discovery pastes. The editor allocates the pins."""
    chunks = []
    for i, (name, n) in enumerate(items):
        kind, props, pins = describe(n)
        chunks.append("\n".join([f'Begin Object Class={kind} Name="{name}"'] + props +
                                [f"NodePosX={(i % 8) * 420}", f"NodePosY={(i // 8) * 360}", f"NodeGuid={new_id()}"] + pins + ["End Object"]))
    return "\n".join(chunks) + "\n"


def templates_from_export(export_text, names):
    """{name: template} for the probe nodes found in an export. A node the editor refused is simply absent."""
    found = {}
    wanted = set(names)
    for node in bpscan.parse_t3d(export_text, keep_body=True)["nodes"]:
        if node["name"] not in wanted:
            continue
        props, pins = [], []
        for line in node["body"]:
            m = PIN_LINE.match(line)
            if m:
                if "bOrphanedPin=True" not in m.group(1):    # the node no longer has this pin
                    pins.append(m.group(1))
            elif line.strip() and not SKIP_PROP.match(line):
                props.append(line.strip())
        found[node["name"]] = {"class": node["class"], "props": props, "pins": pins, "error": node.get("error")}
    return found


def pin_table(template):
    """The pins of a template, for people and for link checks."""
    out = []
    for raw in template["pins"]:
        p = bpscan.parse_pin(raw)
        e = {"name": p["name"], "dir": p["dir"], "type": p["type"]}
        for k in ("object", "array", "default", "hidden"):
            if p.get(k):
                e[k] = p[k]
        out.append(e)
    return out


# ---- assembling the final text ------------------------------------------------

def _strip_field(raw, field):
    """Remove one `Field=value,` from a pin's text. Values can hold quotes, commas and brackets."""
    m = re.search(r"(^|,)" + re.escape(field) + "=", raw)
    if not m:
        return raw
    i, depth, quoted = m.end(), 0, False
    while i < len(raw):
        c = raw[i]
        if quoted:
            if c == "\\":
                i += 1
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            break
        i += 1
    start = m.start() + (1 if m.group(1) else 0)
    return raw[:start] + raw[i + 1:]


def _set_field(raw, field, value):
    """Set or remove one `Field=value,` in a pin's text. value None removes it."""
    raw = _strip_field(raw, field)
    if value is None:
        return raw
    if ",bHidden=" in raw:
        return raw.replace(",bHidden=", f",{field}={value},bHidden=", 1)
    return raw.rstrip(",") + f",{field}={value},"


def _default_fields(pin_type, value):
    """(field, text) for a literal on a pin of this category."""
    if pin_type in ("object", "class", "softobject", "softclass", "interface", "asset", "assetclass"):
        return "DefaultObject", json.dumps(str(value))
    if pin_type == "text":
        key = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:32].upper()
        return "DefaultTextValue", 'NSLOCTEXT("[ABFMCP]", "' + key + '", ' + json.dumps(str(value), ensure_ascii=False) + ")"
    if isinstance(value, bool):
        return "DefaultValue", '"true"' if value else '"false"'
    return "DefaultValue", json.dumps(str(value), ensure_ascii=False)


def _retype(raw, element):
    """Give a wildcard pin the concrete type the node's `element` names. Other pins keep theirs."""
    if 'PinType.PinCategory="wildcard"' not in raw:
        return raw
    raw = raw.replace('PinType.PinCategory="wildcard"', f'PinType.PinCategory="{element["cat"]}"')
    if element.get("obj"):
        kind = "/Script/CoreUObject.Class" if element["cat"] in ("object", "class", "softobject", "softclass") else "/Script/CoreUObject.ScriptStruct"
        raw = re.sub(r"PinType\.PinSubCategoryObject=[^,]*", f"PinType.PinSubCategoryObject=\"{kind}'{element['obj']}'\"", raw, count=1)
    return raw


def _close_names(name, names):
    low = name.lower()
    near = [p for p in names if low in p.lower() or p.lower() in low]
    return near or names


def _layout(ids, edges):
    """Columns by longest path from the sources, so a chain reads left to right. Cycles just stop growing."""
    rank = {i: 0 for i in ids}
    for _ in range(len(ids)):
        moved = False
        for a, b in edges:
            if a != b and rank[b] < rank[a] + 1 and rank[a] < len(ids):
                rank[b] = rank[a] + 1
                moved = True
        if not moved:
            break
    rows = {}
    pos = {}
    for i in ids:
        row = rows.get(rank[i], 0)
        rows[rank[i]] = row + 1
        pos[i] = (rank[i] * 360, row * 240)
    return pos


def assemble(nodes, links, templates, prefix):
    """Final clipboard text. nodes: spec nodes with 'id'. links: ['a.then', 'b.execute'] pairs. templates: {id: template}.

    Returns {'text', 'names', 'problems', 'warnings', 'links'}. With problems, 'text' is None: nothing should be pasted.
    """
    problems, warnings = [], []
    by_id = {}
    for n in nodes:
        if not n.get("id"):
            problems.append(f"a node has no id: {json.dumps(n)[:120]}")
        elif n["id"] in by_id:
            problems.append(f"duplicate node id {n['id']!r}")
        else:
            by_id[n["id"]] = n
    built = {}
    for i, n in enumerate(nodes):
        nid = n.get("id")
        if nid not in by_id or by_id[nid] is not n:
            continue
        tpl = templates.get(nid)
        if tpl is None:
            problems.append(f"{nid}: the editor refused this node when probed (does {n.get('function') or n.get('name') or n.get('kind')!r} exist on that class?)")
            continue
        if tpl.get("error"):
            problems.append(f"{nid}: the editor reports: {tpl['error']}")
        pins = {}
        order = []
        for raw in tpl["pins"]:
            if n.get("element"):
                raw = _retype(raw, n["element"])
            p = bpscan.parse_pin(raw)
            pins[p["name"]] = {"raw": raw, "id": new_id(), "dir": p["dir"], "type": p["type"], "links": [], "default": None}
            order.append(p["name"])
        for key, value in (n.get("args") or {}).items():
            if key not in pins:
                problems.append(f"{nid}: no pin {key!r} for a default. Pins: {_close_names(key, order)}")
                continue
            pins[key]["default"] = _default_fields(pins[key]["type"], value)
        built[nid] = {"name": f"{prefix}_{i}", "tpl": tpl, "pins": pins, "order": order, "spec": n}

    edges, link_count = [], 0
    for link in links:
        if not (isinstance(link, (list, tuple)) and len(link) == 2):
            problems.append(f"a link must be ['node.pin', 'node.pin']: {link!r}")
            continue
        ends = []
        for end in link:
            nid, _, pin = str(end).partition(".")
            if nid not in by_id:
                problems.append(f"link {link}: no node with id {nid!r}")
            elif nid not in built:
                pass                              # already reported above
            elif pin not in built[nid]["pins"]:
                problems.append(f"link {link}: {nid} has no pin {pin!r}. Pins: {_close_names(pin, built[nid]['order'])}")
            else:
                ends.append((nid, pin))
        if len(ends) != 2:
            continue
        (an, ap), (bn, bp) = ends
        a, b = built[an]["pins"][ap], built[bn]["pins"][bp]
        if a["dir"] == b["dir"]:
            problems.append(f"link {link}: both pins are {'outputs' if a['dir'] == 'out' else 'inputs'}")
            continue
        if a["dir"] == "in":                      # write links output first, whichever way the spec has them
            an, ap, a, bn, bp, b = bn, bp, b, an, ap, a
        if (a["type"] == "exec") != (b["type"] == "exec"):
            problems.append(f"link {link}: an exec pin can't connect to a {b['type'] if a['type'] == 'exec' else a['type']} pin")
            continue
        if a["type"] != b["type"] and "wildcard" not in (a["type"], b["type"]):
            warnings.append(f"link {link}: {a['type']} to {b['type']} may need a conversion node; the editor can drop this link")
        a["links"].append((built[bn]["name"], b["id"]))
        b["links"].append((built[an]["name"], a["id"]))
        edges.append((an, bn))
        link_count += 1

    for n in nodes:
        pins = built.get(n.get("id"), {}).get("pins", {})
        if n.get("type") in ("var_get", "var_set") and n.get("owner") and "self" in pins and not pins["self"]["links"]:
            warnings.append(f"{n['id']}: a variable with an owner needs a target linked to its self pin. Leave the owner out for this Blueprint's own or inherited variables.")

    if problems:
        return {"text": None, "names": {}, "problems": problems, "warnings": warnings, "links": link_count}

    auto = _layout([n["id"] for n in nodes], edges)
    chunks = []
    for n in nodes:
        b = built[n["id"]]
        x, y = n.get("x", auto[n["id"]][0]), n.get("y", auto[n["id"]][1])
        props = list(b["tpl"]["props"])
        have = {p.split("=", 1)[0] for p in props}
        # A bare node with nothing linked can forget what it was given (a macro's ResolvedWildcardType), so the spec's come back.
        props += [p for p in describe(n)[1] if p.split("=", 1)[0] not in have and not p.startswith("CustomProperties")]
        lines = [f'Begin Object Class={class_path(b["tpl"]["class"], n)} Name="{b["name"]}"'] + props
        lines += [f"NodePosX={int(x)}", f"NodePosY={int(y)}", f"NodeGuid={new_id()}"]
        for name in b["order"]:
            pin = b["pins"][name]
            raw = re.sub(r"PinId=[0-9A-Fa-f]+", "PinId=" + pin["id"], pin["raw"], count=1)
            raw = _set_field(raw, "LinkedTo", "(" + "".join(f"{t} {i}," for t, i in pin["links"]) + ")" if pin["links"] else None)
            if pin["default"]:
                for field in ("DefaultValue", "DefaultObject", "DefaultTextValue"):
                    raw = _set_field(raw, field, None)
                raw = _set_field(raw, pin["default"][0], pin["default"][1])
            lines.append(f"CustomProperties Pin ({raw})")
        lines.append("End Object")
        chunks.append("\n".join(lines))
    return {"text": "\n".join(chunks) + "\n", "names": {n["id"]: built[n["id"]]["name"] for n in nodes},
            "problems": [], "warnings": warnings, "links": link_count}


def class_path(short, n):
    """Full class path of a node class, from its short name in a template."""
    if "/" in short:
        return short
    kind = describe(n)[0]
    if kind.endswith("." + short):
        return kind
    return ("/Script/UnrealEd." if short.startswith("EdGraphNode") else BG) + short


# ---- checks on text about to be pasted ---------------------------------------

def lint(text):
    """{'errors': [...], 'warnings': [...]} for clipboard text, before it goes near the editor."""
    errors, warnings = [], []
    parsed = bpscan.parse_t3d(text)
    nodes = parsed["nodes"]
    if not nodes:
        errors.append("No Blueprint nodes in this text (expected 'Begin Object Class=...K2Node... Name=...' blocks).")
        return {"errors": errors, "warnings": warnings, "nodes": 0}
    names = [n["name"] for n in nodes]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        errors.append(f"Duplicate node names: {dup}. The editor renames them and their links are lost.")
    ids = {(n["name"], p["id"]) for n in nodes for p in n["pins"]}
    known = set(names)
    for n in nodes:
        seen = set()
        for p in n["pins"]:
            key = (p["name"], p["dir"])
            if key in seen:
                errors.append(f"{n['name']}: pin {p['name']!r} appears twice. The extra one becomes a phantom pin, a compile error only the log shows.")
            seen.add(key)
            for target, target_id in p.get("linked_ids", ()):
                if target not in known:
                    warnings.append(f"{n['name']}.{p['name']} links to {target!r}, which isn't in this paste: that link will be dropped.")
                elif (target, target_id) not in ids:
                    errors.append(f"{n['name']}.{p['name']} links to pin {target_id} on {target}, which has no such pin.")
    return {"errors": errors, "warnings": warnings, "nodes": len(nodes)}


def comment_block(x=0, y=0):
    """A small comment node with a new name: it has no pins, so it's safe at the end of any paste."""
    return "\n".join([f'Begin Object Class={COMMENT} Name="McpPasteEnd_{uuid.uuid4().hex[:8]}"',
                      'NodeComment="Pasted by abf-mod-editor-mcp"', "NodeWidth=240", "NodeHeight=60",
                      f"NodePosX={x}", f"NodePosY={y}", f"NodeGuid={new_id()}", "End Object"]) + "\n"


def ensure_end_comment(text):
    """The editor drops every link of the LAST node in a paste, so make sure that node is a comment, which has none.
    Returns (text, added). It sits at the middle of the other nodes, so it doesn't move where the paste lands."""
    blocks = re.findall(r'^\s*Begin Object Class=(\S+) Name="[^"]+"', text, re.M)
    if not blocks or blocks[-1].endswith("EdGraphNode_Comment"):
        return text, False
    xs = [int(v) for v in re.findall(r"^\s*NodePosX=(-?\d+)", text, re.M)] or [0]
    ys = [int(v) for v in re.findall(r"^\s*NodePosY=(-?\d+)", text, re.M)] or [0]
    return text.rstrip("\n") + "\n" + comment_block(sum(xs) // len(xs), sum(ys) // len(ys)), True


def verify(sent_text, export_text):
    """Compare what was pasted with what the Blueprint now holds. Only makes sense when the node names are new."""
    sent = bpscan.parse_t3d(sent_text)["nodes"]
    now = bpscan.parse_t3d(export_text)
    found = {n["name"]: n for n in now["nodes"]}
    names = [n["name"] for n in sent]
    missing_nodes = [n for n in names if n not in found]
    landed = {}
    changed = []
    for n in sent:
        got = found.get(n["name"])
        if not got:
            continue
        landed[got["graph"]] = landed.get(got["graph"], 0) + 1
        if got["class"] != n["class"].split(".")[-1]:
            changed.append({"node": n["name"], "sent": n["class"].split(".")[-1], "now": got["class"]})
    sent_links = bpscan.links_of(sent)
    kept = bpscan.links_of([found[n] for n in names if n in found])
    missing_links = [{"from": f"{a}.{ap}", "to": f"{b}.{bp}"} for a, ap, b, bp in sorted(sent_links - kept)
                     if a in found and b in found]
    problems = [{"node": n, "error": found[n].get("error"), "orphaned_pins": found[n].get("orphaned_pins")}
                for n in names if n in found and (found[n].get("error") or found[n].get("orphaned_pins"))]
    return {"nodes_sent": len(names), "nodes_found": len(names) - len(missing_nodes), "missing_nodes": missing_nodes,
            "links_sent": len(sent_links), "missing_links": missing_links, "changed_class": changed,
            "problems": problems, "landed_in": landed,
            "ok": not (missing_nodes or missing_links or changed)}
