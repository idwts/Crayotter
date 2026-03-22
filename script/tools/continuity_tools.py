from __future__ import annotations

from ._shared import Path, cv2, json, tool, _resolve_workspace_input_path


def _sample_frames_signature(video_path: Path, from_tail: bool, sample_seconds: float = 1.0) -> dict[str, float]:
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / fps if fps > 0 else 0.0

    if duration <= 0.0 or frame_count <= 2:
        cap.release()
        return {"brightness": 0.0, "motion": 0.0, "sat": 0.0}

    window = max(0.3, float(sample_seconds))
    if from_tail:
        start_t = max(0.0, duration - window)
        end_t = duration
    else:
        start_t = 0.0
        end_t = min(duration, window)

    start_f = int(start_t * fps)
    end_f = min(frame_count - 1, int(end_t * fps))

    br_values: list[float] = []
    sat_values: list[float] = []
    motions: list[float] = []
    prev_gray = None

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    for _ in range(start_f, end_f, max(1, int(fps // 8))):
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        br_values.append(float(gray.mean()))
        sat_values.append(float(hsv[:, :, 1].mean()))

        if prev_gray is not None:
            diff = cv2.absdiff(gray, prev_gray)
            motions.append(float(diff.mean()))
        prev_gray = gray

    cap.release()

    return {
        "brightness": float(sum(br_values) / max(1, len(br_values))),
        "sat": float(sum(sat_values) / max(1, len(sat_values))),
        "motion": float(sum(motions) / max(1, len(motions))),
    }


def _continuity_score(left_sig: dict[str, float], right_sig: dict[str, float]) -> tuple[float, dict[str, float]]:
    b_gap = abs(left_sig["brightness"] - right_sig["brightness"]) / 255.0
    s_gap = abs(left_sig["sat"] - right_sig["sat"]) / 255.0
    m_gap = abs(left_sig["motion"] - right_sig["motion"]) / 80.0

    # 亮度差和运动差对观感影响更大。
    penalty = 0.45 * b_gap + 0.35 * m_gap + 0.20 * s_gap
    score = max(0.0, min(100.0, (1.0 - penalty) * 100.0))
    return score, {"brightness_gap": b_gap, "saturation_gap": s_gap, "motion_gap": m_gap}


@tool
def score_cut_continuity(
    left_video_path: str,
    right_video_path: str,
    sample_seconds: float = 1.0,
) -> str:
    """对相邻两段视频做切点连续性评分（0-100）。

    评分依据：亮度差、饱和度差、运动强度差。
    """
    try:
        left = _resolve_workspace_input_path(left_video_path, must_exist=True)
        right = _resolve_workspace_input_path(right_video_path, must_exist=True)
        if left is None:
            return f"评分出错: 左片段不存在或不在WORKSPACE: {left_video_path}"
        if right is None:
            return f"评分出错: 右片段不存在或不在WORKSPACE: {right_video_path}"

        left_sig = _sample_frames_signature(left, from_tail=True, sample_seconds=sample_seconds)
        right_sig = _sample_frames_signature(right, from_tail=False, sample_seconds=sample_seconds)

        score, gaps = _continuity_score(left_sig, right_sig)

        level = "excellent" if score >= 80 else "good" if score >= 65 else "fair" if score >= 45 else "poor"

        return json.dumps(
            {
                "status": "success",
                "score": round(score, 2),
                "level": level,
                "left_signature": left_sig,
                "right_signature": right_sig,
                "gaps": {k: round(v, 4) for k, v in gaps.items()},
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"评分出错: {e}"


@tool
def recommend_transition_for_cut(
    left_video_path: str,
    right_video_path: str,
    style: str = "cinematic",
    sample_seconds: float = 1.0,
) -> str:
    """基于切点连续性特征推荐转场类型与时长。

    返回值可直接用于 add_transition 的 transition_plan 单项。
    """
    try:
        left = _resolve_workspace_input_path(left_video_path, must_exist=True)
        right = _resolve_workspace_input_path(right_video_path, must_exist=True)
        if left is None or right is None:
            return "推荐出错: 输入片段不存在或不在WORKSPACE"

        left_sig = _sample_frames_signature(left, from_tail=True, sample_seconds=sample_seconds)
        right_sig = _sample_frames_signature(right, from_tail=False, sample_seconds=sample_seconds)
        score, gaps = _continuity_score(left_sig, right_sig)

        style_key = (style or "cinematic").strip().lower()

        if score >= 80:
            transition = "crossfade"
            duration = 0.6
            reason = "两段风格接近，使用轻量溶解保持节奏"
        elif gaps["motion_gap"] > 0.35:
            transition = "fade_through_black" if style_key != "energetic" else "slide_left"
            duration = 0.8
            reason = "运动差异较大，需要过渡缓冲"
        elif gaps["brightness_gap"] > 0.30:
            transition = "fade_through_black"
            duration = 0.9
            reason = "明暗跨度大，先压暗再切换更自然"
        elif style_key == "energetic":
            transition = "zoom_in"
            duration = 0.65
            reason = "节奏型风格优先动态转场"
        else:
            transition = "smooth_right"
            duration = 0.75
            reason = "中等差异，使用平滑位移过渡"

        return json.dumps(
            {
                "status": "success",
                "continuity_score": round(score, 2),
                "recommended": {
                    "transition_type": transition,
                    "duration": duration,
                },
                "reason": reason,
                "features": {
                    "left_signature": left_sig,
                    "right_signature": right_sig,
                    "gaps": {k: round(v, 4) for k, v in gaps.items()},
                },
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"推荐出错: {e}"
