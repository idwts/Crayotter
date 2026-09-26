# Real end-to-end experiment over the Crayotter MCP server (stdio):
# make 3 real clips with distinct visual content, then cut -> merge ->
# inspect -> export, verifying every artifact with independent cv2 probes.
import asyncio
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO, "script"))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPORT = os.path.join(REPO, "functional_20260926_e2e_mcp.json")


def unwrap(result):
    text = result.content[0].text if result.content else ""
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


async def main():
    import cv2
    import numpy as np

    from script.tools._shared import WORKSPACE

    # --- build 3 real source clips with distinct colors/motion ---
    sources = []
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    for i, color in enumerate(colors):
        path = WORKSPACE / f"e2e_src_{i}.mp4"
        w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (160, 120))
        for f in range(40):  # 4s each
            frame = np.full((120, 160, 3), color, dtype="uint8")
            cv2.circle(frame, (20 + f * 3, 60), 10, (255, 255, 255), -1)
            w.write(frame)
        w.release()
        sources.append(path)
    log = {"sources": [str(s) for s in sources], "steps": []}

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", "-m", "script.mcp_server"],
        cwd=REPO,
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            log["tools_listed"] = len(tools.tools)

            async def call(name, args):
                res = await session.call_tool(name, args)
                payload = unwrap(res)
                log["steps"].append({"tool": name, "args": args,
                                     "is_error": res.is_error, "payload": payload})
                print(f"{name}: is_error={res.is_error} -> {str(payload)[:160]}", flush=True)
                return payload

            # cut 1s-3s from each source (real re-encode through native ffmpeg)
            clips = []
            for i, src in enumerate(sources):
                out = f"e2e_clip_{i}.mp4"
                p = await call("cut_video", {
                    "input_path": str(src), "start_time": 1.0, "end_time": 3.0,
                    "output_name": out})
                assert p.get("status") == "success", p
                clips.append(p["path"])

            merged = await call("merge_videos", {
                "video_paths": clips, "output_name": "e2e_merged.mp4"})
            assert merged.get("status") == "success", merged

            probe = await call("inspect_video_duration", {"video_path": merged["path"]})
            assert probe.get("status") == "success", probe

            exported = await call("export_video", {
                "input_path": merged["path"], "output_name": "e2e_final.mp4",
                "resolution": "720p"})
            assert exported.get("status") == "success", exported

    # --- independent verification with cv2 (outside the MCP session) ---
    verify = {}
    for label, p in [("merged", merged["path"]), ("exported", exported["path"])]:
        cap = cv2.VideoCapture(p)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w_ = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h_ = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        # sample middle frames of each third to confirm all three colors survived
        thirds = []
        for k in range(3):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frames * (k + 0.5) / 3))
            ok, frame = cap.read()
            if ok:
                mean = frame.mean(axis=(0, 1))  # BGR
                thirds.append([round(float(x), 1) for x in mean])
        cap.release()
        verify[label] = {"fps": fps, "frames": frames,
                         "duration_s": round(frames / fps, 2) if fps else None,
                         "size": f"{w_}x{h_}", "third_means_bgr": thirds}
        print(label, verify[label], flush=True)
    log["verify"] = verify

    # expected: merged ~6s; each third dominated by its clip's color
    dom = []
    for means in verify["merged"]["third_means_bgr"]:
        b, g, r = means
        dom.append("B" if b == max(means) else ("G" if g == max(means) else "R"))
    log["merged_thirds_dominant_color"] = dom
    log["PASS"] = (dom == ["B", "G", "R"]
                   and 5.0 <= verify["merged"]["duration_s"] <= 7.0
                   and verify["exported"]["size"] == "1280x720")
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump(log, fh, ensure_ascii=False, indent=2)
    print("PASS:", log["PASS"], flush=True)

    # cleanup sources; keep merged/final for user inspection? remove to stay tidy
    for s in sources:
        s.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
