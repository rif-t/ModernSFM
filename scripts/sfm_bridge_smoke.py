# Run inside SFM's Python console with:
# execfile(r"D:\WorkFlow\Programming\Projects\ModernSFM\scripts\sfm_bridge_smoke.py")
#
# This script intentionally uses only Python 2.7-compatible syntax and ctypes.

import ctypes
import os
import sys
import traceback


SCRIPT_FILE = globals().get("__file__", "")
if SCRIPT_FILE:
    REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(SCRIPT_FILE)))
else:
    REPO_DIR = os.environ.get("MODERNSFM_ROOT",
                              r"D:\WorkFlow\Programming\Projects\ModernSFM")
RESULT_FILE = os.path.join(REPO_DIR, "build", "sfm_bridge_smoke_result.txt")


def _report(message):
    print(message)
    try:
        output_dir = os.path.dirname(RESULT_FILE)
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)
        with open(RESULT_FILE, "a") as result_file:
            result_file.write(message + "\n")
    except Exception:
        pass


def _symbol(library, name):
    try:
        return getattr(library, name)
    except AttributeError:
        # MSVC decorates __cdecl C exports with a leading underscore on x86.
        return getattr(library, "_" + name)


def _last_error(library):
    buffer = ctypes.create_string_buffer(1024)
    result = library.last_error(buffer, len(buffer))
    if result != 0:
        return "could not read Bridge error text (code %d)" % result
    return buffer.value


def _check(library, result, operation):
    if result != 0:
        raise RuntimeError("%s failed (%d): %s" %
                           (operation, result, _last_error(library)))


def _request(library, message):
    request = ctypes.create_string_buffer(message)
    response = ctypes.create_string_buffer(64 * 1024)
    response_size = ctypes.c_ulong(0)
    result = library.request(request, len(message), response, len(response),
                             ctypes.byref(response_size), 5000)
    _check(library, result, "Worker request %r" % message)
    return response.raw[:response_size.value]


def run(bridge_path=None, worker_path=None):
    repo_dir = REPO_DIR
    if bridge_path is None:
        bridge_path = os.path.join(repo_dir, "build", "x86", "Release",
                                   "modern_sfm_bridge.dll")
    if worker_path is None:
        worker_path = os.path.join(repo_dir, "build", "x64", "Release",
                                   "modern_sfm_worker.exe")

    _report("Python: %s (%d-bit)" %
            (sys.version.split()[0], ctypes.sizeof(ctypes.c_void_p) * 8))
    _report("Bridge: %s" % bridge_path)
    _report("Worker: %s" % worker_path)

    bridge = ctypes.CDLL(bridge_path)
    bridge.abi_version = _symbol(bridge, "msfm_abi_version")
    bridge.abi_version.argtypes = []
    bridge.abi_version.restype = ctypes.c_int
    bridge.start = _symbol(bridge, "msfm_start")
    bridge.start.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong]
    bridge.start.restype = ctypes.c_int
    bridge.request = _symbol(bridge, "msfm_request")
    bridge.request.argtypes = [ctypes.c_char_p, ctypes.c_ulong,
                               ctypes.c_void_p, ctypes.c_ulong,
                               ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
    bridge.request.restype = ctypes.c_int
    bridge.shutdown = _symbol(bridge, "msfm_shutdown")
    bridge.shutdown.argtypes = [ctypes.c_ulong]
    bridge.shutdown.restype = ctypes.c_int
    bridge.last_error = _symbol(bridge, "msfm_last_error")
    bridge.last_error.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    bridge.last_error.restype = ctypes.c_int

    version = bridge.abi_version()
    if version != 1:
        raise RuntimeError("Unsupported Bridge ABI version: %d" % version)

    started = False
    try:
        missing_worker = os.path.join(repo_dir, "build", "worker_path_does_not_exist.exe")
        bad_start_result = bridge.start(missing_worker, 1000)
        if bad_start_result == 0:
            bridge.shutdown(1000)
            raise RuntimeError("Bridge unexpectedly started a missing Worker")
        _report("Expected invalid-worker-path error %d: %s" %
                (bad_start_result, _last_error(bridge)))

        _check(bridge, bridge.start(os.path.abspath(worker_path), 10000),
               "Bridge start")
        started = True
        ping_response = _request(bridge, "PING")
        _report("PING -> %s" % ping_response)
        if ping_response != "PONG":
            raise RuntimeError("Unexpected PING response: %r" % ping_response)

        echo_message = "ECHO ModernSFM x86-to-x64 IPC works"
        echo_response = _request(bridge, echo_message)
        _report("%s -> %s" % (echo_message, echo_response))
        if echo_response != echo_message[5:]:
            raise RuntimeError("Unexpected ECHO response: %r" % echo_response)

        _check(bridge, bridge.shutdown(5000), "Bridge shutdown")
        started = False
        _report("Worker shut down cleanly; Bridge IPC smoke test passed.")
    finally:
        if started:
            bridge.shutdown(1000)


try:
    if os.path.exists(RESULT_FILE):
        os.remove(RESULT_FILE)
    run()
except Exception:
    _report("Bridge IPC smoke test FAILED")
    _report(traceback.format_exc())
