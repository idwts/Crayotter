from __future__ import annotations

import unittest

from script.media_consistency import (
    CanonicalRenderRequest,
    MediaQualityMetrics,
    build_canonical_render_command,
    build_quality_analysis_commands,
    parse_ffprobe_payload,
    parse_quality_analysis_outputs,
    resolve_media_profile,
    validate_final_media,
    validate_technical_media,
)


def _probe_payload(
    *,
    width: int = 1280,
    height: int = 720,
    fps: str = "30/1",
    nominal_fps: str = "30/1",
    audio: bool = True,
    hdr: bool = False,
) -> dict:
    streams = [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": width,
            "height": height,
            "pix_fmt": "yuv420p",
            "avg_frame_rate": fps,
            "r_frame_rate": nominal_fps,
            "sample_aspect_ratio": "1:1",
            "color_primaries": "bt2020" if hdr else "bt709",
            "color_transfer": "smpte2084" if hdr else "bt709",
            "color_space": "bt2020nc" if hdr else "bt709",
            "bit_rate": "4000000",
            "side_data_list": [{"rotation": -90}],
        }
    ]
    if audio:
        streams.append(
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "channel_layout": "stereo",
                "bit_rate": "128000",
            }
        )
    return {
        "format": {"duration": "30.000", "size": "12000000"},
        "streams": streams,
    }


class MediaProfileTests(unittest.TestCase):
    def test_duration_defaults_and_aspect(self) -> None:
        short = resolve_media_profile(30, target_aspect="9:16")
        medium = resolve_media_profile(60, target_aspect="16:9")
        long = resolve_media_profile(120, target_aspect="square")
        self.assertEqual((short.width, short.height), (720, 1280))
        self.assertEqual((medium.width, medium.height), (1920, 1080))
        self.assertEqual((long.width, long.height), (720, 720))
        self.assertEqual(short.version, "media-profile-v1")

    def test_explicit_resolution_wins_and_cover_is_preserved(self) -> None:
        profile = resolve_media_profile(
            180,
            user_resolution="1080p",
            target_aspect="9:16",
            fit_mode="cover",
        )
        self.assertEqual((profile.width, profile.height), (1080, 1920))
        self.assertEqual(profile.fit_mode, "cover")

    def test_invalid_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_media_profile(0)
        with self.assertRaises(ValueError):
            resolve_media_profile(30, target_aspect="cinematic")


class MediaProbeTests(unittest.TestCase):
    def test_parses_streams_rotation_and_vfr(self) -> None:
        probe = parse_ffprobe_payload(
            _probe_payload(fps="30000/1001", nominal_fps="30/1"), path="clip.mp4"
        )
        self.assertAlmostEqual(probe.average_fps, 29.970, places=2)
        self.assertTrue(probe.is_variable_frame_rate)
        self.assertEqual(probe.rotation_degrees, 270)
        self.assertTrue(probe.has_audio)

    def test_detects_hdr(self) -> None:
        probe = parse_ffprobe_payload(_probe_payload(hdr=True))
        self.assertTrue(probe.is_hdr)


class CanonicalRenderCommandTests(unittest.TestCase):
    def test_blur_fill_command_adds_silent_audio_and_canonical_metadata(self) -> None:
        profile = resolve_media_profile(30)
        probe = parse_ffprobe_payload(_probe_payload(audio=False))
        command = build_canonical_render_command(
            CanonicalRenderRequest("input.mp4", "output.mp4", profile, probe)
        )
        joined = " ".join(command)
        self.assertIn("anullsrc=r=48000:cl=stereo", joined)
        self.assertIn("gblur=sigma=30", joined)
        self.assertIn("fps=30", joined)
        self.assertIn("loudnorm=I=-18.0:TP=-1.5:LRA=11.0", joined)
        self.assertIn("-color_primaries bt709", joined)
        self.assertEqual(command[-1], "output.mp4")

    def test_cover_and_final_mix_change_filters(self) -> None:
        profile = resolve_media_profile(30, fit_mode="cover")
        probe = parse_ffprobe_payload(_probe_payload())
        command = build_canonical_render_command(
            CanonicalRenderRequest(
                "input.mp4", "output.mp4", profile, probe, final_mix=True
            )
        )
        filters = command[command.index("-filter_complex") + 1]
        self.assertIn("force_original_aspect_ratio=increase", filters)
        self.assertNotIn("gblur", filters)
        self.assertIn("loudnorm=I=-16.0", filters)

    def test_hdr_requires_explicit_tone_map_capability(self) -> None:
        profile = resolve_media_profile(30)
        probe = parse_ffprobe_payload(_probe_payload(hdr=True))
        with self.assertRaisesRegex(ValueError, "HDR input"):
            build_canonical_render_command(
                CanonicalRenderRequest("input.mp4", "output.mp4", profile, probe)
            )
        command = build_canonical_render_command(
            CanonicalRenderRequest(
                "input.mp4",
                "output.mp4",
                profile,
                probe,
                allow_hdr_tonemap=True,
            )
        )
        self.assertIn("tonemap=tonemap=hable", " ".join(command))


class MediaValidationTests(unittest.TestCase):
    def test_matching_canonical_media_passes(self) -> None:
        profile = resolve_media_profile(30)
        probe = parse_ffprobe_payload(_probe_payload())
        self.assertTrue(validate_technical_media(probe, profile).passed)
        quality = MediaQualityMetrics(
            integrated_lufs=-16.2,
            true_peak_dbfs=-1.8,
            black_frame_ratio=0.0,
            freeze_frame_ratio=0.01,
            decode_errors=0,
        )
        self.assertTrue(validate_final_media(probe, profile, quality).passed)

    def test_final_validation_rejects_missing_or_bad_metrics(self) -> None:
        profile = resolve_media_profile(30)
        probe = parse_ffprobe_payload(_probe_payload())
        missing = validate_final_media(probe, profile, None)
        self.assertFalse(missing.passed)
        self.assertIn("quality_metrics_missing", {item.code for item in missing.issues})

        bad = validate_final_media(
            probe,
            profile,
            MediaQualityMetrics(-11, -0.5, 0.2, 0.2, 3),
        )
        codes = {item.code for item in bad.issues}
        self.assertIn("loudness_out_of_range", codes)
        self.assertIn("true_peak_too_high", codes)
        self.assertIn("excessive_black_frames", codes)
        self.assertIn("excessive_freeze_frames", codes)
        self.assertIn("decode_errors", codes)


class QualityAnalysisTests(unittest.TestCase):
    def test_commands_are_read_only_and_metrics_are_parsed(self) -> None:
        commands = build_quality_analysis_commands("final.mp4")
        self.assertIn("loudnorm=print_format=json", commands.loudness)
        self.assertIn("blackdetect=d=0.10:pic_th=0.98", commands.black_frames)
        self.assertIn("freezedetect=n=-60dB:d=0.5", commands.freeze_frames)
        self.assertIn("-xerror", commands.decode)

        metrics = parse_quality_analysis_outputs(
            duration_seconds=20,
            loudness_output='noise\n{"input_i":"-16.20","input_tp":"-1.80"}\n',
            black_frames_output="black_start:1 black_end:2 black_duration:1.0",
            freeze_frames_output="freeze_start: 3\nfreeze_duration: 0.5",
            decode_returncode=0,
        )
        self.assertEqual(metrics.integrated_lufs, -16.2)
        self.assertEqual(metrics.true_peak_dbfs, -1.8)
        self.assertEqual(metrics.black_frame_ratio, 0.05)
        self.assertEqual(metrics.freeze_frame_ratio, 0.025)
        self.assertEqual(metrics.decode_errors, 0)


if __name__ == "__main__":
    unittest.main()
