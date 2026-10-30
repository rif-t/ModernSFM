import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
from render_job_state import JobStateMachine, InvalidTransition, load_manifest
from render_job_state import recover_interrupted_jobs, write_manifest


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_invalid(machine, next_state):
    try:
        machine.transition(next_state)
    except InvalidTransition:
        return
    raise AssertionError("expected invalid transition to %s" % next_state)


def main():
    machine = JobStateMachine()
    expect_invalid(machine, "Rendering")
    for state in ("Queued", "Preparing", "Rendering", "Processing", "Completed",
                  "Queued", "Cancelling", "Cancelled", "Queued", "Preparing", "Failed"):
        machine.transition(state)
    expect_invalid(machine, "Processing")

    temp = tempfile.mkdtemp(prefix="modern_sfm_lifecycle_")
    try:
        job = os.path.join(temp, "job_test")
        os.makedirs(job)
        manifest_path = os.path.join(job, "job_status.json")
        write_manifest(manifest_path, {
            "job_id": "job_test", "state": "Rendering", "stage": "SFM rendering",
            "sfm_pid": 43210, "total_frames": 3, "exrs_generated": 0,
        })
        recovered = recover_interrupted_jobs(
            temp, 98765, is_alive=lambda pid: False)
        require(len(recovered) == 1, "stale nonterminal job was not recovered")
        result = load_manifest(manifest_path)
        require(result["state"] == "Failed", "unexpected process termination was not Failed")
        require(result["stage"] == "SFM process termination", "failure stage was not recorded")
        require("43210" in result["error"] and "SFM rendering" in result["error"],
                "termination diagnostic omitted PID or prior stage")

        write_manifest(manifest_path, dict(result, state="Completed", stage="Completed"))
        recovered = recover_interrupted_jobs(
            temp, 98765, is_alive=lambda pid: False)
        require(not recovered, "terminal job was incorrectly recovered as a crash")
        require(load_manifest(manifest_path)["state"] == "Completed",
                "terminal job manifest changed during recovery")

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            require(not recover_interrupted_jobs(temp, os.getpid()),
                    "live process fixture unexpectedly altered terminal manifest")
            require(load_manifest(manifest_path)["state"] == "Completed",
                    "live process fixture changed a terminal manifest")
            write_manifest(manifest_path, {
                "job_id": "job_test", "state": "Processing", "stage": "Worker processing",
                "sfm_pid": child.pid, "total_frames": 3, "exrs_generated": 1,
            })
            require(not recover_interrupted_jobs(temp, os.getpid()),
                    "running process was incorrectly marked as terminated")
        finally:
            child.terminate()
            child.wait()
        recovered = recover_interrupted_jobs(temp, os.getpid())
        require(len(recovered) == 1, "terminated process was not recovered")
        result = load_manifest(manifest_path)
        require(result["state"] == "Failed" and
                result["stage"] == "SFM process termination",
                "terminated process recovery did not report its failure stage")
    finally:
        shutil.rmtree(temp)

    print("Render Job lifecycle smoke checks passed")


if __name__ == "__main__":
    main()
