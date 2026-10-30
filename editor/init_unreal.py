# >>> abf-mod-editor-mcp >>>
# Loader for the abf-mod-editor-mcp server. install.py put this block in
# Documents\UnrealEngine\Python\init_unreal.py, which every Unreal editor runs at startup.
# It does nothing outside the Abiotic Factor Mod Editor (engine build tag +++DF+ABF).
# Remove it with:  python install.py --uninstall
import unreal as _abf_unreal
if "+++DF+ABF" in _abf_unreal.SystemLibrary.get_engine_version():
    try:
        import importlib.util as _abf_util, os as _abf_os
        try:
            _abf_dir = _abf_os.path.dirname(_abf_os.path.abspath(__file__))
        except NameError:                      # no __file__: fall back to the Documents folder
            import ctypes as _abf_ct
            _abf_buf = _abf_ct.create_unicode_buffer(1024)
            _abf_ct.windll.shell32.SHGetFolderPathW(None, 5, None, 0, _abf_buf)
            _abf_dir = _abf_os.path.join(_abf_buf.value, "UnrealEngine", "Python")
        _abf_spec = _abf_util.spec_from_file_location("abf_mcp_startup", _abf_os.path.join(_abf_dir, "abf_mcp_startup.py"))
        _abf_mod = _abf_util.module_from_spec(_abf_spec)
        _abf_spec.loader.exec_module(_abf_mod)
        _abf_mod.start()
    except Exception as _abf_err:
        _abf_unreal.log_warning("ABF_MCP: startup failed: %s" % _abf_err)
# <<< abf-mod-editor-mcp <<<
