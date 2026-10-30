# Run inside SFM's Python console with:
# execfile(r"D:\WorkFlow\Programming\Projects\ModernSFM\scripts\sfm_bridge_smoke.py")
#
# This script intentionally uses only Python 2.7-compatible syntax and ctypes.

import ctypes
import os
import struct
import sys
import traceback


SCRIPT_FILE = globals().get("__file__", "")
if SCRIPT_FILE:
    REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(SCRIPT_FILE)))
else:
    _config_path = os.environ.get("MODERNSFM_CONFIG")
    if not _config_path and os.environ.get("APPDATA"):
        _config_path = os.path.join(os.environ["APPDATA"], "ModernSFM", "config.json")
    _config = {}
    if _config_path and os.path.isfile(_config_path):
        import json
        with open(_config_path, "rb") as _config_stream:
            _config = json.load(_config_stream)
    REPO_DIR = (os.environ.get("MODERNSFM_ROOT") or
                _config.get("project_root"))
    if not REPO_DIR:
        raise RuntimeError("Set MODERNSFM_ROOT or install ModernSFM with setup.ps1")
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


def _request(library, message, timeout_ms=5000):
    request = ctypes.create_string_buffer(message)
    response = ctypes.create_string_buffer(64 * 1024)
    response_size = ctypes.c_ulong(0)
    result = library.request(request, len(message), response, len(response),
                             ctypes.byref(response_size), timeout_ms)
    _check(library, result, "Worker request %r" % message)
    return response.raw[:response_size.value]


def _utf8_path(path):
    if isinstance(path, unicode):
        return path.encode("utf-8")
    return path.decode("mbcs").encode("utf-8")


def _pack(library, beauty, depth, output):
    beauty_bytes = _utf8_path(beauty)
    depth_bytes = _utf8_path(depth)
    output_bytes = _utf8_path(output)
    payload = struct.pack("<4sIIIQ", "PACK", len(beauty_bytes),
                          len(depth_bytes), len(output_bytes), 4096)
    payload += beauty_bytes + depth_bytes + output_bytes
    response = _request(library, payload, 300000)
    if len(response) < 12 or response[:4] != "RSLT":
        raise RuntimeError("Malformed PACK result: %r" % response[:64])
    status, message_size = struct.unpack("<II", response[4:12])
    if message_size != len(response) - 12:
        raise RuntimeError("PACK result message length is invalid")
    message = response[12:].decode("utf-8", "replace")
    _report("PACK status %d: %s" % (status, message))
    if status != 0:
        raise RuntimeError("Worker PACK failed: %s" % message)
    return message


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260)]


def _terminate_test_worker_child():
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        raise RuntimeError("could not snapshot processes to find the test Worker")
    entry = _ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(entry)
    worker_pid = None
    try:
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                if (entry.th32ParentProcessID == os.getpid() and
                        entry.szExeFile.lower() == "modern_sfm_worker.exe"):
                    worker_pid = int(entry.th32ProcessID)
                    break
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    if worker_pid is None:
        raise RuntimeError("could not identify the test Worker child process")
    handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, worker_pid)
    if not handle:
        raise RuntimeError("could not open test Worker process %d" % worker_pid)
    try:
        if not kernel32.TerminateProcess(handle, 23):
            raise RuntimeError("could not terminate test Worker process %d" % worker_pid)
        if kernel32.WaitForSingleObject(handle, 5000) != 0:
            raise RuntimeError("test Worker process %d did not exit" % worker_pid)
    finally:
        kernel32.CloseHandle(handle)
    return worker_pid


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

        missing_beauty = os.path.join(repo_dir, "build", "missing_input_%d.pfm" % os.getpid())
        missing_depth = os.path.join(repo_dir, "build", "missing_depth_%d.pfm" % os.getpid())
        missing_output = os.path.join(repo_dir, "build", "missing_input_%d.exr" % os.getpid())
        try:
            _pack(bridge, missing_beauty, missing_depth, missing_output)
            raise RuntimeError("Worker unexpectedly accepted missing PFM inputs")
        except RuntimeError as error:
            if "Worker PACK failed" not in str(error):
                raise
            _report("Expected Worker job failure returned through Bridge: %s" % error)
        if _request(bridge, "PING") != "PONG":
            raise RuntimeError("Worker did not remain usable after a rejected PACK")
        if os.path.exists(missing_output):
            raise RuntimeError("failed PACK published an EXR")

        beauty = os.path.join(repo_dir, "test000240.pfm")
        depth = os.path.join(repo_dir, "test000240_depth.pfm")
        output = os.path.join(repo_dir, "build", "test000240_bridge_sfm_%d.exr" % os.getpid())
        if not os.path.isfile(beauty) or not os.path.isfile(depth):
            raise RuntimeError("Expected real SFM PFM sample pair was not found")
        _report("Processing real SFM PFM pair through Bridge -> Worker")
        pack_message = _pack(bridge, beauty, depth, output)
        if not os.path.isfile(output) or os.path.getsize(output) == 0:
            raise RuntimeError("Worker reported success but output EXR is missing or empty")
        if not os.path.isfile(beauty) or not os.path.isfile(depth):
            raise RuntimeError("Worker unexpectedly removed a source PFM")
        _report("Output EXR verified (%d bytes); both source PFMs remain." %
                os.path.getsize(output))

        _check(bridge, bridge.shutdown(5000), "Bridge shutdown")
        started = False
        _report("Worker shut down cleanly; Bridge and real-frame smoke test passed.")

        _check(bridge, bridge.start(os.path.abspath(worker_path), 10000),
               "Bridge restart before Worker-exit test")
        started = True
        worker_pid = _terminate_test_worker_child()
        request = ctypes.create_string_buffer("PING")
        response = ctypes.create_string_buffer(64 * 1024)
        response_size = ctypes.c_ulong(0)
        exit_result = bridge.request(request, 4, response, len(response),
                                     ctypes.byref(response_size), 5000)
        if exit_result == 0:
            raise RuntimeError("Bridge accepted a request after Worker %d exited" % worker_pid)
        _report("Expected Worker process exit detected by Bridge (%d): %s" %
                (exit_result, _last_error(bridge)))
        started = False  # Bridge reset its session when it detected the dead process.
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
