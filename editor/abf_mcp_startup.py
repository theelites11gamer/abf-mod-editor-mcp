"""Runs inside the Abiotic Factor Mod Editor at every launch. install.py copies this file next
to the init_unreal.py loader in Documents\\UnrealEngine\\Python. It does two things for the
abf-mod-editor-mcp server:

1. Turns on Python remote execution for this editor session (nothing is written to config).
2. Starts a file bridge in %LOCALAPPDATA%\\abf-mod-editor-mcp\\bridge as a fallback transport:
   the server writes request.json, this tick callback runs it and writes response.json.
   Used when remote execution is unavailable (a modal dialog, or another client holding
   its command connection).
"""
import unreal, json, time, traceback, io, contextlib, os
from pathlib import Path

BRIDGE = Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")) / "abf-mod-editor-mcp" / "bridge"


def _enable_remote_execution():
    try:
        # The settings class isn't exposed to Python by name in this build; its CDO is.
        # set_editor_property fires PostEditChange, which starts remote execution (no config write).
        settings = unreal.find_object(None, '/Script/PythonScriptPlugin.Default__PythonScriptPluginSettings')
        if not settings.get_editor_property('bRemoteExecution'):
            settings.set_editor_property('bRemoteExecution', True)
        unreal.log('ABF_MCP: remote execution on')
    except Exception as e:
        unreal.log_warning('ABF_MCP: could not enable remote execution: %s' % e)


def _run(data):
    out = io.StringIO()
    scope = globals().setdefault('_abf_mcp_scope', {'__name__': '__abf_mcp__', 'unreal': unreal})
    scope['ARGS'] = data.get('args', {})
    code = data.get('code')
    filename = '<mcp>'
    if code is None:
        filename = data['script']
        code = Path(filename).read_text(encoding='utf-8-sig')
    result = None
    with contextlib.redirect_stdout(out):
        if data.get('mode') == 'eval':
            result = repr(eval(compile(code, filename, 'eval'), scope))
        else:
            exec(compile(code, filename, 'exec'), scope)
    return {'ok': True, 'output': out.getvalue(), 'result': result}, out


def _tick(delta):
    now = time.time()
    if now - globals().get('_abf_mcp_beat', 0) > 2:
        globals()['_abf_mcp_beat'] = now
        try:
            (BRIDGE / 'heartbeat.json').write_text(json.dumps({'time': now, 'pid': os.getpid()}))
        except Exception:
            pass
    request = BRIDGE / 'request.json'
    if not request.exists():
        return
    try:
        data = json.loads(request.read_text(encoding='utf-8-sig'))
        request.unlink()
    except Exception:
        return
    out = None
    try:
        result, out = _run(data)
    except Exception:
        result = {'ok': False, 'output': out.getvalue() if out else '', 'error': traceback.format_exc()}
    result['id'] = data.get('id')
    tmp = BRIDGE / 'response.tmp'
    tmp.write_text(json.dumps(result), encoding='utf-8')
    os.replace(tmp, BRIDGE / 'response.json')


def start():
    _enable_remote_execution()
    BRIDGE.mkdir(parents=True, exist_ok=True)
    # A request left from an earlier session (one that quit the editor, say) must not run again.
    (BRIDGE / 'request.json').unlink(missing_ok=True)
    old = globals().get('_abf_mcp_handle')
    if old is not None:
        unreal.unregister_slate_post_tick_callback(old)
    globals()['_abf_mcp_handle'] = unreal.register_slate_post_tick_callback(_tick)
    unreal.log('ABF_MCP: bridge ready at %s' % BRIDGE)
