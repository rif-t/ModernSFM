# -*- coding: utf-8 -*-
"""Open the ModernSFM Render Job panel from SFM's Scripts menu."""
import os
import sys

PROJECT_ROOT = os.environ.get(
    "MODERNSFM_ROOT",
    r"D:\WorkFlow\Programming\Projects\ModernSFM",
)
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import sfm_render_job_panel
sfm_render_job_panel.show_render_job_panel()
