# Functional checks for the S1-S3 hardening branch — real files, real
# subprocesses, no mocks. Complements (not replaces) the unit suite.
# Run from repo root: python functional_checks_s1_s3.py
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
RUNTIME = Path(tempfile.mkdtemp(prefix="cray_func_"))
os.environ["CRAYOTTER_RUNTIME_ROOT"] = str(RUNTIME)
sys.path.insert(0, str(REPO / "script"))

import cv2
import numpy as np

WS = RUNTIME / "temp"
WS.mkdir(parents=True, exist_ok=True)

# Real 3s clip as the shared fixture.
src = WS / "source.mp4"
writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
for i in range(30):
    writer.write(np.full((120, 160, 3), (i * 8) % 255, np.uint8))
writer.release()
assert src.is_file() and src.stat().st_size > 0, "fixture video not written"

results: dict[str, object] = {}

# --- FC-5 (S3-1): real decodability gate, no mocks -----------------------
from phase3_rl.reward import _artifact_gate, find_final_video_path

fake = WS / "fake.mp4"
fake.write_bytes(b"not a video")
results["gate_real_video"] = _artifact_gate(src)
results["gate_fake_video"] = _artifact_gate(fake)

report: dict = {}
found = find_final_video_path(
    [
        {"tool_name": "export_video", "success": True, "arguments": {},
         "parsed_result": {"output_path": str(src)}, "output_paths": [str(src)]},
        {"tool_name": "export_video", "success": True, "arguments": {},
         "parsed_result": {"output_path": str(fake)}, "output_paths": [str(fake)]},
    ],
    gate_report=report,
)
results["fallback_to_earlier_valid"] = found == str(src.resolve())
results["fallback_report"] = report

# --- FC-3 (S1-3): real cut through the moviepy fallback branch -----------
import tools._native_ffmpeg as native_ffmpeg

native_ffmpeg.binaries_available = lambda: False  # force the moviepy branch
cut_tool = importlib.import_module("tools.cut_video").cut_video

bad = str(cut_tool.invoke({"input_path": str(src), "start_time": 0.0, "end_time": 999.0}))
results["cut_invalid_range_rejected"] = bad
good_raw = str(cut_tool.invoke({"input_path": str(src), "start_time": 0.5, "end_time": 2.0}))
try:
    good = json.loads(good_raw)
except ValueError:
    good = {"status": "unparseable", "raw": good_raw[:200]}
results["cut_valid_status"] = good.get("status")
out_path = Path(good.get("path", ""))
results["cut_valid_real_file"] = out_path.is_file() and out_path.stat().st_size > 0
results["cut_valid_decodable"] = _artifact_gate(out_path) if out_path.is_file() else "no file"

# --- FC-4 (S2-1): real worker roundtrip via execute_tool_subprocess ------
os.environ["CRAYOTTER_RL_TOOL_WORKER"] = "1"
from phase3_rl.tool_runtime import _TOOL_WORKER_POOLS, execute_tool_subprocess

r1 = execute_tool_subprocess(
    tool_name="inspect_video_duration",
    arguments={"video_path": str(src)},
    runtime_root=str(RUNTIME),
)
r2 = execute_tool_subprocess(
    tool_name="inspect_video_duration",
    arguments={"video_path": str(src)},
    runtime_root=str(RUNTIME),
)
results["worker_call_1"] = {
    "success": r1.success,
    "returncode": r1.returncode,
    "duration": (json.loads(r1.raw_result).get("duration_seconds")
                 if r1.raw_result.strip().startswith("{") else r1.raw_result[:120]),
}
results["worker_call_2_success"] = r2.success
workers = [w for pool in _TOOL_WORKER_POOLS.values() for w in pool]
results["worker_reused_one_process"] = len(workers) == 1 and workers[0].calls == 2

r3 = execute_tool_subprocess(
    tool_name="no_such_tool", arguments={}, runtime_root=str(RUNTIME)
)
results["worker_unknown_tool_explicit_failure"] = {
    "success": r3.success, "returncode": r3.returncode,
}

# Episode teardown: release retires the episode's worker (round-2 finding 1).
from phase3_rl.tool_runtime import release_tool_workers

retired = release_tool_workers(RUNTIME)
time.sleep(1.0)  # SIGKILL needs a moment before poll() reports the exit
results["release_retired_episode_worker"] = retired == 1
results["release_emptied_pool"] = not any(_TOOL_WORKER_POOLS.values())
results["released_worker_process_dead"] = all(
    w.process.poll() is not None for w in workers
)

# --- FC-1 (S1-1): two-tier verdicts on realistic real-tool payloads ------
from phase3_rl.tool_runtime import result_indicates_failure

GRAPH = dict(markers=("出错", "失败", "error", '"status": "fail"'),
             strict_status=False, full_text=True)
real_payloads = [
    good_raw,                      # real cut_video success JSON
    bad,                           # real cut_video tool_error string
    r1.raw_result,                 # real inspect_video_duration JSON
    str(cut_tool.invoke({"input_path": "/nonexistent.mp4",
                         "start_time": 0.0, "end_time": 1.0})),  # real not-found error
]
results["two_tier_verdicts"] = [
    {"payload_head": p[:60],
     "graph_tier": result_indicates_failure(p, **GRAPH),
     "strict_tier": result_indicates_failure(p)}
    for p in real_payloads
]

print("RUNTIME_DIR:", RUNTIME)
for key, value in results.items():
    print(f"FUNC {key}: {json.dumps(value, ensure_ascii=False, default=str)}")
