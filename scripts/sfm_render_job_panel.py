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
import os
import shutil
import struct
import time

from PySide import QtCore, QtGui
try:
    import shiboken
except ImportError:
    from PySide import shiboken
import sfmApp


ROOT = r"D:\WorkFlow\Programming\Projects\ModernSFM"
SFM_GAME = r"D:\SteamLibrary\steamapps\common\SourceFilmmaker\game"
SESSIONS_DIR = os.path.join(SFM_GAME, "usermod", "elements", "sessions")
BRIDGE_DLL = os.path.join(ROOT, "build", "x86", "Release", "modern_sfm_bridge.dll")
WORKER_EXE = os.path.join(ROOT, "build", "x64", "Release", "modern_sfm_worker.exe")
LOG = os.path.join(ROOT, "build", "render_job_panel.txt")
MAX_FRAMES = 3
MAX_RENDER_SECONDS = 300
SUPPORTED_WIDTH = 1280
SUPPORTED_HEIGHT = 720

_panel_ref = None


def _log(message):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    print("ModernSFM Render Job: " + line)
    try:
        parent = os.path.dirname(LOG)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(LOG, "a") as stream:
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
    app = QtGui.QApplication.instance()
    if app is None:
        return False
    for widget in app.topLevelWidgets():
        try:
            if str(widget.metaObject().className()) == "CQSFMMainWindow":
                title = str(widget.windowTitle()).lower()
                return ("no document available" not in title and
                        " - source filmmaker" in title)
        except Exception:
            pass
    return False


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


def _ready(paths):
    previous = None
    stable = 0
    for unused in range(20):
        state = []
        for path in paths:
            try:
                stat = os.stat(path)
                state.append((path, stat.st_size, stat.st_mtime))
            except OSError:
                state.append((path, None, None))
        if state == previous and all(size for path, size, mtime in state):
            stable += 1
            if stable >= 3:
                return True
        else:
            stable = 0
        previous = state
        time.sleep(0.5)
    return False


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
        self._render_started = None
        self._session_open_started = None
        self._output_dir = None
        self._expected_frames = 0
        self._last_pair_signature = None
        self._quiet_since = None
        self._render_config = None
        self._make_ui()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(750)
        self._timer.timeout.connect(self._poll)
        self._session_poll = QtCore.QTimer(self)
        self._session_poll.setInterval(1000)
        self._session_poll.timeout.connect(self._refresh_sfm_state)
        self._session_poll.start()
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
        self.close_button = QtGui.QPushButton("Close")
        actions.addWidget(self.start_button)
        actions.addWidget(self.close_button)
        root.addLayout(actions)

        self.refresh_button.clicked.connect(self._refresh_sessions)
        self.output_button.clicked.connect(self._browse_output)
        self.start_button.clicked.connect(self._start_job)
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
        if busy:
            self.progress.show()
        else:
            self.progress.hide()

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
        self._render_config = config
        self._set_busy(True)
        self.status.setText("Opening disposable session copy…")
        self._log_path = None
        _log("queued selected-session open for %s" % config["session"])
        QtCore.QTimer.singleShot(0, self._open_session)

    def _open_session(self):
        config = self._render_config
        try:
            # Preserve the job-local copy for audit/recovery. SFM has loaded
            # disposable copies reliably from its own sessions directory in
            # the Sequence Export POC; opening the output-folder copy here
            # produced delayed materialsystem access violations.
            os.makedirs(config["job_dir"])
            config["session_copy"] = os.path.join(config["job_dir"], "session_copy.dmx")
            shutil.copyfile(config["session"], config["session_copy"])
            config["sfm_session_copy"] = os.path.join(
                SESSIONS_DIR, "_modern_sfm_%s.dmx" % os.path.basename(config["job_dir"]))
            if os.path.exists(config["sfm_session_copy"]):
                raise RuntimeError("disposable SFM session already exists: %s" %
                                   config["sfm_session_copy"])
            shutil.copyfile(config["session_copy"], config["sfm_session_copy"])
            wizard = _initial_session_wizard()
            if wizard is not None:
                raise RuntimeError("SFM startup wizard is still open; open a session and retry")
            opened = sfmApp.OpenDocument(config["sfm_session_copy"])
            _log("OpenDocument(disposable SFM-session copy %s; job archive %s) returned %r" %
                 (config["sfm_session_copy"], config["session_copy"], opened))
            if opened is False or opened is None:
                raise RuntimeError("SFM did not open the selected session")
            self._session_open_started = time.time()
            QtCore.QTimer.singleShot(1000, self._wait_for_document)
        except Exception as error:
            self._finish("Failed to open selected session: %s" % error)

    def _wait_for_document(self):
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
            self._finish("SFM did not confirm the selected session copy as loaded within 60 seconds")
            return
        QtCore.QTimer.singleShot(2000, self._wait_for_document)

    def _schedule_exporter_launch(self):
        _log("queued Sequence Exporter launch after document load")
        QtCore.QTimer.singleShot(12000, self._launch_exporter)

    def _launch_exporter(self):
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
            self._finish("Failed to start Sequence Exporter: %s" % error)

    def _configure_exporter(self):
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
            self.status.setText("SFM rendering: waiting for %d Beauty/Depth pair(s)…" % self._expected_frames)
            export_button.click()
            self._timer.start()
        except Exception as error:
            self._finish("Failed to configure Sequence Exporter: %s" % error)

    def _poll(self):
        try:
            pairs = _pairs(self._output_dir)
            if len(pairs) > self._expected_frames:
                raise RuntimeError("exporter produced more pairs than requested; PFMs retained")
            paths = [path for pair in pairs for path in pair]
            signature = []
            for path in paths:
                stat = os.stat(path)
                signature.append((path, stat.st_size, stat.st_mtime))
            if paths and signature == self._last_pair_signature:
                if self._quiet_since is None:
                    self._quiet_since = time.time()
            else:
                self._last_pair_signature = signature
                self._quiet_since = time.time() if paths else None

            progress_open = False
            for widget in _top_widgets():
                class_name = str(widget.metaObject().className())
                title = str(widget.windowTitle())
                if "SettingsDialog" not in class_name and (
                        "Layoff" in class_name or "Progress" in class_name or "Progress" in title):
                    progress_open = True
                    bars = widget.findChildren(QtGui.QProgressBar)
                    if bars:
                        self.status.setText("SFM rendering… native progress %d/%d" %
                                            (bars[0].value(), bars[0].maximum()))
                    break

            elapsed = time.time() - self._render_started
            enough = len(pairs) == self._expected_frames
            quiet = self._quiet_since is not None and time.time() - self._quiet_since >= 3.0
            stable = _ready(paths) if enough else False
            if enough and quiet and stable and not progress_open:
                self._timer.stop()
                self.status.setText("Expected PFM pairs are stable. Checking dimensions…")
                QtCore.QTimer.singleShot(0, self._process_pairs)
                return
            if elapsed > MAX_RENDER_SECONDS:
                self._timer.stop()
                raise RuntimeError("%d-second render watchdog expired; any PFM files were retained" %
                                   MAX_RENDER_SECONDS)
            self.status.setText("SFM render: %d/%d Beauty/Depth pairs found%s" %
                                (len(pairs), self._expected_frames,
                                 "; awaiting stable files" if pairs else ""))
        except Exception as error:
            self._timer.stop()
            self._finish("Render monitoring failed: %s. Any PFMs were retained." % error)

    def _process_pairs(self):
        bridge = None
        try:
            pairs = _pairs(self._output_dir)
            if len(pairs) != self._expected_frames:
                raise RuntimeError("expected %d complete pairs, found %d" %
                                   (self._expected_frames, len(pairs)))
            paths = [path for pair in pairs for path in pair]
            if not _ready(paths):
                raise RuntimeError("PFM sizes and timestamps did not stabilize")
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
            self.status.setText("PFM pairs match %dx%d. Worker is validating EXRs…" %
                                (self._render_config["width"], self._render_config["height"]))
            QtGui.QApplication.instance().processEvents()
            bridge = _load_bridge()
            for index, pair in enumerate(pairs):
                beauty, depth = pair
                before = (_sha256(beauty), _sha256(depth))
                output = os.path.splitext(beauty)[0] + ".exr"
                message = _pack(bridge, beauty, depth, output,
                                self._render_config["memory_mib"])
                if not os.path.isfile(output) or os.path.getsize(output) <= 0:
                    raise RuntimeError("frame %d EXR is missing or empty after Worker response" % index)
                after = (_sha256(beauty), _sha256(depth))
                if before != after:
                    raise RuntimeError("source PFM changed during Worker processing")
                self.status.setText("Worker validated EXR %d/%d" % (index + 1, len(pairs)))
                QtGui.QApplication.instance().processEvents()
                _log(message)
            _log("job completed: %d EXR frame(s); all source PFMs retained" % len(pairs))
            self._finish("Completed: %d EXR frame(s) validated. Source PFMs were retained.\n\n%s" %
                         (len(pairs), self._output_dir))
        except Exception as error:
            self._finish("Worker processing failed: %s. Source PFMs were retained." % error)
        finally:
            if bridge is not None:
                try:
                    result = bridge.shutdown(10000)
                    if result != 0:
                        _log("Bridge shutdown returned %d: %s" % (result, _last_error(bridge)))
                except Exception as error:
                    _log("Bridge shutdown error: %s" % error)

    def _finish(self, message):
        self._timer.stop()
        self._set_busy(False)
        self.status.setText(message)
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
