"""Exercise an extracted desktop release without Python or FFmpeg on its PATH."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    checks = {}
    with tempfile.TemporaryDirectory(prefix="Crayotter Package Test ") as directory:
        root = Path(directory)
        if sys.platform == "darwin":
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(args.archive.resolve()), str(root)], check=True)
            executable = root / "Crayotter.app/Contents/MacOS/Crayotter"
        else:
            shutil.unpack_archive(str(args.archive.resolve()), str(root))
            executable = root / "Crayotter/Crayotter.exe"
        expected_version = (Path(__file__).parent / "VERSION").read_text().strip()
        if sys.platform == "darwin":
            with (root / "Crayotter.app/Contents/Info.plist").open("rb") as stream:
                version = plistlib.load(stream)["CFBundleShortVersionString"]
        else:
            import ctypes
            version_api = ctypes.windll.version
            size = version_api.GetFileVersionInfoSizeW(str(executable), None)
            data = ctypes.create_string_buffer(size)
            assert version_api.GetFileVersionInfoW(str(executable), 0, size, data)
            pointer, length = ctypes.c_void_p(), ctypes.c_uint()
            assert version_api.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length))
            fields = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
            version = f"{fields[4] >> 16}.{fields[4] & 65535}.{fields[5] >> 16}"
        assert version == expected_version, (version, expected_version)
        checks["version"] = version
        seeds = list(root.rglob("memory_experience/latest_skills.md"))
        assert len(seeds) == 1 and "No task experience has been saved" in seeds[0].read_text()
        checks["clean_experience"] = True
        runtime = root / "runtime"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CRAYOTTER_", "CONDA", "PYTHON")) and "API_KEY" not in k and k not in ("FFMPEG_BIN", "FFPROBE_BIN", "DYLD_LIBRARY_PATH")}
        env["CRAYOTTER_RUNTIME_ROOT"] = str(runtime)
        env["PATH"] = os.pathsep.join([str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"), os.environ.get("SystemRoot", r"C:\Windows")]) if os.name == "nt" else "/usr/bin:/bin:/usr/sbin:/sbin"
        result = subprocess.run([str(executable), "--crayotter-self-test"], cwd=root, env=env, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(f"Frozen self-test failed ({result.returncode}): {result.stdout}\n{result.stderr}")
        checks["offline_self_test"] = json.loads((runtime / "package_self_test.json").read_text())
        assert checks["offline_self_test"]["frozen"] and checks["offline_self_test"]["passed"]
        for name in ("ffmpeg", "ffprobe", "yt-dlp"):
            assert Path(checks["offline_self_test"][name]["path"]).resolve().is_relative_to(root.resolve())
        worker = subprocess.run([str(executable), "--crayotter-worker", "--help"], cwd=root, env=env, capture_output=True, text=True, timeout=30)
        assert worker.returncode == 0 and "--task-file" in worker.stdout, worker.stderr
        checks["worker_entry"] = True
        gui_report = root / "native-window.json"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            gui_port = sock.getsockname()[1]
        gui = subprocess.run([str(executable), "--gui-test-report", str(gui_report), "--port", str(gui_port)], cwd=root, env=env, capture_output=True, text=True, timeout=120)
        assert gui.returncode == 0 and gui_report.is_file(), f"Native window failed: {gui.stdout}\n{gui.stderr}"
        checks["native_window"] = json.loads(gui_report.read_text(encoding="utf-8"))
        assert checks["native_window"]["passed"], checks["native_window"]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=3) as response:
                return response.read()

        with (root / "backend.log").open("w") as log:
            process = subprocess.Popen([str(executable), "--headless", "--port", str(port)], cwd=root, env=env, stdout=log, stderr=log)
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError((root / "backend.log").read_text())
                    try:
                        checks["health"] = json.loads(get("/health"))
                        break
                    except OSError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Packaged backend did not start")
                config = json.loads(get("/config"))
                checks["models"] = {k: config["profiles"]["default"][k] for k in ("model_name", "video_model_name", "tts_model_name")}
                html = get("/ui/").decode()
                assets = re.findall(r'(?:src|href)="(/ui/assets/[^\"]+)"', html)
                assert assets, "No frontend assets found"
                for asset in assets:
                    assert get(asset)
                checks["frontend_assets"] = assets
                request = urllib.request.Request(base + "/jobs", data=json.dumps({"task": "desktop package smoke", "mode": "demo", "enable_plan_review": False}).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    job_id = json.load(response)["job_id"]
                for _ in range(100):
                    job = json.loads(get("/jobs/" + job_id))
                    if job["status"] in ("completed", "failed", "cancelled"):
                        break
                    time.sleep(0.1)
                assert job["status"] == "completed", job
                checks["demo_job"] = job["status"]
                checks["events"] = len(json.loads(get(f"/jobs/{job_id}/events"))["items"])
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if sys.platform == "darwin":
            subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(root / "Crayotter.app")], check=True)
            checks["codesign_valid_after_run"] = True
        checks["passed"] = True
        report.write_text(json.dumps(checks, ensure_ascii=False, indent=2))
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
