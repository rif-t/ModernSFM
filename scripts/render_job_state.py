# -*- coding: utf-8 -*-
"""Small Python 2.7-compatible Render Job state and journal helpers."""

import json
import os
import time


STATES = ("Idle", "Queued", "Preparing", "Rendering", "Processing",
          "Cancelling", "Cancelled", "Completed", "Failed")

TRANSITIONS = {
    "Idle": ("Queued",),
    "Queued": ("Preparing", "Cancelling", "Failed"),
    "Preparing": ("Rendering", "Cancelling", "Failed"),
    "Rendering": ("Processing", "Cancelling", "Failed"),
    "Processing": ("Cancelling", "Completed", "Failed"),
    "Cancelling": ("Cancelled", "Failed"),
    "Cancelled": ("Queued", "Idle"),
    "Completed": ("Queued", "Idle"),
    "Failed": ("Queued", "Idle"),
}

TERMINAL_STATES = ("Cancelled", "Completed", "Failed")


class InvalidTransition(ValueError):
    pass


class JobStateMachine(object):
    def __init__(self, state="Idle"):
        if state not in STATES:
            raise ValueError("unknown Render Job state: %r" % state)
        self.state = state

    def transition(self, next_state):
        if next_state not in TRANSITIONS.get(self.state, ()):
            raise InvalidTransition("invalid Render Job transition %s -> %s" %
                                    (self.state, next_state))
        self.state = next_state
        return self.state


def process_is_alive(pid):
    """Return True/False when Windows can answer, None when access is denied."""
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False
    import ctypes
    kernel32 = ctypes.windll.kernel32
    synchronize = 0x00100000
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(synchronize, False, int(pid))
    if not handle:
        error = ctypes.GetLastError()
        # ERROR_INVALID_PARAMETER means the PID no longer exists. Other errors
        # (notably access denied) are inconclusive and must not be called a crash.
        return False if error == 87 else None
    try:
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == 0x00000102:  # WAIT_TIMEOUT: process is still running
            return True
        if result == 0:  # WAIT_OBJECT_0: process has exited
            return False
        return None
    finally:
        kernel32.CloseHandle(handle)


def write_manifest(path, manifest):
    """Replace a JSON job journal without exposing a half-written document."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    temporary = path + ".tmp-%d-%d" % (os.getpid(), int(time.time() * 1000))
    with open(temporary, "wb") as stream:
        payload = json.dumps(manifest, sort_keys=True, indent=2)
        if not isinstance(payload, bytes):
            payload = payload.encode("utf-8")
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "nt" and os.path.exists(path):
        import ctypes
        move = ctypes.windll.kernel32.MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        move.restype = ctypes.c_int
        if not move(temporary, path, 0x1 | 0x8):  # REPLACE_EXISTING | WRITE_THROUGH
            error = ctypes.GetLastError()
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise WindowsError(error, "MoveFileExW failed writing Render Job journal")
    else:
        if os.path.exists(path):
            os.remove(path)
        os.rename(temporary, path)


def load_manifest(path):
    with open(path, "rb") as stream:
        return json.loads(stream.read().decode("utf-8"))


def recover_interrupted_jobs(root, current_pid, is_alive=process_is_alive):
    """Mark stale nonterminal jobs failed after their owning SFM process exits."""
    recovered = []
    if not os.path.isdir(root):
        return recovered
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        path = os.path.join(folder, "job_status.json")
        if not os.path.isdir(folder) or not os.path.isfile(path):
            continue
        try:
            manifest = load_manifest(path)
            state = manifest.get("state")
            pid = int(manifest.get("sfm_pid", 0))
            if state in TERMINAL_STATES or state in (None, "Idle") or pid == int(current_pid):
                continue
            alive = is_alive(pid)
            if alive is not False:
                continue
            old_stage = manifest.get("stage") or state
            manifest["state"] = "Failed"
            manifest["stage"] = "SFM process termination"
            manifest["error"] = "SFM process %d terminated unexpectedly during %s" % (pid, old_stage)
            manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            write_manifest(path, manifest)
            recovered.append((path, manifest["error"]))
        except Exception:
            # A malformed journal is evidence to retain, but is not safe to
            # rewrite as a definitive process crash.
            continue
    return recovered
