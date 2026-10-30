# -*- coding: utf-8 -*-
# First Render Job panel for Source Filmmaker's embedded Python 2.7.
# Run: sfm.exe -game usermod -sfm_startup_script <this file> -console -condebug
#
# This is an initial panel over the experimentally verified Sequence Exporter
# path. UI settings remain separate from Worker settings. The first version is
# deliberately capped at three frames and 1280x720; completion is gated by the
# native progress window closing, stable PFM pairs, header checks, and Worker
# PACK validation. Cancellation/failure classification remains best-effort.

import ctypes
import hashlib
import json
import os
import shutil
import struct
import sys
import time

from PySide import QtCore, QtGui
try:
    import shiboken
except ImportError:
    from PySide import shiboken
import sfmApp

ROOT = os.environ.get("MODERNSFM_ROOT",
                      r"D:\WorkFlow\Programming\Projects\ModernSFM")
SCRIPT_DIR = os.path.join(ROOT, "scripts")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from render_job_state import JobStateMachine, recover_interrupted_jobs, write_manifest
from render_job_processing import ExrProcessingThread


SFM_GAME = r"D:\SteamLibrary\steamapps\common\SourceFilmmaker\game"
SESSIONS_DIR = os.path.join(SFM_GAME, "usermod", "elements", "sessions")
BRIDGE_DLL = os.path.join(ROOT, "build", "x86", "Release", "modern_sfm_bridge.dll")
WORKER_EXE = os.path.join(ROOT, "build", "x64", "Release", "modern_sfm_worker.exe")
LOG = os.path.join(ROOT, "build", "render_job_panel.txt")
MAX_FRAMES = 3
MAX_RENDER_SECONDS = 300
# No exporter progress-window class has been confirmed in a live SFM render.
# Keep this empty until a specific class and its Cancel behavior are observed.
VERIFIED_NATIVE_CANCEL_CLASSES = ()
SUPPORTED_WIDTH = 1280
SUPPORTED_HEIGHT = 720

_panel_ref = None
_readiness_signature = None


def _log(message):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    print("ModernSFM Render Job: " + line)
    try:
        parent = os.path.dirname(LOG)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        paths = [LOG]
        try:
            job_log = getattr(_panel_ref, "_job_log_path", None)
            if job_log and job_log not in paths:
                paths.append(job_log)
        except Exception:
            pass
        for path in paths:
            parent = os.path.dirname(path)
            if not os.path.isdir(parent):
                os.makedirs(parent)
            with open(path, "a") as stream:
                stream.write(line + "\n")
    except Exception:
        pass


def _symbol(library, name):
    try:
        return getattr(library, name)
    except AttributeError:
        return getattr(library, "_" + name)


def _utf8_path(path):
    if isinstance(path, unicode):
        return path.encode("utf-8")
    return path.decode("mbcs").encode("utf-8")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _load_bridge():
    bridge = ctypes.CDLL(BRIDGE_DLL)
    bridge.start = _symbol(bridge, "msfm_start")
    bridge.start.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong]
    bridge.start.restype = ctypes.c_int
    bridge.request = _symbol(bridge, "msfm_request")
    bridge.request.argtypes = [ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p,
                               ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
    bridge.request.restype = ctypes.c_int
    bridge.shutdown = _symbol(bridge, "msfm_shutdown")
    bridge.shutdown.argtypes = [ctypes.c_ulong]
    bridge.shutdown.restype = ctypes.c_int
    bridge.last_error = _symbol(bridge, "msfm_last_error")
    bridge.last_error.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    bridge.last_error.restype = ctypes.c_int
    result = bridge.start(os.path.abspath(WORKER_EXE), 10000)
    if result != 0:
        raise RuntimeError("Bridge start failed (%d): %s" % (result, _last_error(bridge)))
    return bridge


def _last_error(bridge):
    buffer = ctypes.create_string_buffer(1024)
    result = bridge.last_error(buffer, len(buffer))
    return buffer.value if result == 0 else "Bridge error text unavailable (%d)" % result


def _request(bridge, payload):
    request = ctypes.create_string_buffer(payload)
    response = ctypes.create_string_buffer(64 * 1024)
    size = ctypes.c_ulong(0)
    result = bridge.request(request, len(payload), response, len(response),
                            ctypes.byref(size), 300000)
    if result != 0:
        raise RuntimeError("Bridge request failed (%d): %s" % (result, _last_error(bridge)))
    return response.raw[:size.value]


def _pack(bridge, beauty, depth, output, memory_mib):
    beauty_b, depth_b, output_b = map(_utf8_path, (beauty, depth, output))
    payload = struct.pack("<4sIIIQ", "PACK", len(beauty_b), len(depth_b),
                          len(output_b), memory_mib)
    response = _request(bridge, payload + beauty_b + depth_b + output_b)
    if len(response) < 12 or response[:4] != "RSLT":
        raise RuntimeError("malformed PACK response: %r" % response[:64])
    status, size = struct.unpack("<II", response[4:12])
    if size != len(response) - 12:
        raise RuntimeError("PACK response length mismatch")
    message = response[12:].decode("utf-8", "replace")
    if status != 0:
        raise RuntimeError("Worker rejected input: %s" % message)
    if not os.path.isfile(output) or not os.path.getsize(output):
        raise RuntimeError("Worker returned success but output EXR is missing or empty")
    return message


def _find_combo(dialog, required_items):
    for combo in dialog.findChildren(QtGui.QComboBox):
        items = [str(combo.itemText(i)) for i in range(combo.count())]
        if all(item in items for item in required_items):
            return combo
    return None


def _set_combo(combo, value):
    if combo is None:
        raise RuntimeError("required exporter dropdown is missing")
    index = combo.findText(value)
    if index < 0:
        raise RuntimeError("exporter dropdown does not offer %r" % value)
    combo.setCurrentIndex(index)
    combo.activated.emit(index)
    QtGui.QApplication.instance().processEvents()
    if str(combo.currentText()) != value:
        raise RuntimeError("exporter dropdown refused %r" % value)


def _find_check(dialog, label):
    for check in dialog.findChildren(QtGui.QCheckBox):
        if str(check.text()).strip().lower() == label.lower():
            return check
    return None


def _set_check(dialog, label, checked):
    check = _find_check(dialog, label)
    if check is None or not check.isEnabled():
        raise RuntimeError("exporter checkbox %r is missing or disabled" % label)
    if bool(check.isChecked()) != bool(checked):
        check.click()
    QtGui.QApplication.instance().processEvents()
    if bool(check.isChecked()) != bool(checked):
        raise RuntimeError("exporter checkbox %r did not retain requested value" % label)


def _set_line_edit(dialog, old_value, new_value):
    matches = []
    for edit in dialog.findChildren(QtGui.QLineEdit):
        text = str(edit.text()).strip()
        if text == str(old_value):
            matches.append(edit)
            continue
        try:
            if abs(float(text) - float(old_value)) < 0.001:
                matches.append(edit)
        except Exception:
            pass
    if not matches:
        raise RuntimeError("could not find exporter field %r" % old_value)
    edit = matches[0]
    edit.setText(str(new_value))
    edit.editingFinished.emit()
    QtGui.QApplication.instance().processEvents()
    if str(edit.text()).strip() != str(new_value):
        raise RuntimeError("exporter field did not retain %r" % new_value)


def _set_frame_range(dialog, start, inclusive_end):
    numeric = []
    for edit in dialog.findChildren(QtGui.QLineEdit):
        try:
            float(str(edit.text()).strip())
            numeric.append(edit)
        except Exception:
            pass
    if len(numeric) < 6:
        raise RuntimeError("expected six numeric exporter controls, found %d" % len(numeric))
    # Verified dialog ordering: width, height, start, end, output FPS, shutter.
    exporter_end = inclusive_end + 1
    for edit, value in ((numeric[2], start), (numeric[3], exporter_end)):
        edit.setText(str(value))
        edit.editingFinished.emit()
        QtGui.QApplication.instance().processEvents()
        if str(edit.text()).strip() != str(value):
            raise RuntimeError("frame-range field refused %r" % value)
    return exporter_end


def _top_widgets():
    app = QtGui.QApplication.instance()
    if app is None:
        return []
    result = []
    for candidate in app.topLevelWidgets():
        try:
            if not isinstance(candidate, QtGui.QWidget):
                candidate = shiboken.wrapInstance(shiboken.getCppPointer(candidate)[0],
                                                  QtGui.QWidget)
            if candidate.isVisible():
                result.append(candidate)
        except Exception:
            pass
    return result


def _initial_session_wizard():
    app = QtGui.QApplication.instance()
    if app is None:
        return None
    for widget in app.topLevelWidgets():
        try:
            if (widget.isVisible() and
                    str(widget.metaObject().className()) == "CQSFMNewWizardDialog"):
                return widget
        except Exception as error:
            _log("SFM session wizard inspection failed: %s" % error)
    return None


def _sfm_document_ready():
    global _readiness_signature
    try:
        # GetMainWindow is SFM's direct API and is more reliable than relying
        # on PySide's topLevelWidgets() list, which can omit wrapped C++ windows.
        widget = _main_window_widget()
        title = str(widget.windowTitle())
        class_name = str(widget.metaObject().className())
        lowered = title.lower()
        ready = ("no document available" not in lowered and ".dmx" in lowered)
        signature = (class_name, title, ready)
    except Exception as error:
        ready = False
        signature = ("unavailable", str(error), False)
    if signature != _readiness_signature:
        _readiness_signature = signature
        _log("SFM document readiness: class=%s title=%r ready=%s" %
             (signature[0], signature[1], ready))
    return ready


def _find_export_dialog():
    for widget in _top_widgets():
        if "CQMovieLayoffSettingsDialog" in widget.metaObject().className():
            return widget
    return None


def _pairs(directory):
    names = os.listdir(directory) if os.path.isdir(directory) else []
    depths = {}
    for name in names:
        lower = name.lower()
        if lower.endswith("_depth.pfm"):
            depths[lower[:-10]] = os.path.join(directory, name)
    result = []
    for name in names:
        lower = name.lower()
        if lower.endswith(".pfm") and not lower.endswith("_depth.pfm"):
            depth = depths.get(lower[:-4])
            if depth:
                result.append((os.path.join(directory, name), depth))
    return sorted(result)


def _pfm_files(directory):
    """Include orphaned PFMs so readiness detects incomplete pairs."""
    if not os.path.isdir(directory):
        return []
    return sorted(os.path.join(directory, name) for name in os.listdir(directory)
                  if name.lower().endswith(".pfm"))


def _describe_pfm_set(directory, expected_pairs):
    files = _pfm_files(directory)
    paired_paths = set(path.lower() for pair in _pairs(directory) for path in pair)
    orphaned = [os.path.basename(path) for path in files if path.lower() not in paired_paths]
    return "%d/%d complete pairs; %d PFM file(s); unmatched: %s" % (
        len(_pairs(directory)), expected_pairs, len(files),
        ", ".join(orphaned) if orphaned else "none")


def _file_signature(paths):
    signature = []
    for path in paths:
        try:
            stat = os.stat(path)
            signature.append((path, stat.st_size, stat.st_mtime))
        except OSError:
            signature.append((path, None, None))
    return signature


def _native_progress_windows():
    """Return visible exporter-like progress widgets and their real controls."""
    result = []
    for widget in _top_widgets():
        try:
            class_name = str(widget.metaObject().className())
            title = str(widget.windowTitle())
            if "SettingsDialog" in class_name or not (
                    "Layoff" in class_name or "Progress" in class_name or
                    "Progress" in title):
                continue
            bars = widget.findChildren(QtGui.QProgressBar)
            cancels = [button for button in widget.findChildren(QtGui.QPushButton)
                       if str(button.text()).replace("&", "").strip().lower() == "cancel"]
            result.append({"widget": widget, "class": class_name, "title": title,
                           "bars": bars, "cancels": cancels,
                           "cancel_verified": class_name in VERIFIED_NATIVE_CANCEL_CLASSES})
        except Exception:
            pass
    return result


def _pfm_dimensions(path):
    """Read only the PFM header; pixel parsing remains in the x64 Worker."""
    with open(path, "rb") as stream:
        magic = stream.readline().strip()
        if magic not in ("PF", "Pf"):
            raise RuntimeError("%s has invalid PFM magic %r" % (path, magic))
        dimensions = stream.readline().strip().split()
        if len(dimensions) != 2:
            raise RuntimeError("%s has malformed PFM dimensions" % path)
        try:
            width, height = int(dimensions[0]), int(dimensions[1])
        except ValueError:
            raise RuntimeError("%s has non-numeric PFM dimensions" % path)
        if width <= 0 or height <= 0:
            raise RuntimeError("%s has non-positive PFM dimensions" % path)
        scale = stream.readline().strip()
        if not scale:
            raise RuntimeError("%s has a missing PFM scale" % path)
        try:
            if float(scale) == 0.0:
                raise RuntimeError("%s has a zero PFM scale" % path)
        except ValueError:
            raise RuntimeError("%s has a malformed PFM scale" % path)
        return width, height


class RenderJobPanel(QtGui.QDialog):
    def __init__(self, parent=None):
        QtGui.QDialog.__init__(self, parent)
        self.setWindowTitle("ModernSFM Render Job")
        self.setMinimumWidth(570)
        self._busy = False
        self._state = JobStateMachine()
        self._stage = "Idle"
        self._manifest_path = None
        self._job_log_path = None
        self._job_id = None
        self._render_started = None
        self._session_open_started = None
        self._output_dir = None
        self._expected_frames = 0
        self._last_pair_signature = None
        self._quiet_since = None
        self._render_no_change_since = None
        self._last_render_signature = None
        self._render_config = None
        self._pfm_pairs_observed = 0
        self._pfm_pairs_stable = 0
        self._exrs_generated = 0
        self._processing_frame = None
        self._last_manifest_progress = None
        self._native_progress_seen = False
        self._native_progress_value = None
        self._native_progress_maximum = None
        self._last_manifest_progress = None
        self._cancel_requested = False
        self._cancel_origin = None
        self._cancel_requested_at = None
        self._cancel_quiet_since = None
        self._cancel_signature = None
        self._processing_thread = None
        self._make_ui()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(750)
        self._timer.timeout.connect(self._poll)
        self._session_poll = QtCore.QTimer(self)
        self._session_poll.setInterval(1000)
        self._session_poll.timeout.connect(self._refresh_sfm_state)
        self._session_poll.start()
        try:
            roots = set((os.path.abspath(str(self.output.text())),
                         os.path.join(ROOT, "build", "render_jobs")))
            recovered = []
            seen = set()
            for root in roots:
                for record in recover_interrupted_jobs(root, os.getpid()):
                    if record[0] not in seen:
                        recovered.append(record)
                        seen.add(record[0])
            for path, message in recovered:
                _log("recovered interrupted job %s: %s" % (path, message))
            if recovered:
                self.status.setText("Recovered %d interrupted job(s). See their job_status.json and job.log files." %
                                    len(recovered))
        except Exception as error:
            _log("interrupted-job recovery scan failed: %s" % error)
        self._refresh_sfm_state()

    def _make_ui(self):
        root = QtGui.QVBoxLayout(self)
        session_row = QtGui.QHBoxLayout()
        self.session = QtGui.QComboBox()
        self.refresh_button = QtGui.QPushButton("Refresh")
        session_row.addWidget(QtGui.QLabel("Session"))
        session_row.addWidget(self.session, 1)
        session_row.addWidget(self.refresh_button)
        root.addLayout(session_row)

        range_row = QtGui.QHBoxLayout()
        self.start_frame = QtGui.QSpinBox()
        self.start_frame.setRange(0, 99999999)
        self.start_frame.setValue(0)
        self.end_frame = QtGui.QSpinBox()
        self.end_frame.setRange(0, 99999999)
        self.end_frame.setValue(2)
        range_row.addWidget(QtGui.QLabel("Start frame"))
        range_row.addWidget(self.start_frame)
        range_row.addWidget(QtGui.QLabel("End frame (inclusive)"))
        range_row.addWidget(self.end_frame)
        range_row.addStretch(1)
        root.addLayout(range_row)

        output_row = QtGui.QHBoxLayout()
        self.output = QtGui.QLineEdit(os.path.join(ROOT, "build", "render_jobs"))
        self.output_button = QtGui.QPushButton("Browse…")
        output_row.addWidget(QtGui.QLabel("Output folder"))
        output_row.addWidget(self.output, 1)
        output_row.addWidget(self.output_button)
        root.addLayout(output_row)

        render_box = QtGui.QGroupBox("SFM render settings")
        render_form = QtGui.QFormLayout(render_box)
        self.width = QtGui.QComboBox()
        self.width.addItem("1280 × 720 (verified)", (1280, 720))
        self.height_label = QtGui.QLabel("Depth output alignment is only verified at this size")
        self.progressive = QtGui.QCheckBox("Progressive refinement")
        self.progressive.setChecked(True)
        self.depth = QtGui.QCheckBox("Write Depth PFM")
        self.depth.setChecked(True)
        self.dof = QtGui.QCheckBox("Depth of Field")
        self.dof.setChecked(False)
        self.motion_blur = QtGui.QCheckBox("Motion Blur")
        self.motion_blur.setChecked(False)
        render_form.addRow("Resolution", self.width)
        render_form.addRow("", self.height_label)
        render_form.addRow("", self.progressive)
        render_form.addRow("", self.depth)
        render_form.addRow("", self.dof)
        render_form.addRow("", self.motion_blur)
        root.addWidget(render_box)

        worker_box = QtGui.QGroupBox("EXR Worker settings")
        worker_form = QtGui.QFormLayout(worker_box)
        self.worker_count = QtGui.QLabel("1 (current Bridge supports one Worker)")
        self.memory_mib = QtGui.QSpinBox()
        self.memory_mib.setRange(256, 65536)
        self.memory_mib.setValue(4096)
        worker_form.addRow("Worker processes", self.worker_count)
        worker_form.addRow("Memory limit (MiB)", self.memory_mib)
        root.addWidget(worker_box)

        self.disclaimer = QtGui.QLabel("Prototype limit: 1–3 frames, 1280×720. SFM must have a session open before Start.")
        self.disclaimer.setWordWrap(True)
        root.addWidget(self.disclaimer)
        self.status = QtGui.QLabel("Choose a session and configure a short render.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.progress = QtGui.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        root.addWidget(self.progress)

        actions = QtGui.QHBoxLayout()
        actions.addStretch(1)
        self.start_button = QtGui.QPushButton("Start Render Job")
        self.cancel_button = QtGui.QPushButton("Cancel Job")
        self.cancel_button.setEnabled(False)
        self.close_button = QtGui.QPushButton("Close")
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.start_button)
        actions.addWidget(self.close_button)
        root.addLayout(actions)

        self.refresh_button.clicked.connect(self._refresh_sessions)
        self.output_button.clicked.connect(self._browse_output)
        self.start_button.clicked.connect(self._start_job)
        self.cancel_button.clicked.connect(self._cancel_job)
        self.close_button.clicked.connect(self.close)
        self._refresh_sessions()

    def _refresh_sfm_state(self):
        ready = _sfm_document_ready() and _initial_session_wizard() is None
        if not self._busy:
            self.start_button.setEnabled(ready)
            if ready:
                if str(self.status.text()).startswith("Open any SFM session") or \
                        str(self.status.text()).startswith("Choose a session"):
                    self.status.setText("SFM is ready. Choose a session and configure a short render.")
                self.raise_()
            else:
                self.status.setText("Open any session in SFM first. ModernSFM will render the session selected here.")

    def _refresh_sessions(self):
        current = str(self.session.itemData(self.session.currentIndex())) if self.session.count() else ""
        self.session.clear()
        try:
            files = sorted(name for name in os.listdir(SESSIONS_DIR)
                           if name.lower().endswith(".dmx"))
        except OSError:
            files = []
        for name in files:
            self.session.addItem(name, os.path.join(SESSIONS_DIR, name))
        index = self.session.findData(current)
        if index >= 0:
            self.session.setCurrentIndex(index)
        if not files:
            self.status.setText("No .dmx sessions found in %s" % SESSIONS_DIR)

    def _browse_output(self):
        path = QtGui.QFileDialog.getExistingDirectory(self, "Render output folder",
                                                       str(self.output.text()))
        if path:
            self.output.setText(path)

    def _set_busy(self, busy):
        self._busy = busy
        for widget in (self.session, self.refresh_button, self.start_frame, self.end_frame,
                       self.output, self.output_button, self.width, self.progressive,
                       self.depth, self.dof, self.motion_blur, self.worker_count,
                       self.memory_mib):
            widget.setEnabled(not busy)
        self.start_button.setEnabled(not busy)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(busy)
        if busy:
            self.progress.show()
        else:
            self.progress.hide()

    def _manifest(self, error=None):
        if not self._manifest_path or not self._render_config:
            return
        manifest = {
            "job_id": self._job_id,
            "state": self._state.state,
            "stage": self._stage,
            "error": error,
            "sfm_pid": os.getpid(),
            "session_source": self._render_config["session"],
            "session_copy": self._render_config.get("session_copy"),
            "sfm_session_copy": self._render_config.get("sfm_session_copy"),
            "output_dir": self._output_dir or self._render_config.get("job_dir"),
            "start_frame": self._render_config["start"],
            "end_frame_inclusive": self._render_config["end"],
            "total_frames": self._expected_frames or
                            (self._render_config["end"] - self._render_config["start"] + 1),
            "pfm_pairs_observed": self._pfm_pairs_observed,
            "pfm_pairs_stable": self._pfm_pairs_stable,
            "current_processing_frame": self._processing_frame,
            "exrs_generated": self._exrs_generated,
            "native_progress_observed": self._native_progress_seen,
            "native_progress_value": self._native_progress_value,
            "native_progress_maximum": self._native_progress_maximum,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        write_manifest(self._manifest_path, manifest)

    def _transition(self, next_state, stage=None, error=None):
        previous = self._state.state
        self._state.transition(next_state)
        self._stage = stage or next_state
        self._manifest(error)
        _log("state transition %s -> %s; stage=%s%s" %
             (previous, next_state, self._stage,
              "; error=" + str(error) if error else ""))
        self._update_cancel_enabled()

    def _set_stage(self, stage):
        self._stage = stage
        self._manifest()
        _log("job stage: %s (state=%s)" % (stage, self._state.state))

    def _update_cancel_enabled(self, progress_windows=None):
        state = self._state.state
        enabled = state in ("Queued", "Preparing")
        if state == "Rendering":
            if progress_windows is None:
                progress_windows = _native_progress_windows()
            enabled = (len(progress_windows) == 1 and
                       progress_windows[0]["cancel_verified"] and
                       len(progress_windows[0]["cancels"]) == 1 and
                       progress_windows[0]["cancels"][0].isEnabled())
        elif state == "Processing":
            enabled = self._processing_thread is not None and self._processing_thread.isRunning()
        self.cancel_button.setEnabled(bool(enabled) and not self._cancel_requested)

    def _fail(self, stage, error):
        message = "Failed during %s: %s" % (stage, error)
        try:
            if self._state.state not in ("Failed", "Cancelled", "Completed"):
                self._transition("Failed", stage, str(error))
        except Exception as transition_error:
            _log("could not record Failed transition after %s: %s" % (error, transition_error))
        self._finish(message)

    def _cancelled(self, message):
        if self._state.state == "Cancelling":
            self._transition("Cancelled", "Cancelled")
        self._finish(message)

    def _start_job(self):
        if self._busy:
            return
        if not _sfm_document_ready() or _initial_session_wizard() is not None:
            QtGui.QMessageBox.warning(
                self, "SFM session required",
                "Open a session in SFM first. The initial SFM startup wizard cannot yet be bypassed safely by the panel.")
            self._refresh_sfm_state()
            return
        session_path = self.session.itemData(self.session.currentIndex())
        output_root = str(self.output.text()).strip()
        start = self.start_frame.value()
        end = self.end_frame.value()
        count = end - start + 1
        if not session_path or not os.path.isfile(session_path):
            QtGui.QMessageBox.warning(self, "Session required", "Choose an existing SFM session.")
            return
        if count < 1 or count > MAX_FRAMES:
            QtGui.QMessageBox.warning(self, "Prototype frame limit",
                                      "The current prototype supports 1 to 3 frames.")
            return
        if end < start:
            QtGui.QMessageBox.warning(self, "Invalid frame range", "End frame must be >= start frame.")
            return
        if not output_root:
            QtGui.QMessageBox.warning(self, "Output folder required", "Choose an output folder.")
            return
        if not self.depth.isChecked():
            QtGui.QMessageBox.warning(self, "Depth required",
                                      "The Worker currently needs a matching Depth PFM.")
            return

        config = {
            "session": str(session_path), "start": start, "end": end,
            "width": SUPPORTED_WIDTH, "height": SUPPORTED_HEIGHT,
            "progressive": self.progressive.isChecked(), "depth": self.depth.isChecked(),
            "dof": self.dof.isChecked(), "motion_blur": self.motion_blur.isChecked(),
            "worker_count": 1, "memory_mib": self.memory_mib.value(),
            "output_root": os.path.abspath(output_root)
        }
        if config["dof"]:
            QtGui.QMessageBox.warning(self, "DOF is not supported in this prototype",
                                      "The current panel does not expose verified light-sample controls. Disable Depth of Field for this proof.")
            return
        if config["motion_blur"]:
            QtGui.QMessageBox.warning(self, "Motion Blur is not validated",
                                      "The prototype keeps Motion Blur off until its sequence output is verified.")
            return

        answer = QtGui.QMessageBox.question(
            self, "Confirm Render Job",
            "Session: %s\nFrames: %d–%d inclusive (%d)\nResolution: %dx%d\n"
            "Beauty + Depth PFM; DOF off; Motion Blur off\n"
            "Worker processes: %d; memory limit: %d MiB\nOutput: %s" %
            (os.path.basename(config["session"]), start, end, count,
             config["width"], config["height"], config["worker_count"],
             config["memory_mib"], config["output_root"]),
            QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
            QtGui.QMessageBox.No)
        if answer != QtGui.QMessageBox.Yes:
            return
        job_name = "job_" + time.strftime("%Y%m%d_%H%M%S")
        config["job_dir"] = os.path.join(config["output_root"], job_name)
        if os.path.exists(config["job_dir"]):
            QtGui.QMessageBox.warning(self, "Output already exists",
                                      "A job folder with this timestamp already exists. Wait a second and try again.")
            return
        try:
            os.makedirs(config["job_dir"])
        except Exception as error:
            self.status.setText("Failed during job preparation: %s" % error)
            _log("could not create job directory %s: %s" % (config["job_dir"], error))
            return
        self._render_config = config
        self._job_id = job_name
        self._manifest_path = os.path.join(config["job_dir"], "job_status.json")
        self._job_log_path = os.path.join(config["job_dir"], "job.log")
        self._output_dir = config["job_dir"]
        self._expected_frames = count
        self._pfm_pairs_observed = 0
        self._pfm_pairs_stable = 0
        self._exrs_generated = 0
        self._processing_frame = None
        self._cancel_requested = False
        self._cancel_origin = None
        self._cancel_requested_at = None
        self._cancel_quiet_since = None
        self._cancel_signature = None
        self._last_pair_signature = None
        self._quiet_since = None
        self._render_no_change_since = None
        self._last_render_signature = None
        self._set_busy(True)
        self._transition("Queued", "Job accepted")
        self._transition("Preparing", "Preparing disposable session")
        self.status.setText("Opening disposable session copy…")
        _log("queued selected-session open for %s" % config["session"])
        QtCore.QTimer.singleShot(0, self._open_session)

    def _open_session(self):
        if self._cancel_requested:
            self._cancelled("Cancelled during preparation. Session files were retained.")
            return
        config = self._render_config
        try:
            # Preserve the job-local copy for audit/recovery. SFM has loaded
            # disposable copies reliably from its own sessions directory in
            # the Sequence Export POC; opening the output-folder copy here
            # produced delayed materialsystem access violations.
            config["session_copy"] = os.path.join(config["job_dir"], "session_copy.dmx")
            shutil.copyfile(config["session"], config["session_copy"])
            config["sfm_session_copy"] = os.path.join(
                SESSIONS_DIR, "_modern_sfm_%s.dmx" % os.path.basename(config["job_dir"]))
            if os.path.exists(config["sfm_session_copy"]):
                raise RuntimeError("disposable SFM session already exists: %s" %
                                   config["sfm_session_copy"])
            shutil.copyfile(config["session_copy"], config["sfm_session_copy"])
            self._manifest()
            wizard = _initial_session_wizard()
            if wizard is not None:
                raise RuntimeError("SFM startup wizard is still open; open a session and retry")
            opened = sfmApp.OpenDocument(config["sfm_session_copy"])
            _log("OpenDocument(disposable SFM-session copy %s; job archive %s) returned %r" %
                 (config["sfm_session_copy"], config["session_copy"], opened))
            if opened is False or opened is None:
                raise RuntimeError("SFM did not open the selected session")
            self._set_stage("Waiting for disposable session to load")
            self._session_open_started = time.time()
            QtCore.QTimer.singleShot(1000, self._wait_for_document)
        except Exception as error:
            self._fail("session preparation", error)

    def _wait_for_document(self):
        if self._cancel_requested:
            self._cancelled("Cancelled during session preparation. Session copies were retained.")
            return
        expected = os.path.basename(self._render_config["sfm_session_copy"]).lower()
        loaded = False
        for widget in QtGui.QApplication.instance().topLevelWidgets():
            try:
                if str(widget.metaObject().className()) == "CQSFMMainWindow":
                    title = str(widget.windowTitle()).lower()
                    if expected in title and "no document available" not in title:
                        loaded = True
                        break
            except Exception:
                pass
        if loaded:
            _log("SFM main window confirms selected session copy is loaded")
            self._schedule_exporter_launch()
            return
        if time.time() - self._session_open_started >= 60:
            self._fail("session preparation", "SFM did not confirm the selected session copy as loaded within 60 seconds")
            return
        QtCore.QTimer.singleShot(2000, self._wait_for_document)

    def _schedule_exporter_launch(self):
        if self._cancel_requested:
            self._cancelled("Cancelled before native exporter launch. Session copies were retained.")
            return
        self._set_stage("Preparing native Sequence Exporter")
        _log("queued Sequence Exporter launch after document load")
        QtCore.QTimer.singleShot(12000, self._launch_exporter)

    def _launch_exporter(self):
        if self._cancel_requested:
            self._cancelled("Cancelled before native exporter launch. Session copies were retained.")
            return
        _log("launching the native Sequence Exporter")
        try:
            self._output_dir = self._render_config["job_dir"]
            _log("render output folder: %s" % self._output_dir)
            self._render_started = time.time()
            main_window = sfmApp.GetMainWindow()
            for action in main_window.findChildren(QtGui.QAction):
                text = str(action.text()).replace("&", "").strip()
                name = str(action.objectName())
                if text == "Movie..." or "Export Movie" in text or "ExportMovie" in name:
                    self.status.setText("Configuring SFM Sequence Exporter…")
                    self.hide()
                    self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowStaysOnTopHint)
                    QtCore.QTimer.singleShot(1000, self._configure_exporter)
                    action.trigger()
                    _log("native Export Movie action returned to panel")
                    self.show()
                    self.raise_()
                    return
            raise RuntimeError("native Export Movie action not found")
        except Exception as error:
            self._fail("SFM exporter startup", error)

    def _configure_exporter(self):
        if self._cancel_requested:
            self._cancelled("Cancelled before native exporter configuration. Session copies were retained.")
            return
        try:
            dialog = _find_export_dialog()
            if dialog is None:
                raise RuntimeError("native Export Movie settings dialog did not appear")
            _set_combo(_find_combo(dialog, ["Movie", "Image Sequence", "Sound"]), "Image Sequence")
            _set_combo(_find_combo(dialog, ["PFM", "PNG", "JPEG", "TGA"]), "PFM")
            _set_combo(_find_combo(dialog, ["Sequence", "Selected Shots", "Custom"]), "Custom")
            _set_combo(_find_combo(dialog, ["Seconds", "Frames"]), "Frames")
            _set_combo(_find_combo(dialog, ["Custom", "360p", "480p", "720p"]), "Custom")
            prefix = os.path.join(self._output_dir, "frame").replace("\\", "/")
            path_edits = [edit for edit in dialog.findChildren(QtGui.QLineEdit)
                          if "/" in str(edit.text()) or "\\" in str(edit.text())]
            if not path_edits:
                raise RuntimeError("exporter output path field not found")
            path_edits[0].setText(prefix)
            path_edits[0].editingFinished.emit()
            config = self._render_config
            _set_line_edit(dialog, "1280", config["width"])
            _set_line_edit(dialog, "720", config["height"])
            exporter_end = _set_frame_range(dialog, config["start"], config["end"])
            _set_check(dialog, "Write PFM depth file", True)
            _set_check(dialog, "Audio", False)
            _set_check(dialog, "Separate WAV file", False)
            _set_check(dialog, "Progressive Refinement", config["progressive"])
            _set_check(dialog, "Depth Of Field", config["dof"])
            _set_check(dialog, "Motion Blur", config["motion_blur"])
            self._expected_frames = config["end"] - config["start"] + 1
            _log("panel verified exporter controls: frames %d..%d inclusive translated to %d end-exclusive; %dx%d; PFM + depth" %
                 (config["start"], config["end"], exporter_end,
                  config["width"], config["height"]))
            export_button = None
            for button in dialog.findChildren(QtGui.QPushButton):
                if str(button.text()).replace("&", "").strip() == "Export Movie" and button.isEnabled():
                    export_button = button
                    break
            if export_button is None:
                raise RuntimeError("enabled Export Movie button not found")
            self._transition("Rendering", "SFM rendering")
            self.status.setText("SFM rendering: waiting for %d Beauty/Depth pair(s)…" % self._expected_frames)
            export_button.click()
            self._timer.start()
        except Exception as error:
            self._fail("SFM exporter configuration", error)

    def _cancel_job(self):
        state = self._state.state
        if state in ("Queued", "Preparing"):
            self._cancel_requested = True
            self._transition("Cancelling", "Cancelling before render")
            self._cancelled("Cancelled before rendering. Any session copies were retained.")
            return
        if state == "Rendering":
            windows = _native_progress_windows()
            if (len(windows) != 1 or not windows[0]["cancel_verified"] or
                    len(windows[0]["cancels"]) != 1 or
                    not windows[0]["cancels"][0].isEnabled()):
                self.cancel_button.setEnabled(False)
                self.status.setText("SFM render cancellation is unavailable: no live-verified native Cancel control. Rendering continues.")
                return
            self._cancel_requested = True
            self._cancel_origin = "rendering"
            self._cancel_requested_at = time.time()
            self._cancel_quiet_since = None
            self._cancel_signature = None
            self._transition("Cancelling", "Cancelling native SFM exporter")
            try:
                windows[0]["cancels"][0].click()
                _log("activated the native Cancel button in %s (%s)" %
                     (windows[0]["class"], windows[0]["title"]))
                self.status.setText("Cancelling SFM render; waiting for the native progress window to close…")
            except Exception as error:
                self._fail("SFM render cancellation", error)
            return
        if state == "Processing" and self._processing_thread is not None:
            self._cancel_requested = True
            self._cancel_origin = "processing"
            self._transition("Cancelling", "Finishing current EXR before cancellation")
            self._processing_thread.request_cancel()
            self.status.setText("Cancelling after the current EXR; no further frames will be submitted.")
            return

    def _cancel_render_poll(self, progress_windows, signature):
        if time.time() - self._cancel_requested_at > 30.0:
            self._fail("SFM render cancellation", "native exporter did not close within 30 seconds")
            return True
        if progress_windows:
            self.status.setText("Cancelling SFM render; native progress window is still open…")
            return True
        if signature != self._cancel_signature:
            self._cancel_signature = signature
            self._cancel_quiet_since = time.time()
        elif self._cancel_quiet_since is None:
            self._cancel_quiet_since = time.time()
        if (self._cancel_quiet_since is not None and
                time.time() - self._cancel_quiet_since >= 2.0 and
                time.time() - self._cancel_requested_at >= 2.0):
            partial = len(_pairs(self._output_dir))
            self._cancelled("Cancelled during SFM rendering. %d stable PFM pair(s) retained; no EXRs were submitted." %
                            partial)
        return True

    def _poll(self):
        try:
            pairs = _pairs(self._output_dir)
            if len(pairs) > self._expected_frames:
                raise RuntimeError("exporter produced more pairs than requested; PFMs retained")
            paths = _pfm_files(self._output_dir)
            signature = _file_signature(paths)
            if paths and signature == self._last_render_signature:
                if self._render_no_change_since is None:
                    self._render_no_change_since = time.time()
            else:
                self._last_render_signature = signature
                self._render_no_change_since = time.time()
            pair_paths = [path for pair in pairs for path in pair]
            pair_signature = _file_signature(pair_paths)
            if pair_paths and pair_signature == self._last_pair_signature:
                if self._quiet_since is None:
                    self._quiet_since = time.time()
            else:
                self._last_pair_signature = pair_signature
                self._quiet_since = time.time() if pair_paths else None

            self._pfm_pairs_observed = len(pairs)
            progress_windows = _native_progress_windows()
            progress_open = bool(progress_windows)
            self._update_cancel_enabled(progress_windows)
            if progress_windows:
                if not self._native_progress_seen:
                    self._native_progress_seen = True
                    _log("observed SFM progress widget: %s title=%r; cancel buttons=%d" %
                         (progress_windows[0]["class"], progress_windows[0]["title"],
                          len(progress_windows[0]["cancels"])))
                bars = progress_windows[0]["bars"]
                if bars:
                    bar = bars[0]
                    self._native_progress_value = int(bar.value())
                    self._native_progress_maximum = int(bar.maximum())
                    if bar.maximum() > 0:
                        self.progress.setRange(int(bar.minimum()), int(bar.maximum()))
                        self.progress.setValue(int(bar.value()))
                    else:
                        self.progress.setRange(0, 0)
                    self.status.setText("SFM native progress %d/%d; %d PFM pair(s) observed; %d/%d EXRs validated" %
                                        (bar.value(), bar.maximum(), len(pairs),
                                         self._exrs_generated, self._expected_frames))
                else:
                    self.status.setText("SFM rendering; %d PFM pair(s) observed; %d/%d EXRs validated" %
                                        (len(pairs), self._exrs_generated, self._expected_frames))
            else:
                self._native_progress_value = None
                self._native_progress_maximum = None
                self.progress.setRange(0, 0)
                self.status.setText("SFM rendering; %d PFM pair(s) observed (not yet confirmed complete); %d/%d EXRs validated" %
                                    (len(pairs), self._exrs_generated, self._expected_frames))

            progress_snapshot = (self._pfm_pairs_observed,
                                 self._native_progress_value,
                                 self._native_progress_maximum)
            if progress_snapshot != self._last_manifest_progress:
                self._last_manifest_progress = progress_snapshot
                self._manifest()

            if self._state.state == "Cancelling" and self._cancel_origin == "rendering":
                self._cancel_render_poll(progress_windows, signature)
                return

            elapsed = time.time() - self._render_started
            enough = len(pairs) == self._expected_frames
            quiet = self._quiet_since is not None and time.time() - self._quiet_since >= 3.0
            stable = bool(paths) and all(item[1] for item in signature)
            if enough and quiet and stable and not progress_open:
                self._timer.stop()
                self._pfm_pairs_stable = len(pairs)
                self.status.setText("Expected PFM pairs are stable. Checking dimensions…")
                QtCore.QTimer.singleShot(0, self._process_pairs)
                return
            # The progress dialog was absent in the successful SFM run, so
            # absence alone cannot distinguish completion from an exporter
            # fault. If output has stopped changing for a full minute while
            # the requested set is incomplete, fail with the exact observed
            # file set instead of silently waiting for the five-minute limit.
            no_change = (self._render_no_change_since is not None and
                         time.time() - self._render_no_change_since >= 60.0)
            no_output_for = (not paths and self._render_started is not None and
                             time.time() - self._render_started >= 60.0)
            if (not progress_open and len(pairs) < self._expected_frames and
                    (no_change or no_output_for)):
                self._timer.stop()
                self._fail("PFM output readiness",
                           "export output stopped before all expected frames were written: %s" %
                           _describe_pfm_set(self._output_dir, self._expected_frames))
                return
            if elapsed > MAX_RENDER_SECONDS:
                self._timer.stop()
                self._fail("SFM exporter completion",
                           "%d-second watchdog expired; %s; any output was retained" %
                           (MAX_RENDER_SECONDS,
                            _describe_pfm_set(self._output_dir, self._expected_frames)))
                return
        except Exception as error:
            self._timer.stop()
            self._fail("SFM render monitoring", error)

    def _process_pairs(self):
        failed_stage = "PFM input validation"
        try:
            if self._cancel_requested:
                self._cancelled("Cancelled before EXR processing. PFM sources were retained.")
                return
            pairs = _pairs(self._output_dir)
            if len(pairs) != self._expected_frames:
                raise RuntimeError("expected %d complete pairs, found %d" %
                                   (self._expected_frames, len(pairs)))
            paths = _pfm_files(self._output_dir)
            signature = _file_signature(paths)
            if not paths or len(signature) != self._expected_frames * 2 or \
                    not all(item[1] for item in signature):
                raise RuntimeError("expected exactly %d nonempty Beauty/Depth PFM files; found %d (%s)" %
                                   (self._expected_frames * 2, len(paths),
                                    _describe_pfm_set(self._output_dir, self._expected_frames)))
            for index, (beauty, depth) in enumerate(pairs):
                beauty_size = _pfm_dimensions(beauty)
                depth_size = _pfm_dimensions(depth)
                expected_size = (self._render_config["width"], self._render_config["height"])
                if beauty_size != depth_size:
                    raise RuntimeError("frame %d Beauty/Depth dimensions differ: %r vs %r" %
                                       (index, beauty_size, depth_size))
                if beauty_size != expected_size:
                    raise RuntimeError("frame %d dimensions are %r; expected %r" %
                                       (index, beauty_size, expected_size))
                _log("frame %d PFM headers verified: Beauty=%r Depth=%r" %
                     (index, beauty_size, depth_size))
            failed_stage = "Worker startup"
            self._set_stage("Starting x64 Worker")
            self.progress.setRange(0, self._expected_frames)
            self.progress.setValue(self._exrs_generated)
            self._transition("Processing", "Starting x64 Worker")
            thread = ExrProcessingThread(pairs, self._render_config["memory_mib"],
                                         _load_bridge, _pack, _sha256, self)
            thread.frame_started.connect(self._on_worker_frame_started)
            thread.frame_validated.connect(self._on_worker_frame_validated)
            thread.result_ready.connect(self._on_worker_result)
            self._processing_thread = thread
            self._set_stage("x64 Worker is processing EXR frames")
            self.status.setText("EXR processing: 0/%d validated" % self._expected_frames)
            thread.start()
        except Exception as error:
            self._fail(failed_stage, error)

    def _on_worker_frame_started(self, frame_number, total):
        self._processing_frame = frame_number
        self._set_stage("Worker processing frame %d/%d" % (frame_number, total))
        self.status.setText("EXR processing: frame %d/%d; %d EXR(s) validated" %
                            (frame_number, total, self._exrs_generated))

    def _on_worker_frame_validated(self, frame_number, total, message):
        self._exrs_generated = frame_number
        self._processing_frame = None
        _log(message)
        self._set_stage("Worker validated EXR %d/%d" % (frame_number, total))
        self.progress.setRange(0, total)
        self.progress.setValue(self._exrs_generated)
        self.status.setText("EXR processing: %d/%d validated" % (self._exrs_generated, total))

    def _on_worker_result(self, outcome, completed, failed_stage, error_text):
        self._exrs_generated = completed
        self._processing_frame = None
        if outcome == "Completed" and self._state.state == "Processing":
            self._transition("Completed", "Completed")
            _log("job completed: %d EXR frame(s); all source PFMs retained" % completed)
            self._finish("Completed: %d EXR frame(s) validated. Source PFMs were retained.\n\n%s" %
                         (completed, self._output_dir))
        elif outcome == "Cancelled" and self._state.state == "Cancelling":
            self._cancelled("Cancelled during EXR processing after %d validated frame(s). "
                            "No further frames were submitted; PFMs and partial EXRs were retained." %
                            completed)
        elif outcome == "Failed":
            self._fail(failed_stage or "Worker processing", error_text or "unspecified Worker failure")
        else:
            self._fail("Render Job state handling",
                       "Worker finished with outcome %s while job state was %s" %
                       (outcome, self._state.state))

    def _finish(self, message):
        self._timer.stop()
        self._set_busy(False)
        self.status.setText(message)
        self._manifest()
        _log(message.replace("\n", " | "))


def show_render_job_panel():
    global _panel_ref
    if _panel_ref is None:
        _panel_ref = RenderJobPanel(None)
    _panel_ref.show()
    _panel_ref.raise_()
    _panel_ref.activateWindow()
    return _panel_ref


def _main_window_widget():
    raw = sfmApp.GetMainWindow()
    try:
        return shiboken.wrapInstance(shiboken.getCppPointer(raw)[0], QtGui.QWidget)
    except Exception:
        if isinstance(raw, QtGui.QWidget):
            return raw
        raise


def _register():
    show_render_job_panel()
    try:
        import __builtin__
        __builtin__.show_modern_sfm_render_job = show_render_job_panel
    except Exception:
        pass
    _log("opened standalone ModernSFM Render Job window. Console entry: show_modern_sfm_render_job()")


_register()
