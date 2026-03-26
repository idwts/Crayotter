from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
from pathlib import Path
from typing import Any

MARKER = "__CRAYOTTER_EVENT__"


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True, write_through=True)


def emit(payload: dict[str, Any]) -> None:
    print(MARKER + json.dumps(payload, ensure_ascii=False), flush=True)


class _AsyncEmitter:
    def __init__(self) -> None:
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="crayotter-worker-emitter",
            daemon=True,
        )
        self._thread.start()

    def send(self, payload: dict[str, Any]) -> None:
        self._queue.put(payload)

    def close(self, timeout: float = 2.0) -> None:
        self._queue.put(None)
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            payload = self._queue.get()
            if payload is None:
                return
            emit(payload)


def main() -> int:
    configure_stdio()
    emitter = _AsyncEmitter()

    parser = argparse.ArgumentParser(description="Run a single Crayotter agent job worker.")
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--config-file", required=True)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    script_dir = project_root / "script"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    import agent

    task = Path(args.task_file).read_text(encoding="utf-8")
    config = json.loads(Path(args.config_file).read_text(encoding="utf-8"))

    agent.apply_runtime_config(config)

    def on_event(event: dict[str, Any]) -> None:
        emitter.send(
            {
                "kind": "event",
                "type": event.get("type", "runtime_event"),
                "payload": dict(event.get("payload", {})),
                "timestamp": event.get("timestamp"),
            }
        )

    try:
        final_output = agent.run_task(task, event_callback=on_event, verbose=False)
        emitter.send(
            {
                "kind": "result",
                "final_output": final_output,
                "output_files": [str(path) for path in agent.WORKSPACE.glob("*.mp4") if path.is_file()],
            }
        )
        return 0
    except Exception as exc:
        emitter.send({"kind": "error", "error": str(exc)})
        return 1
    finally:
        emitter.close()


if __name__ == "__main__":
    raise SystemExit(main())
