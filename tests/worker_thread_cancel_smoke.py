# Run with the bundled x86 Python 2.7 that loads PySide and the Bridge DLL.

import hashlib
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
from render_job_processing import ExrProcessingThread
from PySide import QtCore, QtGui


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class FakeBridge(object):
    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self, timeout):
        self.shutdown_calls += 1
        return 0


def hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        digest.update(stream.read())
    return digest.hexdigest()


def wait_for_thread(app, thread, terminal, timeout=15.0):
    deadline = time.time() + timeout
    while not terminal and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    thread.wait(1000)
    app.processEvents()
    require(bool(terminal), "Worker thread did not report a terminal result")


def main():
    app = QtGui.QApplication.instance() or QtGui.QApplication([])
    temp = tempfile.mkdtemp(prefix="modern_sfm_worker_cancel_")
    try:
        pairs = []
        for index in range(3):
            beauty = os.path.join(temp, "frame%d.pfm" % index)
            depth = os.path.join(temp, "frame%d_depth.pfm" % index)
            with open(beauty, "wb") as stream:
                stream.write(b"beauty-%d" % index)
            with open(depth, "wb") as stream:
                stream.write(b"depth-%d" % index)
            pairs.append((beauty, depth))

        bridge = FakeBridge()
        submitted = []
        terminal = []
        thread = None

        def slow_pack(unused_bridge, beauty, depth, output, unused_memory):
            submitted.append((beauty, depth))
            time.sleep(0.20)  # emulate one in-flight Worker PACK request
            with open(output, "wb") as stream:
                stream.write(b"validated-exr")
            return "validated %s" % os.path.basename(output)

        thread = ExrProcessingThread(pairs, 64, lambda: bridge, slow_pack, hash_file)
        thread.frame_started.connect(lambda frame, total: thread.request_cancel())
        thread.result_ready.connect(lambda *args: terminal.append(args))
        thread.start()
        wait_for_thread(app, thread, terminal)
        outcome, completed, stage, error = terminal[0]
        require(outcome == "Cancelled", "mid-PACK cancellation was not reported as Cancelled: %r" %
                (terminal[0],))
        require(completed == 1, "current frame did not finish before cancellation")
        require(len(submitted) == 1, "a new frame was submitted after cancellation")
        require(os.path.isfile(os.path.splitext(pairs[0][0])[0] + ".exr"),
                "the in-flight EXR was not retained")
        require(not os.path.exists(os.path.splitext(pairs[1][0])[0] + ".exr"),
                "a later frame was written after cancellation")
        require(bridge.shutdown_calls == 1, "Worker was not shut down after cancellation")

        failure_bridge = FakeBridge()
        failure_result = []

        def fail_pack(unused_bridge, unused_beauty, unused_depth, unused_output, unused_memory):
            raise RuntimeError("injected Worker failure")

        failure = ExrProcessingThread(pairs, 64, lambda: failure_bridge, fail_pack, hash_file)
        failure.result_ready.connect(lambda *args: failure_result.append(args))
        failure.start()
        wait_for_thread(app, failure, failure_result)
        require(failure_result[0][0] == "Failed", "Worker exception was not reported as Failed")
        require(failure_result[0][2] == "Worker processing frame 1/3",
                "Worker failure stage was not specific: %r" % (failure_result[0],))
        require("injected Worker failure" in failure_result[0][3], "Worker diagnostic was lost")
        require(failure_bridge.shutdown_calls == 1, "Worker was not shut down after failure")
    finally:
        shutil.rmtree(temp)
    print("Worker thread cancellation/failure smoke checks passed")
    sys.stdout.flush()
    # Keep Qt wrapper locals alive until immediate process exit. The standalone
    # bundled PySide runtime faults while tearing them down; SFM itself remains
    # alive and does not tear QApplication down between scripts.
    os._exit(0)


if __name__ == "__main__":
    main()
