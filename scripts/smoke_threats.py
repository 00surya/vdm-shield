"""Exercise the installed detector in its real worker; this is not an accuracy test."""
import sys
import time
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vmd.threats import ThreatWorker


def main():
    worker = ThreatWorker("cpu", Path(__file__).resolve().parents[1] / "models")
    try:
        worker.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status, sample, error = worker.poll()
            if status == "error":
                raise RuntimeError(error)
            if status == "ready":
                worker.submit(1, 0, np.zeros((480, 640, 3), np.uint8))
            if sample:
                print({"status": status, "detections": sample["detections"], "latency_ms": sample["latency_ms"]})
                return
            time.sleep(.05)
        raise RuntimeError("Object detector did not return a frame")
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
