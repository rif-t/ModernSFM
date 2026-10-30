# Run with SFM's bundled x86 Python 2.7; exercises actual Bridge + x64 Worker.

import ctypes
import hashlib
import os
import shutil
import struct
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from render_job_processing import ExrProcessingThread
from PySide import QtGui


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BRIDGE_PATH = os.path.join(ROOT, "build", "x86", "Release", "modern_sfm_bridge.dll")
WORKER_PATH = os.path.join(ROOT, "build", "x64", "Release", "modern_sfm_worker.exe")
BEAUTY_SOURCE = os.path.join(ROOT, "test000240.pfm")
DEPTH_SOURCE = os.path.join(ROOT, "test000240_depth.pfm")


def symbol(library, name):
    try:
        return getattr(library, name)
    except AttributeError:
        return getattr(library, "_" + name)


def load_bridge():
    bridge = ctypes.CDLL(BRIDGE_PATH)
    bridge.start = symbol(bridge, "msfm_start")
    bridge.start.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong]
    bridge.start.restype = ctypes.c_int
    bridge.request = symbol(bridge, "msfm_request")
    bridge.request.argtypes = [ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p,
                               ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
    bridge.request.restype = ctypes.c_int
    bridge.shutdown = symbol(bridge, "msfm_shutdown")
    bridge.shutdown.argtypes = [ctypes.c_ulong]
    bridge.shutdown.restype = ctypes.c_int
    bridge.last_error = symbol(bridge, "msfm_last_error")
    bridge.last_error.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    bridge.last_error.restype = ctypes.c_int
    result = bridge.start(os.path.abspath(WORKER_PATH), 10000)
    if result:
        message = ctypes.create_string_buffer(1024)
        bridge.last_error(message, len(message))
        raise RuntimeError("Bridge start failed (%d): %s" % (result, message.value))
    return bridge


def pack(bridge, beauty, depth, output, memory_mib):
    encode = lambda path: path.decode("mbcs").encode("utf-8")
    beauty_b, depth_b, output_b = map(encode, (beauty, depth, output))
    payload = struct.pack("<4sIIIQ", "PACK", len(beauty_b), len(depth_b),
                          len(output_b), memory_mib) + beauty_b + depth_b + output_b
    request = ctypes.create_string_buffer(payload)
    response = ctypes.create_string_buffer(64 * 1024)
    size = ctypes.c_ulong(0)
    result = bridge.request(request, len(payload), response, len(response),
                            ctypes.byref(size), 300000)
    if result:
        message = ctypes.create_string_buffer(1024)
        bridge.last_error(message, len(message))
        raise RuntimeError("Bridge PACK failed (%d): %s" % (result, message.value))
    raw = response.raw[:size.value]
    if len(raw) < 12 or raw[:4] != "RSLT":
        raise RuntimeError("malformed Worker response")
    status, length = struct.unpack("<II", raw[4:12])
    message = raw[12:].decode("utf-8", "replace")
    if length != len(raw) - 12 or status:
        raise RuntimeError("Worker rejected PACK: %s" % message)
    return message


def hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def main():
    for path in (BRIDGE_PATH, WORKER_PATH, BEAUTY_SOURCE, DEPTH_SOURCE):
        if not os.path.isfile(path):
            raise RuntimeError("required test input is missing: %s" % path)
    app = QtGui.QApplication.instance() or QtGui.QApplication([])
    source_hashes = (hash_file(BEAUTY_SOURCE), hash_file(DEPTH_SOURCE))
    temp = tempfile.mkdtemp(prefix="modern_sfm_live_cancel_")
    try:
        pairs = []
        for index in range(2):
            beauty = os.path.join(temp, "frame%d.pfm" % index)
            depth = os.path.join(temp, "frame%d_depth.pfm" % index)
            shutil.copyfile(BEAUTY_SOURCE, beauty)
            shutil.copyfile(DEPTH_SOURCE, depth)
            pairs.append((beauty, depth))

        terminal = []
        thread = ExrProcessingThread(pairs, 4096, load_bridge, pack, hash_file)
        thread.frame_started.connect(lambda frame, total: thread.request_cancel())
        thread.result_ready.connect(lambda *args: terminal.append(args))
        thread.start()
        deadline = time.time() + 120
        while not terminal and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        thread.wait(5000)
        app.processEvents()
        if not terminal:
            raise RuntimeError("live Worker cancellation test timed out")
        outcome, completed, stage, error = terminal[0]
        if outcome != "Cancelled" or completed != 1:
            raise RuntimeError("expected current PACK to finish then cancel; got %r" %
                               (terminal[0],))
        first_output = os.path.splitext(pairs[0][0])[0] + ".exr"
        second_output = os.path.splitext(pairs[1][0])[0] + ".exr"
        if not os.path.isfile(first_output) or not os.path.getsize(first_output):
            raise RuntimeError("first in-flight EXR was not retained")
        if os.path.exists(second_output):
            raise RuntimeError("a later frame was submitted after cancellation")
        if source_hashes != (hash_file(BEAUTY_SOURCE), hash_file(DEPTH_SOURCE)):
            raise RuntimeError("source sample PFM changed")
        print("Live Worker cancellation passed: one real 3840x2160 PACK finished, "
              "second frame was not submitted, and PFMs were retained")
        sys.stdout.flush()
    finally:
        shutil.rmtree(temp)
    # Bundled standalone PySide may fault during QApplication teardown.
    os._exit(0)


if __name__ == "__main__":
    main()
