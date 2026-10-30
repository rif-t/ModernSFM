# -*- coding: utf-8 -*-
"""Open or reload ModernSFM from SFM's Scripts menu."""
import imp
import json
import os
import sys


def _config_path():
    configured = os.environ.get("MODERNSFM_CONFIG")
    if configured:
        return configured
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "ModernSFM", "config.json")
    return None


path = _config_path()
if not path or not os.path.isfile(path):
    raise RuntimeError("ModernSFM is not installed for this SFM user. Run the repository's setup.ps1 first.")

with open(path, "rb") as stream:
    config = json.load(stream)
project_root = config.get("project_root")
sfm_game = config.get("sfm_game")
if not project_root or not sfm_game:
    raise RuntimeError("ModernSFM config must define project_root and sfm_game: %s" % path)

scripts_dir = os.path.join(project_root, "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

module = sys.modules.get("sfm_render_job_panel")
if module is None:
    import sfm_render_job_panel as module
else:
    old_panel = getattr(module, "_panel_ref", None)
    if old_panel is not None:
        for timer_name in ("_timer", "_session_poll"):
            timer = getattr(old_panel, timer_name, None)
            if timer is not None:
                timer.stop()
        old_panel.close()
    module = imp.reload(module)

module.show_render_job_panel()
