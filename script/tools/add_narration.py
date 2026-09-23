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
        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return tool_error("旁白添加", f"输入视频不存在或不在WORKSPACE: {video_path}")

        client = OpenAI(base_url=TTS_BASE_URL, api_key=TTS_API_KEY)

        # client = _get_openai_client()
        safe_stem = _safe_output_video_path(output_name, default_stem="narrated").stem
        audio_path = WORKSPACE / f"{safe_stem}_narration.mp3"
        # response = client.audio.speech.create(
        #     model="qwen3-tts-instruct-flash", voice=voice, input=narration_text
        # )
        # response.stream_to_file(str(audio_path))

        tts_error = _tts_generate(narration_text, voice, audio_path)
        if tts_error:
            return tool_error("旁白添加", tts_error)

        output_path = _safe_output_video_path(output_name, default_stem="narrated")

        from ._native_ffmpeg import binaries_available, mix_narration_native, probe_video_optional

        if binaries_available():
            probe = probe_video_optional(resolved_video)
            if probe is not None:
                max_seconds = probe.duration if probe.duration > 0 else None
                mix_narration_native(
                    resolved_video,
                    [(audio_path, 0.0, 1.0, max_seconds)],
                    output_path,
                )
                return tool_success(path=str(output_path), narration_length=len(narration_text))

        from moviepy.audio.AudioClip import CompositeAudioClip
        from moviepy.audio.io.AudioFileClip import AudioFileClip
        from moviepy.video.io.VideoFileClip import VideoFileClip

        video = VideoFileClip(str(resolved_video))
        narration_audio = AudioFileClip(str(audio_path))

        if narration_audio.duration > video.duration:
            narration_audio = narration_audio.subclipped(0, video.duration)

        if video.audio is not None:
            mixed = CompositeAudioClip(
                [video.audio.with_volume_scaled(0.2), narration_audio]
            )
        else:
            mixed = narration_audio

        final = video.with_audio(mixed)
        final.write_videofile(
            str(output_path), codec="libx264", audio_codec="aac", logger=None
        )
        video.close()
        narration_audio.close()
        final.close()

        return tool_success(path=str(output_path), narration_length=len(narration_text))
    except ModelCallError:
        raise
    except Exception as e:
        return tool_error("旁白添加", e)
