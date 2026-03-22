from __future__ import annotations

from ._shared import Path, json, subprocess, tool, _resolve_workspace_input_path, _safe_output_video_path


def _run_ffmpeg(cmd: list[str], timeout: int = 900) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0:
            return True, ""
        return False, (proc.stderr or proc.stdout or "")[-1200:]
    except Exception as e:
        return False, str(e)


def _build_duck_volume_expr(segments: list[dict[str, float]], duck_factor: float) -> str:
    expr = "1"
    for seg in segments:
        s = max(0.0, float(seg["start"]))
        e = max(s, float(seg["end"]))
        expr = f"if(between(t,{s:.3f},{e:.3f}),{duck_factor:.4f},{expr})"
    return expr


@tool
def duck_background_audio(
    video_path: str,
    narration_segments: list[dict],
    duck_gain_db: float = -12.0,
    output_name: str = "ducked",
) -> str:
    """按旁白时间段压低背景音，提升人声可懂度。

    适用场景：
    - 你已经有旁白时间段，但尚未最终混音
    - 希望在旁白出现时自动降低底噪/BGM

    Args:
        video_path: 输入视频。
        narration_segments: 旁白时间段列表（每项需含 start/end）。
        duck_gain_db: 压低分贝，默认 -12dB。
        output_name: 输出文件名（不含扩展名）。
    """
    try:
        resolved = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved is None:
            return f"压背景音出错: 输入视频不存在或不在WORKSPACE: {video_path}"
        if not isinstance(narration_segments, list) or not narration_segments:
            return "压背景音出错: narration_segments 必须是非空列表"

        valid_segs: list[dict[str, float]] = []
        for seg in narration_segments:
            if not isinstance(seg, dict):
                continue
            if "start" not in seg or "end" not in seg:
                continue
            s = float(seg.get("start", 0.0))
            e = float(seg.get("end", 0.0))
            if e <= s:
                continue
            valid_segs.append({"start": s, "end": e})

        if not valid_segs:
            return "压背景音出错: narration_segments 无有效时间段"

        factor = pow(10.0, float(duck_gain_db) / 20.0)
        factor = max(0.05, min(1.0, factor))

        volume_expr = _build_duck_volume_expr(valid_segs, factor)
        output_path = _safe_output_video_path(output_name, default_stem="ducked")

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(resolved),
            "-filter:a",
            f"volume='{volume_expr}'",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(output_path),
        ]
        ok, err = _run_ffmpeg(cmd, timeout=1200)
        if not ok:
            return f"压背景音出错: {err}"

        return json.dumps(
            {
                "status": "success",
                "path": str(output_path),
                "duck_gain_db": duck_gain_db,
                "segments": len(valid_segs),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"压背景音出错: {e}"


@tool
def normalize_loudness(
    video_path: str,
    target_lufs: float = -16.0,
    true_peak: float = -1.5,
    lra: float = 11.0,
    output_name: str = "loudnorm",
) -> str:
    """对视频音轨执行响度归一化（EBU R128 loudnorm）。

    适用场景：
    - 最终导出前统一响度
    - 解决不同来源片段音量忽大忽小
    """
    try:
        resolved = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved is None:
            return f"响度归一化出错: 输入视频不存在或不在WORKSPACE: {video_path}"

        output_path = _safe_output_video_path(output_name, default_stem="loudnorm")
        af = (
            f"loudnorm=I={float(target_lufs):.1f}:"
            f"TP={float(true_peak):.1f}:LRA={float(lra):.1f}"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(resolved),
            "-af",
            af,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(output_path),
        ]
        ok, err = _run_ffmpeg(cmd, timeout=1200)
        if not ok:
            return f"响度归一化出错: {err}"

        return json.dumps(
            {
                "status": "success",
                "path": str(output_path),
                "target_lufs": target_lufs,
                "true_peak": true_peak,
                "lra": lra,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"响度归一化出错: {e}"
