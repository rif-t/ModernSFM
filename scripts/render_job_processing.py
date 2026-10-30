# -*- coding: utf-8 -*-
"""Responsive x86 orchestration thread for per-frame Bridge PACK calls."""

from PySide import QtCore


_QtSignal = getattr(QtCore, "Signal", None) or getattr(QtCore, "pyqtSignal", None)
if _QtSignal is None:
    raise RuntimeError("PySide does not expose Qt signals needed for Worker cancellation")


class ExrProcessingThread(QtCore.QThread):
    frame_started = _QtSignal(int, int)
    frame_validated = _QtSignal(int, int, str)
    result_ready = _QtSignal(str, int, str, str)

    def __init__(self, pairs, memory_mib, bridge_loader, pack_request,
                 hash_file, parent=None):
        QtCore.QThread.__init__(self, parent)
        self._pairs = list(pairs)
        self._memory_mib = memory_mib
        self._bridge_loader = bridge_loader
        self._pack_request = pack_request
        self._hash_file = hash_file
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def run(self):
        bridge = None
        completed = 0
        outcome = "Completed"
        failed_stage = ""
        error_text = ""
        try:
            failed_stage = "Worker startup"
            bridge = self._bridge_loader()
            for index, pair in enumerate(self._pairs):
                if self._cancel_requested:
                    outcome = "Cancelled"
                    break
                beauty, depth = pair
                frame_number = index + 1
                failed_stage = "Worker processing frame %d/%d" % (frame_number, len(self._pairs))
                self.frame_started.emit(frame_number, len(self._pairs))
                before = (self._hash_file(beauty), self._hash_file(depth))
                output = self._output_path(beauty)
                message = self._pack_request(bridge, beauty, depth, output, self._memory_mib)
                if not self._is_nonempty_file(output):
                    raise RuntimeError("Worker returned without a nonempty EXR")
                after = (self._hash_file(beauty), self._hash_file(depth))
                if before != after:
                    raise RuntimeError("source PFM changed during Worker processing")
                completed += 1
                self.frame_validated.emit(frame_number, len(self._pairs), message)
                if self._cancel_requested:
                    outcome = "Cancelled"
                    break
        except Exception as error:
            outcome = "Failed"
            error_text = str(error)
        finally:
            if bridge is not None:
                try:
                    result = bridge.shutdown(10000)
                    if result != 0:
                        raise RuntimeError("Bridge shutdown failed (%d)" % result)
                except Exception as error:
                    outcome = "Failed"
                    failed_stage = "Worker shutdown"
                    error_text = str(error)
        self.result_ready.emit(outcome, completed, failed_stage, error_text)

    @staticmethod
    def _output_path(beauty):
        import os
        return os.path.splitext(beauty)[0] + ".exr"

    @staticmethod
    def _is_nonempty_file(path):
        import os
        return os.path.isfile(path) and os.path.getsize(path) > 0
