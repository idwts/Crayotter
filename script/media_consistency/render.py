from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import subprocess
from typing import Callable

from .probe import MediaProbe, probe_media
from .profile import MediaProfile
from .quality import _last_loudnorm_json

# AAC reconstruction can exceed the PCM true peak; reserve encoding headroom.
AAC_PEAK_HEADROOM_DB = 0.5

@dataclass(frozen=True)
class CanonicalRenderRequest:
    input_path: str | Path
    output_path: str | Path
    profile: MediaProfile
    probe: MediaProbe
    start_seconds: float = 0.0
    duration_seconds: float | None = None
    final_mix: bool = False
    allow_hdr_tonemap: bool = False
    preset: str = "veryfast"
    crf: int = 20
    overwrite: bool = True


def _video_filter(profile: MediaProfile, *, hdr: bool) -> str:
    width, height = profile.width, profile.height
    common = (
        f"fps={profile.fps},setsar=1,format={profile.pixel_format},"
        "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
    tone_map = ""
    if hdr:
        tone_map = (
            "zscale=t=linear:npl=100,format=gbrpf32le,"
            "tonemap=tonemap=hable:desat=0,"
            "zscale=p=bt709:t=bt709:m=bt709:r=tv,"
        )
    if profile.fit_mode == "cover":
        return (
            f"[0:v]{tone_map}scale={width}:{height}:force_original_aspect_ratio=increase:"
            f"flags=lanczos,crop={width}:{height},{common}[vout]"
        )
    return (
        f"[0:v]{tone_map}split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase:"
        f"flags=bicubic,crop={width}:{height},gblur=sigma=30[bg];"
        f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease:"
        "flags=lanczos[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,"
        f"{common}[vout]"
    )


def build_canonical_render_command(
    request: CanonicalRenderRequest,
    *,
    ffmpeg_bin: str = "ffmpeg",
    loudness_measurements: dict | None = None,
) -> list[str]:
    """Build, but do not execute, a canonical FFmpeg render command."""
    if request.probe.is_hdr and not request.allow_hdr_tonemap:
        raise ValueError("HDR input requires explicit allow_hdr_tonemap=True")
    if request.start_seconds < 0:
        raise ValueError("start_seconds cannot be negative")
    source_duration = float(request.probe.duration_seconds)
    if source_duration <= 0:
        raise ValueError("source duration must be positive")
    available_duration = source_duration - float(request.start_seconds)
    if available_duration <= 0.001:
        raise ValueError(
            "render start is outside source duration: "
            f"start={request.start_seconds:.3f}s, source={source_duration:.3f}s"
        )
    duration = (
        float(request.duration_seconds)
        if request.duration_seconds is not None
        else available_duration
    )
    if duration <= 0:
        raise ValueError("render duration must be positive")
    duration = min(duration, available_duration)
    if not 0 <= int(request.crf) <= 51:
        raise ValueError("crf must be between 0 and 51")

    command = [ffmpeg_bin, "-y" if request.overwrite else "-n"]
    if request.start_seconds:
        command.extend(["-ss", f"{request.start_seconds:.3f}"])
    command.extend(["-t", f"{duration:.3f}", "-i", str(request.input_path)])
    if not request.probe.has_audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-t",
                f"{duration:.3f}",
                "-i",
                "anullsrc=r=48000:cl=stereo",
            ]
        )

    loudness = (
        request.profile.final_loudness_lufs
        if request.final_mix
        else request.profile.clip_loudness_lufs
    )
    audio_input = "0:a:0" if request.probe.has_audio else "1:a:0"
    peak_target = request.profile.true_peak_dbfs - (
        AAC_PEAK_HEADROOM_DB if request.final_mix and request.profile.audio_codec == "aac" else 0.0
    )
    normalization = ""
    if request.probe.has_audio:
        normalization = (
            f"loudnorm=I={loudness:.1f}:TP={peak_target:.1f}:"
            f"LRA={request.profile.loudness_range:.1f}"
        )
        if loudness_measurements is not None:
            values = {name: float(loudness_measurements[key]) for name, key in {
                "measured_I": "input_i", "measured_TP": "input_tp",
                "measured_LRA": "input_lra", "measured_thresh": "input_thresh",
                "offset": "target_offset",
            }.items()}
            if values["measured_I"] == values["measured_TP"] == -math.inf:
                normalization = ""  # Silence has no finite loudness to normalize.
            elif not all(math.isfinite(value) for value in values.values()):
                raise ValueError("Final audio loudness could not be measured")
            else:
                normalization += "".join(f":{name}={value:.6f}" for name, value in values.items()) + ":linear=true"
    audio_filter = (
        f"[{audio_input}]aresample={request.profile.audio_sample_rate},"
        "aformat=sample_fmts=fltp:channel_layouts=stereo"
        + (f",{normalization}" if normalization else "") + "[aout]"
    )
    filters = _video_filter(request.profile, hdr=request.probe.is_hdr) + ";" + audio_filter
    command.extend(
        [
            "-filter_complex",
            filters,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            request.profile.video_codec,
            "-preset",
            request.preset,
            "-crf",
            str(request.crf),
            "-pix_fmt",
            request.profile.pixel_format,
            "-color_primaries",
            request.profile.color_primaries,
            "-color_trc",
            request.profile.color_transfer,
            "-colorspace",
            request.profile.color_space,
            "-c:a",
            request.profile.audio_codec,
            "-ar",
            str(request.profile.audio_sample_rate),
            "-ac",
            str(request.profile.audio_channels),
            "-movflags",
            "+faststart",
            "-shortest",
            str(request.output_path),
        ]
    )
    return command


Runner = Callable[..., subprocess.CompletedProcess[str]]
Probe = Callable[[str | Path], MediaProbe]


def render_canonical_media(
    request: CanonicalRenderRequest,
    *,
    ffmpeg_bin: str = "ffmpeg",
    runner: Runner = subprocess.run,
    probe_fn: Probe = probe_media,
    timeout_seconds: float = 1200.0,
) -> Path:
    """Execute a pre-specified canonical render without registering artifacts."""
    source = Path(request.input_path).resolve(strict=False)
    if not source.is_file():
        raise FileNotFoundError(source)
    output = Path(request.output_path).resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = build_canonical_render_command(request, ffmpeg_bin=ffmpeg_bin)
    if request.final_mix and request.probe.has_audio:
        filters = command[command.index("-filter_complex") + 1]
        loudnorm = filters.split("loudnorm=", 1)[1].split("[aout]", 1)[0]
        measurement_command = command[:command.index("-filter_complex")] + [
            "-map", "0:a:0", "-vn", "-af", f"loudnorm={loudnorm}:print_format=json",
            "-f", "null", "-",
        ]
        measured = runner(measurement_command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        if measured.returncode != 0:
            raise RuntimeError(f"Audio loudness measurement failed: {(measured.stderr or '')[-1600:]}")
        measurements = _last_loudnorm_json((measured.stderr or "") + (measured.stdout or ""))
        command = build_canonical_render_command(request, ffmpeg_bin=ffmpeg_bin, loudness_measurements=measurements)
    completed = runner(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown ffmpeg error")[-1600:]
        raise RuntimeError(f"canonical render failed: {detail}")
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("canonical render produced no output file")
    try:
        output_probe = probe_fn(output)
    except Exception as exc:
        raise RuntimeError(f"canonical render produced invalid media: {exc}") from exc
    if output_probe.duration_seconds <= 0.001:
        raise RuntimeError("canonical render produced a zero-duration video")
    return output
