# Run in SFM with:
# sfm.exe -game usermod -sfm_startup_script <this file> -console -condebug
#
# Small native-sequence-export proof. Uses a disposable copy of session4.dmx,
# exports frames 0..2 at 640x360 to PFM + depth, then sends validated pairs
# through the existing x86 Bridge to x64 Worker. It never saves the session or
# deletes the source PFM files.

import ctypes
import hashlib
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


ROOT = r"D:\WorkFlow\Programming\Projects\ModernSFM"
SFM_GAME = r"D:\SteamLibrary\steamapps\common\SourceFilmmaker\game"
SOURCE_SESSION = os.path.join(SFM_GAME, "usermod", "elements", "sessions", "session4.dmx")
SESSION_COPY = os.path.join(SFM_GAME, "usermod", "elements", "sessions",
                            "_modern_sfm_poc_copy.dmx")
BRIDGE_DLL = os.path.join(ROOT, "build", "x86", "Release", "modern_sfm_bridge.dll")
WORKER_EXE = os.path.join(ROOT, "build", "x64", "Release", "modern_sfm_worker.exe")
LOG = os.path.join(ROOT, "build", "sfm_sequence_export_poc.txt")
FRAME_START = 0
FRAME_END = 2
WIDTH = 1280
HEIGHT = 720
MAX_FRAMES = 3

_cancelled = [False]
_progress_seen = [False]
_cancel_connected = [False]
_quiet_signature = [None]
_quiet_since = [None]


def _log(text):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), text)
    print("ModernSFM POC: " + line)
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


def _error_text(library):
    buffer = ctypes.create_string_buffer(1024)
    result = library.last_error(buffer, len(buffer))
    return buffer.value if result == 0 else "Bridge error text unavailable (%d)" % result


def _check(library, code, operation):
    if code != 0:
        raise RuntimeError("%s failed (%d): %s" % (operation, code, _error_text(library)))


def _utf8_path(path):
    if isinstance(path, unicode):
        return path.encode("utf-8")
    return path.decode("mbcs").encode("utf-8")


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
    _check(bridge, bridge.start(os.path.abspath(WORKER_EXE), 10000), "Bridge start")
    return bridge


def _request(bridge, payload, timeout_ms=300000):
    request = ctypes.create_string_buffer(payload)
    response = ctypes.create_string_buffer(64 * 1024)
    response_size = ctypes.c_ulong(0)
    code = bridge.request(request, len(payload), response, len(response),
                          ctypes.byref(response_size), timeout_ms)
    _check(bridge, code, "Worker request")
    return response.raw[:response_size.value]


def _pack(bridge, beauty, depth, output):
    beauty_bytes, depth_bytes, output_bytes = map(_utf8_path, (beauty, depth, output))
    payload = struct.pack("<4sIIIQ", "PACK", len(beauty_bytes), len(depth_bytes),
                          len(output_bytes), 4096)
    response = _request(bridge, payload + beauty_bytes + depth_bytes + output_bytes)
    if len(response) < 12 or response[:4] != "RSLT":
        raise RuntimeError("malformed PACK response: %r" % response[:64])
    status, message_size = struct.unpack("<II", response[4:12])
    if message_size != len(response) - 12:
        raise RuntimeError("PACK response length mismatch")
    message = response[12:].decode("utf-8", "replace")
    _log("PACK status %d: %s" % (status, message))
    if status != 0:
        raise RuntimeError("Worker rejected PFM pair: %s" % message)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _top_widgets():
    app = QtGui.QApplication.instance()
    if app is None:
        return []
    widgets = []
    for candidate in app.topLevelWidgets():
        try:
            if not isinstance(candidate, QtGui.QWidget):
                candidate = shiboken.wrapInstance(shiboken.getCppPointer(candidate)[0],
                                                  QtGui.QWidget)
            if candidate.isVisible():
                widgets.append(candidate)
        except Exception:
            continue
    return widgets


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
    if check is None:
        raise RuntimeError("exporter checkbox %r is missing" % label)
    if not check.isEnabled():
        raise RuntimeError("exporter checkbox %r is disabled" % label)
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
        raise RuntimeError("could not uniquely find exporter field %r" % old_value)
    edit = matches[0]
    edit.setText(str(new_value))
    edit.editingFinished.emit()
    QtGui.QApplication.instance().processEvents()
    if str(edit.text()).strip() != str(new_value):
        raise RuntimeError("exporter field did not retain %r" % new_value)
    return edit


def _set_frame_range(dialog, start, end):
    numeric = []
    for edit in dialog.findChildren(QtGui.QLineEdit):
        try:
            float(str(edit.text()).strip())
            numeric.append(edit)
        except Exception:
            pass
    _log("numeric exporter fields after mode selection: %r" %
         [str(edit.text()) for edit in numeric])
    # Observed dialog order is width, height, start, end, output FPS,
    # shutter speed. Require all six before using the two range fields.
    if len(numeric) < 6:
        raise RuntimeError("expected six numeric exporter controls, found %d" % len(numeric))
    for edit, value in ((numeric[2], start), (numeric[3], end)):
        edit.setText(str(value))
        edit.editingFinished.emit()
        QtGui.QApplication.instance().processEvents()
        if str(edit.text()).strip() != str(value):
            raise RuntimeError("frame-range control refused %r" % value)


def _output_files(output_dir):
    if not os.path.isdir(output_dir):
        return [], []
    pfms = [os.path.join(output_dir, name) for name in os.listdir(output_dir)
            if name.lower().endswith(".pfm")]
    depths = [path for path in pfms if os.path.basename(path).lower().endswith("_depth.pfm")]
    beauties = [path for path in pfms if path not in depths]
    return beauties, depths


def _pair_frames(output_dir):
    beauties, depths = _output_files(output_dir)
    depth_by_base = {os.path.basename(path)[:-10].lower(): path for path in depths}
    pairs = []
    for beauty in beauties:
        base = os.path.basename(beauty)[:-4].lower()
        depth = depth_by_base.get(base)
        if depth:
            pairs.append((beauty, depth))
    return sorted(pairs)


def _wait_stable(paths, rounds=3, delay=0.75):
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
            if stable >= rounds:
                return True
        else:
            stable = 0
        previous = state
        time.sleep(delay)
    return False


def _poll_render(output_dir, started_at):
    if _cancelled[0]:
        _log("job status: cancelled; PFM inputs retained")
        return
    progress_open = False
    for widget in _top_widgets():
        class_name = str(widget.metaObject().className())
        title = str(widget.windowTitle())
        if "SettingsDialog" not in class_name and (
                "Layoff" in class_name or "Progress" in class_name or "Progress" in title):
            progress_open = True
            break
    pairs = _pair_frames(output_dir)
    beauties, depths = _output_files(output_dir)
    elapsed = time.time() - started_at
    if progress_open:
        _quiet_signature[0] = None
        _quiet_since[0] = None
        QtCore.QTimer.singleShot(750, lambda: _poll_render(output_dir, started_at))
        return
    if pairs:
        paths = [path for pair in pairs for path in pair]
        signature = []
        for path in paths:
            try:
                stat = os.stat(path)
                signature.append((path, stat.st_size, stat.st_mtime))
            except OSError:
                signature.append((path, None, None))
        if signature != _quiet_signature[0]:
            _quiet_signature[0] = signature
            _quiet_since[0] = time.time()
        quiet_for = time.time() - (_quiet_since[0] or time.time())
        enough_frames = len(pairs) == MAX_FRAMES
        quiet_required = 3 if enough_frames else 10
        if _wait_stable(paths, rounds=3, delay=0.75) and quiet_for >= quiet_required:
            _log("native exporter produced %d stable beauty/depth pair(s) after %.1fs" %
                 (len(pairs), elapsed))
            try:
                _process_render(output_dir)
            except Exception as error:
                _log("job status: failed during PFM validation/EXR processing: %s" % error)
            return
    if elapsed >= 60:
        _log("job status: failed; exporter did not produce a complete pair within 60 seconds; "
             "beauty=%d depth=%d; any PFMs retained" % (len(beauties), len(depths)))
        return
    QtCore.QTimer.singleShot(750, lambda: _poll_render(output_dir, started_at))


def _find_export_dialog():
    for widget in _top_widgets():
        if "CQMovieLayoffSettingsDialog" in widget.metaObject().className():
            return widget
    return None


def _watch_progress():
    found = False
    for widget in _top_widgets():
        class_name = str(widget.metaObject().className())
        title = str(widget.windowTitle())
        if "SettingsDialog" in class_name or not (
                "Layoff" in class_name or "Progress" in class_name or "Progress" in title):
            continue
        found = True
        if not _progress_seen[0]:
            _progress_seen[0] = True
            _log("native render progress window appeared: %s (%r)" %
                 (class_name, str(widget.windowTitle())))
        bars = widget.findChildren(QtGui.QProgressBar)
        if bars:
            _log("native progress = %d/%d" % (bars[0].value(), bars[0].maximum()))
        for button in widget.findChildren(QtGui.QPushButton):
            if str(button.text()).replace("&", "").strip().lower() == "cancel":
                if not _cancel_connected[0]:
                    _cancel_connected[0] = True
                    button.clicked.connect(lambda: _mark_cancelled())
    if found:
        QtCore.QTimer.singleShot(500, _watch_progress)


def _mark_cancelled():
    _cancelled[0] = True
    _log("render Cancel button was activated")


def _process_render(output_dir):
    if _cancelled[0]:
        _log("job status: cancelled; existing PFM files retained; Worker not started")
        return
    pairs = _pair_frames(output_dir)
    _log("render dialog returned; discovered %d complete beauty/depth filename pairs" % len(pairs))
    if not pairs:
        raise RuntimeError("export dialog closed without producing a PFM pair")
    if len(pairs) > MAX_FRAMES:
        raise RuntimeError("export produced %d frames; expected at most %d; PFMs retained" %
                           (len(pairs), MAX_FRAMES))
    paths = [path for pair in pairs for path in pair]
    if not _wait_stable(paths):
        raise RuntimeError("PFM files did not reach a stable size/mtime; PFMs retained")
    _log("all PFM sizes and timestamps remained stable across repeated checks")

    bridge = _load_bridge()
    try:
        for index, (beauty, depth) in enumerate(pairs):
            beauty_hash = _sha256(beauty)
            depth_hash = _sha256(depth)
            output = os.path.splitext(beauty)[0] + ".exr"
            _pack(bridge, beauty, depth, output)
            if not os.path.isfile(output) or not os.path.getsize(output):
                raise RuntimeError("Worker succeeded but EXR is missing or empty")
            if _sha256(beauty) != beauty_hash or _sha256(depth) != depth_hash:
                raise RuntimeError("a source PFM changed during conversion")
            _log("frame %d validated; EXR=%s; both PFMs retained and hash-identical" %
                 (index, output))
        _log("Worker status: completed for %d validated pair(s)" % len(pairs))
        if len(pairs) == MAX_FRAMES:
            _log("render status: all %d requested pairs are present and validated; native progress signal observed=%s" %
                 (MAX_FRAMES, _progress_seen[0]))
        else:
            _log("render status: incomplete or range semantics differ; %d of %d expected pairs found; "
                 "PFM/EXR values validated for available frames" % (len(pairs), MAX_FRAMES))
    finally:
        _check(bridge, bridge.shutdown(10000), "Bridge shutdown")


def _configure_and_render(output_dir):
    try:
        dialog = _find_export_dialog()
        if dialog is None:
            raise RuntimeError("native Export Movie settings dialog did not appear")

        _set_combo(_find_combo(dialog, ["Movie", "Image Sequence", "Sound"]), "Image Sequence")
        _set_combo(_find_combo(dialog, ["PFM", "PNG", "JPEG", "TGA"]), "PFM")
        _set_combo(_find_combo(dialog, ["Sequence", "Selected Shots", "Custom"]), "Custom")
        _set_combo(_find_combo(dialog, ["Seconds", "Frames"]), "Frames")
        _set_combo(_find_combo(dialog, ["Custom", "360p", "480p", "720p"]), "Custom")

        prefix = os.path.join(output_dir, "frame").replace("\\", "/")
        output_edits = [edit for edit in dialog.findChildren(QtGui.QLineEdit)
                        if "/" in str(edit.text()) or "\\" in str(edit.text())]
        if not output_edits:
            raise RuntimeError("could not identify Export Movie output path control")
        output_edits[0].setText(prefix)
        output_edits[0].editingFinished.emit()

        _set_line_edit(dialog, "1280", WIDTH)
        _set_line_edit(dialog, "720", HEIGHT)
        # The observed exporter produces [start, end) frames. Add one so the
        # ModernSFM-facing range remains inclusive.
        exporter_end = FRAME_END + 1
        _set_frame_range(dialog, FRAME_START, exporter_end)
        _set_check(dialog, "Write PFM depth file", True)
        _set_check(dialog, "Audio", False)
        _set_check(dialog, "Separate WAV file", False)
        _set_check(dialog, "Progressive Refinement", True)
        _set_check(dialog, "Depth Of Field", False)
        _set_check(dialog, "Motion Blur", False)

        _log("verified exporter settings: Image Sequence, PFM, inclusive range %d..%d translated to exporter range %d..%d Frames, %dx%d, depth on, audio off, progressive refinement on, DOF off, motion blur off" %
             (FRAME_START, FRAME_END, FRAME_START, exporter_end, WIDTH, HEIGHT))
        _log("output prefix: %s" % prefix)
        _log("light-sample override left at camera default because DOF is disabled")

        export_button = None
        for button in dialog.findChildren(QtGui.QPushButton):
            if str(button.text()).replace("&", "").strip() == "Export Movie" and button.isEnabled():
                export_button = button
                break
        if export_button is None:
            raise RuntimeError("enabled Export Movie button not found")
        QtCore.QTimer.singleShot(250, _watch_progress)
        _log("starting the guarded 1-3 frame native export")
        export_button.click()
    except Exception as error:
        _log("job status: failed before/while starting native export: %s" % error)
        for widget in _top_widgets():
            if "CQMovieLayoffSettingsDialog" in widget.metaObject().className():
                widget.reject()


def _trigger_native_export():
    try:
        main_window = sfmApp.GetMainWindow()
        for action in main_window.findChildren(QtGui.QAction):
            text = str(action.text()).replace("&", "").strip()
            name = str(action.objectName())
            if text == "Movie..." or "Export Movie" in text or "ExportMovie" in name:
                _log("launching native Export Movie action: %r (%s)" % (text, name))
                output_dir = os.path.join(ROOT, "build", "sfm_sequence_poc",
                                          "render_" + time.strftime("%Y%m%d_%H%M%S"))
                if not os.path.isdir(output_dir):
                    os.makedirs(output_dir)
                QtCore.QTimer.singleShot(1500, lambda: _configure_and_render(output_dir))
                action.trigger()
                _log("native exporter dialog returned to Python")
                QtCore.QTimer.singleShot(750, lambda: _poll_render(output_dir, time.time()))
                return
        raise RuntimeError("native Export Movie QAction not found")
    except Exception as error:
        _log("job status: failed to launch native exporter: %s" % error)


def run():
    try:
        if os.path.exists(LOG):
            os.remove(LOG)
        for path in (SESSION_COPY,):
            if os.path.exists(path):
                os.remove(path)
        if not os.path.isfile(SOURCE_SESSION):
            raise RuntimeError("disposable test source session not found: %s" % SOURCE_SESSION)
        shutil.copyfile(SOURCE_SESSION, SESSION_COPY)
        _log("created disposable session copy: %s" % SESSION_COPY)
        opened = sfmApp.OpenDocument(SESSION_COPY)
        _log("sfmApp.OpenDocument(disposable session) returned %r" % opened)
        QtCore.QTimer.singleShot(5000, _trigger_native_export)
    except Exception as error:
        _log("job status: failed during setup: %s" % error)


run()
