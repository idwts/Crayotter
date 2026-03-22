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
        from moviepy.audio.AudioClip import CompositeAudioClip
        from moviepy.audio.io.AudioFileClip import AudioFileClip
        from moviepy.video.io.VideoFileClip import VideoFileClip

        resolved_video = _resolve_workspace_input_path(video_path, must_exist=True)
        if resolved_video is None:
            return f"旁白添加出错: 输入视频不存在或不在WORKSPACE: {video_path}"
        
        client = OpenAI(base_url=TTS_BASE_URL, api_key=TTS_API_KEY)

        # client = _get_openai_client()
        safe_stem = _safe_output_video_path(output_name, default_stem="narrated").stem
        audio_path = WORKSPACE / f"{safe_stem}_narration.mp3"
        # response = client.audio.speech.create(
        #     model="qwen3-tts-instruct-flash", voice=voice, input=narration_text
        # )
        # response.stream_to_file(str(audio_path))

        dashscope.api_key = TTS_API_KEY
        response = dashscope.MultiModalConversation.call(
            model=TTS_MODEL_NAME,
            text=narration_text,
            voice=voice
        )

        # 获取音频 URL
        if response.status_code == 200:
            audio_url = response.output.audio.url
            logger.info(f'TTS 音频URL: {audio_url}')
            # 下载音频
            import urllib.request
            urllib.request.urlretrieve(audio_url, audio_path)
            logger.info(f'TTS 音频已保存到 {audio_path}')
        else:
            return f"旁白添加出错: TTS 生成失败 (status={response.status_code}): {response.message}"

        output_path = _safe_output_video_path(output_name, default_stem="narrated")
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

        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "narration_length": len(narration_text),
        }, ensure_ascii=False)
    except Exception as e:
        return f"旁白添加出错: {e}"
