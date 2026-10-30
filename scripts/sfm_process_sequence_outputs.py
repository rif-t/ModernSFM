# Process a completed SFM PFM sequence through the existing Bridge/Worker.
# Run with MODERNSFM_SEQUENCE_DIR set, or edit DEFAULT_SEQUENCE_DIR.

import ctypes
import json
import os
import struct
import sys
import time
import traceback


_config_path = os.environ.get("MODERNSFM_CONFIG")
if not _config_path and os.environ.get("APPDATA"):
    _config_path = os.path.join(os.environ["APPDATA"], "ModernSFM", "config.json")
_config = {}
if _config_path and os.path.isfile(_config_path):
    with open(_config_path, "rb") as _config_stream:
        _config = json.load(_config_stream)
_script_file = globals().get("__file__", "")
ROOT = (os.environ.get("MODERNSFM_ROOT") or _config.get("project_root") or
        (os.path.dirname(os.path.dirname(os.path.abspath(_script_file))) if _script_file else ""))
if not ROOT:
    raise RuntimeError("Run setup.ps1 or set MODERNSFM_ROOT")
DEFAULT_SEQUENCE_DIR = os.path.join(ROOT, "build", "sfm_sequence_poc")
SEQUENCE_DIR = os.environ.get("MODERNSFM_SEQUENCE_DIR", DEFAULT_SEQUENCE_DIR)
BRIDGE_DLL = os.path.join(ROOT, "build", "x86", "Release", "modern_sfm_bridge.dll")
WORKER_EXE = os.path.join(ROOT, "build", "x64", "Release", "modern_sfm_worker.exe")
LOG = os.path.join(ROOT, "build", "sfm_sequence_worker_result.txt")


def _log(message):
    print("ModernSFM sequence Worker: " + message)
    try:
        parent = os.path.dirname(LOG)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(LOG, "a") as stream:
            stream.write(message + "\n")
    except Exception:
        pass


def _symbol(library, name):
    try:
        return getattr(library, name)
    except AttributeError:
        return getattr(library, "_" + name)


def _path_bytes(path):
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


def _pack(bridge, beauty, depth, output):
    beauty_b, depth_b, output_b = map(_path_bytes, (beauty, depth, output))
    payload = struct.pack("<4sIIIQ", "PACK", len(beauty_b), len(depth_b),
                          len(output_b), 4096)
    response = _request(bridge, payload + beauty_b + depth_b + output_b)
    if len(response) < 12 or response[:4] != "RSLT":
        raise RuntimeError("malformed PACK response: %r" % response[:64])
    status, size = struct.unpack("<II", response[4:12])
    if size != len(response) - 12:
        raise RuntimeError("PACK response length mismatch")
    message = response[12:].decode("utf-8", "replace")
    if status != 0:
        raise RuntimeError("Worker rejected input: %s" % message)
    return message


def _ready(path):
    previous = None
    stable = 0
    for unused in range(20):
        try:
            stat = os.stat(path)
            current = (stat.st_size, stat.st_mtime)
            if stat.st_size > 0 and current == previous:
                stable += 1
                if stable >= 3:
                    return True
            else:
                stable = 0
            previous = current
        except OSError:
            stable = 0
            previous = None
        time.sleep(0.5)
    return False


def _pairs(directory):
    if not os.path.isdir(directory):
        raise RuntimeError("sequence output directory is missing: " + directory)
    files = [os.path.join(directory, name) for name in os.listdir(directory)
             if name.lower().endswith(".pfm")]
    depth_files = [path for path in files if os.path.basename(path).lower().endswith("_depth.pfm")]
    depth_by_base = {os.path.basename(path)[:-10].lower(): path for path in depth_files}
    result = []
    for beauty in files:
        if beauty in depth_files:
            continue
        depth = depth_by_base.get(os.path.basename(beauty)[:-4].lower())
        if depth:
            result.append((beauty, depth))
    return sorted(result)


def run():
    if os.path.exists(LOG):
        os.remove(LOG)
    _log("Python %s, %d-bit; sequence directory: %s" %
         (sys.version.split()[0], ctypes.sizeof(ctypes.c_void_p) * 8, SEQUENCE_DIR))
    pairs = _pairs(SEQUENCE_DIR)
    if not pairs:
        raise RuntimeError("no complete beauty/depth filename pairs found")
    _log("found %d Beauty/Depth pair(s)" % len(pairs))
    bridge = _load_bridge()
    try:
        for index, pair in enumerate(pairs):
            beauty, depth = pair
            if not _ready(beauty) or not _ready(depth):
                raise RuntimeError("PFM files did not stabilize: %s / %s" % pair)
            output = os.path.splitext(beauty)[0] + ".exr"
            result = _pack(bridge, beauty, depth, output)
            if not os.path.isfile(output) or not os.path.getsize(output):
                raise RuntimeError("Worker reported success but EXR is missing")
            if not os.path.isfile(beauty) or not os.path.isfile(depth):
                raise RuntimeError("Worker unexpectedly removed source PFMs")
            _log("frame %d: %s; output=%s (%d bytes); source PFMs retained" %
                 (index, result, output, os.path.getsize(output)))
        _log("Worker processing completed for all %d pair(s)" % len(pairs))
    finally:
        result = bridge.shutdown(10000)
        if result != 0:
            raise RuntimeError("Bridge shutdown failed (%d): %s" % (result, _last_error(bridge)))


try:
    run()
except Exception:
    _log("FAILED\n" + traceback.format_exc())
