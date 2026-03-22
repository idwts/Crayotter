from __future__ import annotations

from ._shared import *


@tool
def add_subtitles(
    video_path: str,
    subtitles: list[dict],
    output_name: str = "subtitled",
) -> str:
    """为视频添加字幕（不含配音），字幕按时间段显示在画面底部。

    适合已有配音的视频补加字幕，或为保留原声的片段添加解说字幕。

    Args:
        video_path: 输入视频文件路径，例如 "/workspace/final.mp4"。
        subtitles: 字幕列表，每个元素是一个 dict：
            - "text": str — 字幕文字（必填）
            - "start": float — 显示开始时间（秒）（必填）
            - "end": float — 显示结束时间（秒）（必填）
            示例：
            [
                {"text": "新疆大学始建于1924年", "start": 10.0, "end": 15.0},
                {"text": "是国家双一流建设高校", "start": 15.0, "end": 20.0}
            ]
        output_name: 输出文件名（不含扩展名），默认 "subtitled"。
    """
    try:
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
        from moviepy.video.VideoClip import TextClip

        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return f"字幕添加出错: 输入视频不存在或不在WORKSPACE: {video_path}"

        if not subtitles or not isinstance(subtitles, list):
            return "字幕添加出错: subtitles 参数必须是非空列表"

        video = VideoFileClip(str(resolved_video))
        video_dur = video.duration
        sub_clips: list[Any] = []

        for idx, sub in enumerate(subtitles):
            text = sub.get("text", "").strip()
            start = float(sub.get("start", 0))
            end = float(sub.get("end", 0))

            if not text or end <= start or start >= video_dur:
                continue
            end = min(end, video_dur)

            lines = []
            remaining = text
            while len(remaining) > 18:
                lines.append(remaining[:18])
                remaining = remaining[18:]
            if remaining:
                lines.append(remaining)
            display_text = "\n".join(lines)

            try:
                txt_clip = TextClip(
                    text=display_text,
                    font_size=42,
                    color="white",
                    stroke_color="black",
                    stroke_width=2,
                    font="./AlibabaPuHuiTi-3-55-Regular/AlibabaPuHuiTi-3-55-Regular.ttf",
                    text_align="center",
                    size=(video.size[0] - 120, None),
                    duration=end - start,
                )
                txt_clip = txt_clip.with_position(("center", video.size[1] - 160))
                txt_clip = txt_clip.with_start(start)
                sub_clips.append(txt_clip)
            except Exception as se:
                logger.warning("字幕 %d 创建失败: %s", idx+1, se)

        if not sub_clips:
            video.close()
            return "字幕添加出错: 无有效字幕段"

        final = CompositeVideoClip([video] + sub_clips)
        if video.audio is not None:
            final = final.with_audio(video.audio)

        output_path = _safe_output_video_path(output_name, default_stem="subtitled")
        final.write_videofile(
            str(output_path), codec="libx264", audio_codec="aac", logger=None
        )

        for sc in sub_clips:
            try:
                sc.close()
            except Exception:
                pass
        video.close()
        final.close()

        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "subtitle_count": len(sub_clips),
        }, ensure_ascii=False)
    except Exception as e:
        return f"字幕添加出错: {e}"
