import math
import os
import struct
import subprocess
import sys
import tempfile


def write_pfm(path, magic, width, height, channels, values, scale=-1.0):
    with open(path, "wb") as stream:
        stream.write((magic + "\n").encode("ascii"))
        stream.write(("%d %d\n" % (width, height)).encode("ascii"))
        stream.write(("%.1f\n" % scale).encode("ascii"))
        # PFM stores rows bottom-to-top; the fixture values are top-to-bottom.
        row_size = width * channels
        for y in range(height - 1, -1, -1):
            row = values[y * row_size:(y + 1) * row_size]
            stream.write(struct.pack("<%df" % len(row), *row))


def run_worker(worker, beauty, depth, output, *extra):
    return subprocess.run(
        [worker, "pack", "--beauty", beauty, "--depth", depth, "--output", output] + list(extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    worker = os.path.abspath(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="modern_sfm_worker_") as temp:
        beauty = os.path.join(temp, "beauty.pfm")
        depth = os.path.join(temp, "depth.pfm")
        output = os.path.join(temp, "frame.exr")
        beauty_values = [
            0.0, 0.5, 1.0,  2.0, 4.0, 8.0,
            -0.25, 0.125, 3.5,  16.0, 32.0, 64.0,
        ]
        depth_values = [1.0, 2.0, 3.0, 4.0]
        write_pfm(beauty, "PF", 2, 2, 3, beauty_values)
        write_pfm(depth, "Pf", 2, 2, 1, depth_values)

        result = run_worker(worker, beauty, depth, output)
        require(result.returncode == 0, "valid fixture failed:\n" + result.stderr)
        require(os.path.isfile(output) and os.path.getsize(output) > 0, "EXR output missing or empty")
        require(os.path.isfile(beauty) and os.path.isfile(depth), "worker deleted source PFM inputs")
        require("raw-sfm.R/G/B" in result.stdout and "depth.Z" in result.stdout,
                "worker did not report expected EXR channels")

        # Existing outputs are never silently overwritten.
        repeated = run_worker(worker, beauty, depth, output)
        require(repeated.returncode != 0, "worker unexpectedly overwrote an existing EXR")
        require(os.path.isfile(output), "existing EXR was removed after overwrite refusal")

        truncated = os.path.join(temp, "truncated.pfm")
        with open(beauty, "rb") as source:
            payload = source.read()
        with open(truncated, "wb") as target:
            target.write(payload[:-4])
        bad_output = os.path.join(temp, "bad.exr")
        malformed = run_worker(worker, truncated, depth, bad_output)
        require(malformed.returncode != 0, "truncated PFM was accepted")
        require(not os.path.exists(bad_output), "failed conversion published an EXR")

        mismatched_depth = os.path.join(temp, "mismatched_depth.pfm")
        write_pfm(mismatched_depth, "Pf", 1, 2, 1, [1.0, 2.0])
        mismatch = run_worker(worker, beauty, mismatched_depth, bad_output)
        require(mismatch.returncode != 0, "mismatched dimensions were accepted")
        require(not os.path.exists(bad_output), "dimension failure published an EXR")

        invalid_float = os.path.join(temp, "invalid_float.pfm")
        write_pfm(invalid_float, "PF", 2, 2, 3,
                  [0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                   0.0, 0.0, 0.0, math.nan, 0.0, 0.0])
        nonfinite = run_worker(worker, invalid_float, depth, bad_output)
        require(nonfinite.returncode != 0, "NaN sample was accepted")

        limited = run_worker(worker, beauty, depth, bad_output, "--max-memory-mib", "1")
        require(limited.returncode == 0, "small fixture should fit the 1 MiB budget:\n" + limited.stderr)
        require(os.path.exists(bad_output), "valid conversion under budget did not publish output")

        large_beauty = os.path.join(temp, "large_beauty.pfm")
        large_depth = os.path.join(temp, "large_depth.pfm")
        large_output = os.path.join(temp, "large.exr")
        large_width = 512
        large_height = 512
        write_pfm(large_beauty, "PF", large_width, large_height, 3,
                  [0.0] * (large_width * large_height * 3))
        write_pfm(large_depth, "Pf", large_width, large_height, 1,
                  [1.0] * (large_width * large_height))
        over_budget = run_worker(worker, large_beauty, large_depth, large_output,
                                 "--max-memory-mib", "1")
        require(over_budget.returncode != 0, "inputs exceeding the memory budget were accepted")
        require(not os.path.exists(large_output), "over-budget conversion published an EXR")

    print("worker smoke checks passed")


if __name__ == "__main__":
    main()
