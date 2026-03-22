from __future__ import annotations

from ._shared import *


_TRANSITION_PRESETS: dict[str, dict[str, Any]] = {
    "crossfade": {"ffmpeg": "fade", "default_duration": 0.8, "category": "basic"},
    "fade_through_black": {"ffmpeg": "fadeblack", "default_duration": 1.0, "category": "basic"},
    "fade_through_white": {"ffmpeg": "fadewhite", "default_duration": 1.0, "category": "basic"},
    "wipe_left": {"ffmpeg": "wipeleft", "default_duration": 0.7, "category": "motion"},
    "wipe_right": {"ffmpeg": "wiperight", "default_duration": 0.7, "category": "motion"},
    "wipe_up": {"ffmpeg": "wipeup", "default_duration": 0.7, "category": "motion"},
    "wipe_down": {"ffmpeg": "wipedown", "default_duration": 0.7, "category": "motion"},
    "slide_left": {"ffmpeg": "slideleft", "default_duration": 0.8, "category": "motion"},
    "slide_right": {"ffmpeg": "slideright", "default_duration": 0.8, "category": "motion"},
    "slide_up": {"ffmpeg": "slideup", "default_duration": 0.8, "category": "motion"},
    "slide_down": {"ffmpeg": "slidedown", "default_duration": 0.8, "category": "motion"},
    "zoom_in": {"ffmpeg": "zoomin", "default_duration": 0.9, "category": "cinematic"},
    "pixelize": {"ffmpeg": "pixelize", "default_duration": 0.7, "category": "stylized"},
    "circle_crop": {"ffmpeg": "circlecrop", "default_duration": 0.9, "category": "stylized"},
    "rect_crop": {"ffmpeg": "rectcrop", "default_duration": 0.9, "category": "stylized"},
    "distance": {"ffmpeg": "distance", "default_duration": 0.8, "category": "cinematic"},
    "smooth_left": {"ffmpeg": "smoothleft", "default_duration": 0.9, "category": "cinematic"},
    "smooth_right": {"ffmpeg": "smoothright", "default_duration": 0.9, "category": "cinematic"},
}

_TRANSITION_ALIASES = {
    "fadeblack": "fade_through_black",
    "fadewhite": "fade_through_white",
    "fade": "crossfade",
    "zoomin": "zoom_in",
}


def _normalize_transition_name(name: str) -> str:
    key = (name or "").strip().lower()
    if key in _TRANSITION_PRESETS:
        return key
    if key in _TRANSITION_ALIASES:
        return _TRANSITION_ALIASES[key]
    return "crossfade"


def _ffmpeg_binary() -> str:
    custom = os.environ.get("FFMPEG_BIN", "").strip()
    if custom:
        return custom
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_binary() -> str:
    custom = os.environ.get("FFPROBE_BIN", "").strip()
    if custom:
        return custom
    return shutil.which("ffprobe") or "ffprobe"


def _ffmpeg_run(cmd: list[str], timeout: int = 900) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0:
            # ffmpeg/ffprobe 在不同平台可能将有效输出写入 stdout 或 stderr。
            return True, (proc.stdout or proc.stderr or "")
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err[-1200:]
    except Exception as e:
        return False, str(e)


def _has_audio_stream(video_path: Path) -> bool:
    cmd = [
        _ffprobe_binary(),
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video_path),
    ]
    ok, out = _ffmpeg_run(cmd, timeout=60)
    if not ok:
        return False
    return bool((out or "").strip())


def _normalize_clip_for_transition(
    src: Path,
    out: Path,
    width: int,
    height: int,
    fps: float,
) -> tuple[bool, str]:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={max(1.0, fps):.3f},format=yuv420p"
    )
    has_audio = _has_audio_stream(src)
    if has_audio:
        cmd = [
            _ffmpeg_binary(),
            "-y",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "48000",
            str(out),
        ]
        return _ffmpeg_run(cmd, timeout=900)

    cmd = [
        _ffmpeg_binary(),
        "-y",
        "-i",
        str(src),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "48000",
        str(out),
    ]
    return _ffmpeg_run(cmd, timeout=900)


def _safe_transition_duration(requested: float, left_dur: float, right_dur: float) -> float:
    # 为防止片段过短导致 xfade/acrossfade 失败，自动夹紧到合法区间。
    max_d = max(0.15, min(left_dur, right_dur) * 0.45)
    return round(max(0.15, min(float(requested), max_d)), 3)


def _ffmpeg_supports_filter(filter_name: str) -> bool:
    ok, out = _ffmpeg_run([_ffmpeg_binary(), "-hide_banner", "-filters"], timeout=60)
    if not ok:
        return False
    text = (out or "").lower()
    needle = f" {filter_name.lower()} "
    return (needle in text) or (filter_name.lower() in text)


def _parse_transition_plan(transition_plan: Any) -> list[dict[str, Any]]:
    if transition_plan is None:
        return []
    if isinstance(transition_plan, list):
        return [item for item in transition_plan if isinstance(item, dict)]
    if isinstance(transition_plan, str):
        raw = transition_plan.strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except Exception:
            return []
    return []


@tool
def list_transition_presets() -> str:
    """返回专业转场预设列表、默认时长与分类建议。

    用途:
    - 在调用 add_transition 前先查看可用效果
    - 让 Agent 根据风格选择更合适的转场
    """
    rows: list[dict[str, Any]] = []
    for name, item in sorted(_TRANSITION_PRESETS.items(), key=lambda x: x[0]):
        rows.append(
            {
                "name": name,
                "ffmpeg_transition": item["ffmpeg"],
                "default_duration": item["default_duration"],
                "category": item["category"],
            }
        )
    return json.dumps(
        {
            "status": "success",
            "count": len(rows),
            "presets": rows,
            "usage": "可配合 plan_transition_timeline 与 add_transition(transition_plan=...) 使用",
        },
        ensure_ascii=False,
    )


@tool
def plan_transition_timeline(
    video_paths: list[str],
    style: str = "cinematic",
    base_duration: float = 0.8,
) -> str:
    """根据素材片段自动生成转场时间线建议。

    Args:
        video_paths: 视频路径列表（按时间顺序）。
        style: 转场风格，可选 cinematic / energetic / minimal。
        base_duration: 基础转场时长（秒），最终会按片段长度自动调整。
    """
    try:
        if not video_paths or len(video_paths) < 2:
            return "规划转场出错: video_paths 至少需要 2 个视频"

        resolved: list[Path] = []
        for p in video_paths:
            rp = _resolve_workspace_input_path(p, must_exist=True)
            if rp is None:
                return f"规划转场出错: 文件不存在或不在WORKSPACE: {p}"
            resolved.append(rp)

        durations: list[float] = []
        for rp in resolved:
            meta = _get_video_meta(str(rp))
            durations.append(float(meta.get("duration_seconds", 0.0)))

        style_key = (style or "cinematic").strip().lower()
        if style_key == "energetic":
            pool = ["zoom_in", "slide_left", "slide_right", "pixelize"]
            d_mul = 0.85
        elif style_key == "minimal":
            pool = ["crossfade", "fade_through_black", "smooth_left"]
            d_mul = 0.95
        else:
            pool = ["crossfade", "fade_through_black", "zoom_in", "smooth_right", "distance"]
            d_mul = 1.0

        plan: list[dict[str, Any]] = []
        for i in range(len(resolved) - 1):
            name = pool[i % len(pool)]
            d = _safe_transition_duration(base_duration * d_mul, durations[i], durations[i + 1])
            plan.append(
                {
                    "cut_index": i,
                    "from": str(resolved[i]),
                    "to": str(resolved[i + 1]),
                    "transition_type": name,
                    "duration": d,
                    "reason": f"{style_key} 风格推荐",
                }
            )

        return json.dumps(
            {
                "status": "success",
                "style": style_key,
                "base_duration": base_duration,
                "transition_plan": plan,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"规划转场出错: {e}"


@tool
def add_transition(
    video_paths: list[str],
    transition_type: str = "crossfade",
    duration: float = 1.0,
    transition_plan: Any = None,
    output_name: str = "transitioned",
) -> str:
    """在多个视频片段之间添加专业转场并合并为一个视频。

    支持三种模式:
    - 1个视频: 添加片头淡入 + 片尾淡出效果
    - 2个视频: 在两者之间添加转场并合并
    - 3个及以上: 在每对相邻视频之间添加转场并合并

    新增能力:
    - 支持 transition_plan 为每个切点指定不同转场
    - 支持更多专业转场（渐黑、渐白、滑动、缩放、像素化等）
    - 使用 ffmpeg xfade/acrossfade，保证音画时间轴一致

    ⚠️ 重要: video_paths 是列表，不要传两个独立参数。
    ⚠️ 重要: 列表中的路径必须各不相同，传入重复路径会被自动去重。

    Args:
        video_paths: 视频文件路径列表，**按播放顺序**排列，至少 1 个路径。
            例如 ["/workspace/clip_1.mp4", "/workspace/clip_2.mp4", "/workspace/clip_3.mp4"]。
            如只需对已合并的单个视频添加淡入淡出，传 ["/workspace/final_video.mp4"] 即可。
        transition_type: 默认转场类型（当不传 transition_plan 时生效）。
            推荐值: crossfade / fade_through_black / fade_through_white /
            slide_left / slide_right / zoom_in / smooth_left / smooth_right 等。
        duration: 每个转场效果的持续时间（秒），默认 1.0。
            建议范围 0.5~2.0，过大会明显缩短有效画面时长。
                transition_plan: 可选的逐切点转场计划。
            每项格式示例:
            {
              "cut_index": 0,
              "transition_type": "fade_through_black",
              "duration": 0.9
            }
            cut_index 表示第几个切点（0 表示 clip0→clip1）。
            若缺省或非法会自动回退到 transition_type + duration。
                        兼容传入 JSON 字符串，如 "[{\"cut_index\":0,...}]"。
        output_name: 输出文件名（不含扩展名），默认 "transitioned"。
            输出文件将保存为 /workspace/{output_name}.mp4。
    """
    try:
        if not video_paths:
            return "转场出错: 视频路径列表为空"

        # 去重: 防止同一个文件被传入多次导致时长翻倍
        seen: set[str] = set()
        unique_paths: list[str] = []
        for p in video_paths:
            resolved = _resolve_workspace_input_path(p, must_exist=True)
            if resolved is None:
                return f"转场出错: 文件不存在或不在WORKSPACE: {p}"
            key = str(resolved).replace("\\", "/")
            if key not in seen:
                seen.add(key)
                unique_paths.append(str(resolved))
        
        if not unique_paths:
            return "转场出错: 去重后没有可用的视频片段"

        transition_type = _normalize_transition_name(transition_type)
        duration = max(0.15, float(duration))
        output_path = _safe_output_video_path(output_name, default_stem="transitioned")

        tmp_dir = WORKSPACE / f"_transition_tmp_{output_path.stem}_{int(datetime.now().timestamp())}"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # ---------- 模式 1: 单个视频 → 淡入淡出 ----------
        if len(unique_paths) == 1:
            src = Path(unique_paths[0])
            meta = _get_video_meta(str(src))
            d = min(duration, max(0.15, float(meta.get("duration_seconds", 0.0)) / 3))

            normalized = tmp_dir / "single_normalized.mp4"
            ok, err = _normalize_clip_for_transition(
                src,
                normalized,
                int(meta.get("width", 1280) or 1280),
                int(meta.get("height", 720) or 720),
                float(meta.get("fps", 30.0) or 30.0),
            )
            if not ok:
                return f"转场出错: 单视频预处理失败: {err}"

            dur = _get_video_meta(str(normalized)).get("duration_seconds", 0.0)
            out_start = max(0.0, float(dur) - d)
            vf = f"fade=t=in:st=0:d={d},fade=t=out:st={out_start}:d={d}"
            af = f"afade=t=in:st=0:d={d},afade=t=out:st={out_start}:d={d}"

            ok, err = _ffmpeg_run(
                [
                    _ffmpeg_binary(),
                    "-y",
                    "-i",
                    str(normalized),
                    "-vf",
                    vf,
                    "-af",
                    af,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "20",
                    "-c:a",
                    "aac",
                    str(output_path),
                ],
                timeout=900,
            )
            if not ok:
                return f"转场出错: 单视频淡入淡出失败: {err}"

            out_dur = float(_get_video_meta(str(output_path)).get("duration_seconds", 0.0))
            return json.dumps({
                "status": "success",
                "path": str(output_path),
                "transition": transition_type,
                "duration_seconds": round(out_dur, 1),
                "num_clips": 1,
            }, ensure_ascii=False)

        # ---------- 模式 2/3: 多个视频 → 逐对转场合并 ----------
        first_meta = _get_video_meta(unique_paths[0])
        target_w = int(first_meta.get("width", 1280) or 1280)
        target_h = int(first_meta.get("height", 720) or 720)
        target_fps = float(first_meta.get("fps", 30.0) or 30.0)
        if target_fps <= 1.0:
            target_fps = 30.0

        normalized_paths: list[Path] = []
        for i, p in enumerate(unique_paths):
            src = Path(p)
            normalized = tmp_dir / f"norm_{i:02d}.mp4"
            ok, err = _normalize_clip_for_transition(src, normalized, target_w, target_h, target_fps)
            if not ok:
                return f"转场出错: 片段预处理失败({src.name}): {err}"
            normalized_paths.append(normalized)

        durations = [float(_get_video_meta(str(p)).get("duration_seconds", 0.0)) for p in normalized_paths]
        if any(d <= 0.2 for d in durations):
            return "转场出错: 存在时长过短片段(<0.2s)，无法应用稳定转场"

        parsed_plan = _parse_transition_plan(transition_plan)
        plan_by_cut: dict[int, tuple[str, float]] = {}
        if parsed_plan:
            for item in parsed_plan:
                cut_index = int(item.get("cut_index", -1))
                if cut_index < 0 or cut_index >= len(normalized_paths) - 1:
                    continue
                t_name = _normalize_transition_name(str(item.get("transition_type", transition_type)))
                d = float(item.get("duration", duration))
                plan_by_cut[cut_index] = (t_name, max(0.15, d))

        applied_plan: list[dict[str, Any]] = []
        filter_parts: list[str] = []
        timeline_cursor = durations[0]

        for i in range(1, len(normalized_paths)):
            cut = i - 1
            t_name, d_req = plan_by_cut.get(cut, (transition_type, duration))
            d_use = _safe_transition_duration(d_req, durations[i - 1], durations[i])
            ff_t = _TRANSITION_PRESETS[t_name]["ffmpeg"]
            offset = max(0.0, timeline_cursor - d_use)

            v_left = f"[{i-1}:v]" if i == 1 else f"[v{i-1}]"
            a_left = f"[{i-1}:a]" if i == 1 else f"[a{i-1}]"
            v_right = f"[{i}:v]"
            a_right = f"[{i}:a]"

            filter_parts.append(
                f"{v_left}{v_right}xfade=transition={ff_t}:duration={d_use}:offset={offset}[v{i}]"
            )
            filter_parts.append(
                f"{a_left}{a_right}acrossfade=d={d_use}:c1=tri:c2=tri[a{i}]"
            )

            timeline_cursor = timeline_cursor + durations[i] - d_use
            applied_plan.append(
                {
                    "cut_index": cut,
                    "transition_type": t_name,
                    "ffmpeg_transition": ff_t,
                    "duration": round(d_use, 3),
                    "offset": round(offset, 3),
                }
            )

        cmd = [_ffmpeg_binary(), "-y"]
        for p in normalized_paths:
            cmd.extend(["-i", str(p)])
        cmd.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                f"[v{len(normalized_paths)-1}]",
                "-map",
                f"[a{len(normalized_paths)-1}]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )

        if not _ffmpeg_supports_filter("xfade"):
            ffmpeg_path = _ffmpeg_binary()
            return f"转场出错: 当前 ffmpeg 不支持 xfade（检测命令使用: {ffmpeg_path}），请确认运行时 PATH 指向包含 xfade 的 ffmpeg"

        ok, err = _ffmpeg_run(cmd, timeout=1800)
        if not ok:
            return f"转场出错: ffmpeg 合成失败: {err}"

        out_dur = float(_get_video_meta(str(output_path)).get("duration_seconds", 0.0))

        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "transition": transition_type,
            "duration_seconds": round(out_dur, 1),
            "num_clips": len(unique_paths),
            "applied_transition_plan": applied_plan,
        }, ensure_ascii=False)
    except Exception as e:
        return f"转场出错: {e}"
