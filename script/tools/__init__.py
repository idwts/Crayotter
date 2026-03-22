from __future__ import annotations

from . import _shared
from ._shared import LOGS_DIR, MEMORY_EXPERIENCE_DIR, USER_WORKSPACE, WORKSPACE, logger
from .search_youtobe_video import search_youtobe_video
from .download_youtobe_video import download_youtobe_video
from .analyze_video import analyze_video
from .cut_video import cut_video
from .batch_cut_video import batch_cut_video
from .recall_semantic_segments import recall_semantic_segments
from .merge_videos import merge_videos
from .inspect_video_duration import inspect_video_duration
from .add_transition import add_transition, list_transition_presets, plan_transition_timeline
from .add_narration import add_narration
from .add_narration_segments import add_narration_segments
from .validate_narration_timeline import validate_narration_timeline
from .timeline_tools import build_edit_timeline_from_segments, align_narration_to_timeline, validate_timeline_constraints
from .continuity_tools import score_cut_continuity, recommend_transition_for_cut
from .audio_post_tools import duck_background_audio, normalize_loudness
from .add_subtitles import add_subtitles
from .export_video import export_video
from .search_bilibili_video import search_bilibili_video
from .download_bilibili_video import download_bilibili_video
from .rank_video_candidates import rank_video_candidates


def configure(*, api_key=None, base_url=None, model_name=None, video_api_key=None, video_base_url=None, video_model_name=None, tts_api_key=None, tts_base_url=None, tts_model_name=None) -> None:
    if api_key is not None:
        _shared.API_KEY = api_key
    if base_url is not None:
        _shared.BASE_URL = base_url
    if model_name is not None:
        _shared.MODEL_NAME = model_name
    if video_api_key is not None:
        _shared.VIDEO_API_KEY = video_api_key
    if video_base_url is not None:
        _shared.VIDEO_BASE_URL = video_base_url
    if video_model_name is not None:
        _shared.VIDEO_MODEL_NAME = video_model_name
    if tts_api_key is not None:
        _shared.TTS_API_KEY = tts_api_key
    if tts_base_url is not None:
        _shared.TTS_BASE_URL = tts_base_url
    if tts_model_name is not None:
        _shared.TTS_MODEL_NAME = tts_model_name
    _shared._openai_client = None
    _shared._video_client = None


def __getattr__(name: str):
    if name in ['API_KEY', 'BASE_URL', 'MODEL_NAME', 'VIDEO_API_KEY', 'VIDEO_BASE_URL', 'VIDEO_MODEL_NAME', 'TTS_API_KEY', 'TTS_BASE_URL', 'TTS_MODEL_NAME']:
        return getattr(_shared, name)
    raise AttributeError(f"module 'tools' has no attribute {name!r}")


ALL_TOOLS = [
    search_bilibili_video,
    download_bilibili_video,
    rank_video_candidates,
    analyze_video,
    recall_semantic_segments,
    batch_cut_video,
    cut_video,
    merge_videos,
    inspect_video_duration,
    list_transition_presets,
    plan_transition_timeline,
    add_transition,
    validate_narration_timeline,
    build_edit_timeline_from_segments,
    align_narration_to_timeline,
    validate_timeline_constraints,
    score_cut_continuity,
    recommend_transition_for_cut,
    duck_background_audio,
    normalize_loudness,
    add_narration,
    add_narration_segments,
    add_subtitles,
    export_video,
]

__all__ = [
    'WORKSPACE',
    'USER_WORKSPACE',
    'MEMORY_EXPERIENCE_DIR',
    'LOGS_DIR',
    'logger',
    'configure',
    'ALL_TOOLS',
    'search_youtobe_video',
    'download_youtobe_video',
    'analyze_video',
    'cut_video',
    'batch_cut_video',
    'recall_semantic_segments',
    'merge_videos',
    'inspect_video_duration',
    'list_transition_presets',
    'plan_transition_timeline',
    'add_transition',
    'validate_narration_timeline',
    'build_edit_timeline_from_segments',
    'align_narration_to_timeline',
    'validate_timeline_constraints',
    'score_cut_continuity',
    'recommend_transition_for_cut',
    'duck_background_audio',
    'normalize_loudness',
    'add_narration',
    'add_narration_segments',
    'add_subtitles',
    'generate_video',
    'export_video',
    'search_bilibili_video',
    'download_bilibili_video',
    'rank_video_candidates',
    'API_KEY',
    'BASE_URL',
    'MODEL_NAME',
    'VIDEO_API_KEY',
    'VIDEO_BASE_URL',
    'VIDEO_MODEL_NAME',
    'TTS_API_KEY',
    'TTS_BASE_URL',
    'TTS_MODEL_NAME',
]
