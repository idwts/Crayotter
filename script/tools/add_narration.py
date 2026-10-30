from __future__ import annotations

from ._shared import *


@tool
def add_narration(
    video_path: str,
    narration_text: str,
    voice: str = "Cherry",
    output_name: str = "narrated",
) -> str:
    """为视频添加 AI 生成的 TTS 旁白配音，原视频背景音量会自动降至 20%。
    旁白音频时长若超过视频时长，超出部分会被截断。

    Args:
        video_path: 输入视频文件路径，例如 "/workspace/final_video.mp4"。
            建议在已完成剪辑和转场后的视频上添加旁白。
        narration_text: 旁白文案内容（纯文字）。
            - **必须**基于视频内容和任务主题撰写，禁止使用无关文本
            - 字数建议与视频时长匹配：120秒视频约 200~350 字中文
            - 不要包含 HTML 标签或特殊格式字符
        voice: TTS 音色，可选值：
            "Cherry"（阳光积极、亲切自然小姐姐（女性））/ "Serena"（温柔小姐姐（女性））/ 
            "Ethan"（标准普通话，带部分北方口音。阳光、温暖、活力、朝气（男性））/
            "Moon"（率性帅气的月白（男性））
        output_name: 输出文件名（不含扩展名），默认 "narrated"。
            输出文件将保存为 /workspace/{output_name}.mp4。
    """
    try:
        from ._native_ffmpeg import mix_narration_native, probe_video

        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return f"旁白添加出错: 输入视频不存在或不在WORKSPACE: {video_path}"

        safe_stem = _safe_output_video_path(output_name, default_stem="narrated").stem
        audio_path = WORKSPACE / f"{safe_stem}_narration.mp3"

        tts_error = _tts_generate(narration_text, voice, audio_path)
        if tts_error:
            return f"旁白添加出错: {tts_error}"

        output_path = _safe_output_video_path(output_name, default_stem="narrated")
        duration = probe_video(resolved_video).duration
        mix_narration_native(resolved_video, output_path, [(audio_path, 0.0, duration)])

        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "narration_length": len(narration_text),
        }, ensure_ascii=False)
    except ModelCallError:
        raise
    except Exception as e:
        return f"旁白添加出错: {e}"
