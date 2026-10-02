from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase3_rl.reward import (
    _medium_semantic_grounding_reward,
    build_tool_signature,
    classify_tool_stage,
    compute_episode_reward,
    compute_stage_credit,
    compute_step_reward,
)
from phase3_rl.preference_credit import (
    ALLOCATOR_VERSION,
    _preference_training_feature_maps,
    _quantize_conserved_allocations,
    _relative_rank_advantages,
    _segment_features,
    annotate_group_relative_preference_credit,
)
from phase3_rl.segment_credit import build_contiguous_segments, compute_segment_credit
from phase3_rl.tool_runtime import ToolExecutionResult


def _execution(
    *,
    tool_name: str,
    arguments: dict,
    success: bool = True,
    output_paths: list[str] | None = None,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        arguments=arguments,
        raw_result="{}",
        parsed_result={},
        success=success,
        returncode=0 if success else 1,
        stdout="",
        stderr="",
        output_paths=output_paths or [],
    )


class StepRewardTests(unittest.TestCase):
    def test_known_tools_are_mapped_to_editing_stages(self) -> None:
        self.assertEqual(classify_tool_stage("cut_video"), "rough_cut")
        self.assertEqual(classify_tool_stage("add_subtitles"), "subtitle_narration")
        self.assertEqual(classify_tool_stage("missing_tool"), "other")

    def test_repeated_call_uses_canonical_signature(self) -> None:
        arguments = {"end": 3, "start": 1}
        execution = _execution(tool_name="cut_video", arguments=arguments)
        prior_events = [
            {
                "tool_name": "cut_video",
                "success": True,
                "signature": build_tool_signature(
                    "cut_video",
                    {"start": 1, "end": 3},
                ),
            }
        ]

        reward = compute_step_reward(
            tool_name="cut_video",
            execution=execution,
            prior_events=prior_events,
        )

        self.assertLess(reward.components["repeat_penalty"], 0)

    def test_medium_task_rewards_semantic_grounding_before_cut(self) -> None:
        grounded, grounded_components = _medium_semantic_grounding_reward(
            [
                {
                    "tool_name": "analyze_video",
                    "success": True,
                    "arguments": {"video_path": "user_temp/materials/a.mp4"},
                },
                {"tool_name": "cut_video", "success": True, "arguments": {}},
            ],
            {"multi_constraint_task": True},
        )
        ungrounded, _ = _medium_semantic_grounding_reward(
            [{"tool_name": "cut_video", "success": True, "arguments": {}}],
            {"multi_constraint_task": True},
        )

        self.assertGreater(grounded, 0.0)
        self.assertLess(ungrounded, 0.0)
        self.assertGreater(grounded_components["grounding_before_cut"], 0.0)

    def test_legacy_medium_horizon_metrics_enable_grounding_reward(self) -> None:
        ungrounded, components = _medium_semantic_grounding_reward(
            [{"tool_name": "cut_video", "success": True, "arguments": {}}],
            {"horizon_metrics": {"task_type": "medium_horizon_editing"}},
        )

        self.assertLess(ungrounded, 0.0)
        self.assertIn("semantic_material_grounding", components)

    def test_inspection_does_not_get_artifact_bonus_for_input_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.mp4"
            source.write_bytes(b"source")
            execution = _execution(
                tool_name="inspect_video_duration",
                arguments={"video_path": str(source)},
                output_paths=[str(source)],
            )

            reward = compute_step_reward(
                tool_name="inspect_video_duration",
                execution=execution,
                prior_events=[],
            )

        self.assertEqual(reward.components["artifact_bonus"], 0.0)


class EpisodeRewardTests(unittest.TestCase):
    def test_stage_credit_allocates_outcome_residual(self) -> None:
        events = [
            {
                "tool_name": "cut_video",
                "stage": "rough_cut",
                "success": True,
                "step_reward": 0.2,
            },
            {
                "tool_name": "export_video",
                "stage": "export_repair",
                "success": True,
                "step_reward": 0.1,
            },
        ]

        credit = compute_stage_credit(events, total_reward=1.3)

        self.assertEqual(credit["raw_step_total"], 0.3)
        self.assertAlmostEqual(credit["outcome_residual"], 1.0)
        self.assertIn("rough_cut", credit["stages"])
        self.assertGreater(credit["stages"]["rough_cut"]["stage_reward_total"], 0.2)

    def test_judge_credit_only_redistributes_without_changing_scalar_reward(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            final_video = Path(temp_dir) / "final.mp4"
            final_video.write_bytes(b"video")
            events = [
                {
                    "tool_name": "cut_video",
                    "stage": "rough_cut",
                    "success": True,
                    "output_paths": [],
                    "arguments": {},
                    "step_reward": 0.2,
                },
                {
                    "tool_name": "merge_videos",
                    "stage": "timeline_ordering",
                    "success": True,
                    "output_paths": [],
                    "arguments": {},
                    "step_reward": 0.2,
                },
                {
                    "tool_name": "export_video",
                    "stage": "export_repair",
                    "success": True,
                    "output_paths": [str(final_video)],
                    "arguments": {"output_path": str(final_video)},
                    "step_reward": 0.2,
                },
            ]

            with patch.dict("os.environ", {"CRAYOTTER_RL_JUDGE_CREDIT_ONLY": "1"}):
                reward = compute_episode_reward(
                    tool_events=events,
                    target_duration_seconds=0.0,
                    final_output="done",
                    judge_result={"score": 100},
                )

        self.assertTrue(reward["judge_credit_only"])
        self.assertEqual(reward["judge_reward"], 1.0)
        self.assertEqual(reward["judge_scalar_reward"], 0.0)
        self.assertEqual(reward["total_reward"], reward["rule_reward"])
        self.assertTrue(reward["stage_credit"]["quality_credit_enabled"])
        self.assertAlmostEqual(reward["stage_credit"]["quality_credit_allocated"], 0.0)
        self.assertNotEqual(
            reward["stage_credit"]["stages"]["timeline_ordering"]["allocated_quality_credit"],
            0.0,
        )

    def test_segment_credit_conserves_episode_return(self) -> None:
        events = [
            {"tool_name": "cut_video", "stage": "rough_cut", "success": True, "step_reward": 0.2},
            {"tool_name": "cut_video", "stage": "rough_cut", "success": True, "step_reward": 0.1},
            {"tool_name": "merge_videos", "stage": "timeline_ordering", "success": True, "step_reward": 0.2},
        ]
        stage_credit = compute_stage_credit(events, total_reward=1.4)

        segment_credit = compute_segment_credit(events, stage_credit, total_reward=1.4)

        self.assertEqual(segment_credit["segment_count"], 2)
        self.assertAlmostEqual(
            sum(item["segment_reward_total"] for item in segment_credit["segments"]),
            1.4,
            places=5,
        )

    def test_inspection_input_is_not_counted_as_new_segment_artifact(self) -> None:
        events = [
            {
                "tool_name": "inspect_video_duration",
                "stage": "validation",
                "success": True,
                "output_paths": ["source.mp4"],
                "duration_seconds": 10.0,
                "step_reward": 0.0,
            }
        ]

        segment = build_contiguous_segments(events)[0]

        self.assertEqual(segment["artifact_count"], 0)
        self.assertEqual(segment["video_artifact_count"], 0)
        self.assertEqual(segment["artifact_paths"], [])

    def test_semantic_features_are_request_conditioned_allocator_inputs(self) -> None:
        segment = {
            "segment_id": "segment_001",
            "segment_index": 1,
            "stage": "rough_cut",
            "call_count": 1,
            "success_count": 1,
            "failure_count": 0,
        }
        summary = {
            "user_request": "保留人物并加快开场节奏",
            "episode_metadata": {"long_horizon_task": True},
            "semantic_artifact_delta": {
                "segments": {
                    "segment_001": {
                        "request_fulfillment_delta": 0.8,
                        "coverage_delta": -0.2,
                        "confidence": 0.5,
                    }
                }
            },
        }

        features = _segment_features(segment, summary, 2)

        self.assertAlmostEqual(
            features["stage::rough_cut::semantic::request_fulfillment_delta"],
            0.4,
        )
        self.assertAlmostEqual(features["stage::rough_cut::semantic::coverage_delta"], -0.1)
        self.assertEqual(features["stage::rough_cut::semantic::evaluated"], 1.0)

    def test_counterfactual_preference_uses_only_suffix_segments(self) -> None:
        summary = {
            "user_request": "优化开场",
            "episode_metadata": {
                "counterfactual_prefix": {
                    "prefix_id": "prefix_a",
                    "branch_point_stage": "rough_cut",
                }
            },
            "segment_credit": {
                "segments": [
                    {"segment_id": "segment_000", "segment_index": 0, "stage": "validation"},
                    {"segment_id": "segment_001", "segment_index": 1, "stage": "rough_cut"},
                    {"segment_id": "segment_002", "segment_index": 2, "stage": "export_repair"},
                ]
            },
        }

        features = _preference_training_feature_maps(summary)

        self.assertEqual(len(features), 2)
        self.assertTrue(any(key.startswith("stage::rough_cut") for key in features[0]))
        self.assertFalse(any(key.startswith("stage::validation") for item in features for key in item))

    def test_lagged_pairwise_allocator_keeps_scalar_reward_unchanged(self) -> None:
        high_events = [
            {
                "tool_name": "cut_video",
                "stage": "rough_cut",
                "success": True,
                "step_reward": 0.2,
                "output_paths": ["clip.mp4"],
            },
            {
                "tool_name": "merge_videos",
                "stage": "timeline_ordering",
                "success": True,
                "step_reward": 0.2,
            },
            {
                "tool_name": "export_video",
                "stage": "export_repair",
                "success": True,
                "step_reward": 0.1,
            },
        ]
        low_events = [dict(item) for item in high_events]
        low_events[0] = {
            **low_events[0],
            "success": False,
            "step_reward": -0.2,
            "output_paths": [],
        }

        def make_summary(events: list[dict], score: float) -> dict:
            stage_credit = compute_stage_credit(events, total_reward=1.0)
            return {
                "total_reward": 1.0,
                "export_success": True,
                "judge_applied": True,
                "judge": {"score": score},
                "user_request": "保留主体并优化节奏后重新导出",
                "episode_metadata": {"long_horizon_task": True},
                "stage_credit": stage_credit,
                "segment_credit": compute_segment_credit(events, stage_credit, 1.0),
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "allocator.json")
            environment = {
                "CRAYOTTER_RL_SEGMENT_ALLOCATOR_ENABLED": "1",
                "CRAYOTTER_RL_SEGMENT_ALLOCATOR_STATE": state_path,
                "CRAYOTTER_RL_ALLOCATOR_WARMUP_PAIRS": "1",
                "CRAYOTTER_RL_PREFERENCE_CREDIT_MAX_ABS": "0.5",
            }
            first_low = make_summary(low_events, 40)
            first_high = make_summary(high_events, 80)
            with patch.dict("os.environ", environment):
                annotate_group_relative_preference_credit(
                    [first_low, first_high],
                    group_keys=["same_task", "same_task"],
                )
                self.assertFalse(first_high["preference_backprop"]["applied"])

                low = make_summary(low_events, 40)
                high = make_summary(high_events, 80)
                annotate_group_relative_preference_credit(
                    [low, high],
                    group_keys=["same_task", "same_task"],
                )
                low = make_summary(low_events, 40)
                high = make_summary(high_events, 80)
                annotate_group_relative_preference_credit(
                    [low, high],
                    group_keys=["same_task", "same_task"],
                )

        self.assertEqual(low["total_reward"], 1.0)
        self.assertEqual(high["total_reward"], 1.0)
        self.assertTrue(high["preference_backprop"]["applied"])
        self.assertTrue(high["preference_backprop"]["current_group_rank_used_for_policy"])
        self.assertFalse(high["preference_backprop"]["raw_judge_score_used_as_policy_reward"])
        high_credit = sum(
            item["allocated_preference_credit"]
            for item in high["segment_credit"]["segments"]
        )
        low_credit = sum(
            item["allocated_preference_credit"]
            for item in low["segment_credit"]["segments"]
        )
        self.assertGreater(high_credit, 0.0)
        self.assertLess(low_credit, 0.0)
        self.assertAlmostEqual(
            high_credit + low_credit,
            0.0,
            places=5,
        )
        self.assertGreater(high["allocator_update"]["post_update_pair_examples"], 1)

    def test_tiny_allocator_contrast_is_not_forced_to_full_credit_budget(self) -> None:
        events = [
            {"tool_name": "cut_video", "stage": "rough_cut", "success": True, "step_reward": 0.2},
            {"tool_name": "merge_videos", "stage": "timeline_ordering", "success": True, "step_reward": 0.2},
        ]
        stage_credit = compute_stage_credit(events, total_reward=1.0)
        summary = {
            "total_reward": 1.0,
            "export_success": True,
            "judge_applied": True,
            "judge": {"score": 60},
            "episode_metadata": {},
            "stage_credit": stage_credit,
            "segment_credit": compute_segment_credit(events, stage_credit, 1.0),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "allocator.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": ALLOCATOR_VERSION,
                        "pair_examples": 100,
                        "update_steps": 10,
                        "calibration_pairs": 100,
                        "calibration_accuracy_ema": 1.0,
                        "weights": {"stage::rough_cut::present": 0.001},
                        "grad_sq": {},
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {
                    "CRAYOTTER_RL_SEGMENT_ALLOCATOR_ENABLED": "1",
                    "CRAYOTTER_RL_SEGMENT_ALLOCATOR_STATE": str(state_path),
                    "CRAYOTTER_RL_ALLOCATOR_WARMUP_PAIRS": "1",
                    "CRAYOTTER_RL_ALLOCATOR_MIN_RELIABILITY": "0",
                    "CRAYOTTER_RL_PREFERENCE_CREDIT_MAX_ABS": "0.5",
                },
            ):
                annotate_group_relative_preference_credit(
                    [summary],
                    group_keys=["single"],
                    update_allocator=False,
                )

        allocations = [
            abs(item["allocated_preference_credit"])
            for item in summary["segment_credit"]["segments"]
        ]
        self.assertLess(sum(allocations), 0.01)

    def test_frozen_allocator_does_not_update_state(self) -> None:
        events = [
            {"tool_name": "cut_video", "stage": "rough_cut", "success": True, "step_reward": 0.2},
            {"tool_name": "export_video", "stage": "export_repair", "success": True, "step_reward": 0.2},
        ]

        def summary(score: float) -> dict:
            stage_credit = compute_stage_credit(events, total_reward=1.0)
            return {
                "total_reward": 1.0,
                "export_success": True,
                "judge_applied": True,
                "judge": {"score": score},
                "stage_credit": stage_credit,
                "segment_credit": compute_segment_credit(events, stage_credit, 1.0),
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "allocator.json"
            environment = {
                "CRAYOTTER_RL_SEGMENT_ALLOCATOR_ENABLED": "1",
                "CRAYOTTER_RL_SEGMENT_ALLOCATOR_STATE": str(state_path),
            }
            with patch.dict("os.environ", environment):
                annotate_group_relative_preference_credit(
                    [summary(20), summary(80)],
                    group_keys=["same", "same"],
                    update_allocator=False,
                )
            self.assertFalse(state_path.exists())

    def test_rank_advantage_backprop_is_group_zero_sum(self) -> None:
        scores = [20.0, 40.0, 60.0, 80.0]
        proxies = [-0.8, -0.2, 0.2, 0.8]
        summaries = []
        for index, (score, proxy) in enumerate(zip(scores, proxies)):
            events = [
                {"tool_name": "cut_video", "stage": "rough_cut", "success": True, "step_reward": 0.2},
                {"tool_name": "export_video", "stage": "export_repair", "success": True, "step_reward": 0.2},
            ]
            stage_credit = compute_stage_credit(events, total_reward=1.0)
            summary = {
                "total_reward": 1.0,
                "export_success": True,
                "judge_applied": True,
                "judge": {"score": score},
                "episode_metadata": {
                    "counterfactual_prefix": {
                        "prefix_id": "shared_prefix",
                        "id": f"branch_{index}",
                        "branch_point_event_index": 0,
                    }
                },
                "stage_credit": stage_credit,
                "segment_credit": compute_segment_credit(events, stage_credit, 1.0),
                "semantic_artifact_delta": {
                    "enabled": True,
                    "eligible": True,
                    "segments": {
                        "segment_000": {
                            dimension: proxy for dimension in (
                                "request_fulfillment_delta",
                                "coverage_delta",
                                "narrative_delta",
                                "pacing_delta",
                                "preservation_delta",
                                "visual_quality_delta",
                            )
                        }
                    },
                },
            }
            summary["semantic_artifact_delta"]["segments"]["segment_000"]["confidence"] = 1.0
            summaries.append(summary)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "allocator.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": ALLOCATOR_VERSION,
                        "pair_examples": 100,
                        "update_steps": 10,
                        "calibration_pairs": 100,
                        "calibration_accuracy_ema": 1.0,
                        "calibration_log_loss_ema": 0.1,
                        "weights": {
                            "stage::rough_cut::semantic::request_fulfillment_delta": 1.0,
                            "stage::export_repair::present": -0.2,
                        },
                        "grad_sq": {},
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {
                    "CRAYOTTER_RL_SEGMENT_ALLOCATOR_ENABLED": "1",
                    "CRAYOTTER_RL_SEGMENT_ALLOCATOR_STATE": str(state_path),
                    "CRAYOTTER_RL_PREFERENCE_GROUP_MIN_SIZE": "3",
                    "CRAYOTTER_RL_RANK_TIE_EPSILON": "3",
                    "CRAYOTTER_RL_ALLOCATOR_WARMUP_PAIRS": "1",
                    "CRAYOTTER_RL_ALLOCATOR_MIN_RELIABILITY": "0.01",
                },
            ):
                annotate_group_relative_preference_credit(
                    summaries,
                    group_keys=["same"] * 4,
                    update_allocator=True,
                )

        credits = [
            summary["segment_credit"]["preference_credit_sum"]
            for summary in summaries
        ]
        self.assertAlmostEqual(sum(credits), 0.0, places=5)
        self.assertLess(credits[0], 0.0)
        self.assertGreater(credits[-1], 0.0)
        self.assertTrue(all(summary["total_reward"] == 1.0 for summary in summaries))
        self.assertTrue(all(summary["group_preference_backprop"]["applied"] for summary in summaries))

    def test_quantized_preference_credit_preserves_target_return(self) -> None:
        allocations = _quantize_conserved_allocations(
            [-0.116666, -0.116666, -0.116666],
            -0.35,
            max_segment_abs=0.35,
        )

        self.assertEqual(sum(round(value * 1_000_000) for value in allocations), -350_000)
        self.assertTrue(all(abs(value) <= 0.35 for value in allocations))

    def test_relative_rank_advantage_is_tie_aware_and_zero_sum(self) -> None:
        advantages = _relative_rank_advantages([80.0, 60.0, 59.0, 20.0], tie_epsilon=3.0)
        self.assertEqual(advantages, [1.0, 0.0, 0.0, -1.0])
        self.assertAlmostEqual(sum(advantages), 0.0)

    def test_no_group_centering_uses_pairwise_win_rate(self) -> None:
        advantages = _relative_rank_advantages(
            [80.0, 60.0, 59.0, 20.0],
            tie_epsilon=3.0,
            center=False,
        )

        self.assertEqual(advantages, [1.0, 1.0 / 3.0, 1.0 / 3.0, 0.0])
        self.assertGreater(sum(advantages), 0.0)

    def test_terminal_and_uniform_variants_change_only_segment_allocation(self) -> None:
        events = [
            {
                "tool_name": "cut_video",
                "stage": "rough_cut",
                "success": True,
                "step_reward": 0.2,
                "output_paths": ["clip.mp4"],
            },
            {
                "tool_name": "export_video",
                "stage": "export_repair",
                "success": True,
                "step_reward": 0.2,
                "output_paths": ["final.mp4"],
            },
        ]

        def summary(score: float) -> dict:
            stage_credit = compute_stage_credit(events, total_reward=1.0)
            return {
                "total_reward": 1.0,
                "export_success": True,
                "judge_applied": True,
                "judge": {"score": score, "eligible_for_preference": True},
                "stage_credit": stage_credit,
                "segment_credit": compute_segment_credit(events, stage_credit, 1.0),
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            common = {
                "CRAYOTTER_RL_SEGMENT_ALLOCATOR_ENABLED": "1",
                "CRAYOTTER_RL_SEGMENT_ALLOCATOR_STATE": str(Path(temp_dir) / "allocator.json"),
                "CRAYOTTER_RL_PREFERENCE_GROUP_MIN_SIZE": "2",
                "CRAYOTTER_RL_RANK_CREDIT_MAX_RETURN": "0.35",
            }
            low_terminal, high_terminal = summary(20.0), summary(80.0)
            with patch.dict(
                "os.environ",
                {**common, "CRAYOTTER_RL_PREFERENCE_VARIANT": "terminal_rank"},
            ):
                annotate_group_relative_preference_credit(
                    [low_terminal, high_terminal],
                    group_keys=["same", "same"],
                    update_allocator=False,
                    apply_policy_credit=True,
                )

            terminal_credits = [
                segment["allocated_preference_credit"]
                for segment in high_terminal["segment_credit"]["segments"]
            ]
            self.assertEqual(terminal_credits[:-1], [0.0] * (len(terminal_credits) - 1))
            self.assertAlmostEqual(terminal_credits[-1], 0.35, places=6)
            self.assertEqual(
                high_terminal["preference_backprop"]["strategy"],
                "terminal_rank_advantage",
            )

            allocator_path = Path(common["CRAYOTTER_RL_SEGMENT_ALLOCATOR_STATE"])
            allocator_path.write_text(
                json.dumps(
                    {
                        "version": ALLOCATOR_VERSION,
                        "pair_examples": 100,
                        "update_steps": 10,
                        "calibration_pairs": 100,
                        "calibration_accuracy_ema": 1.0,
                        "calibration_log_loss_ema": 0.1,
                        "weights": {},
                        "grad_sq": {},
                    }
                ),
                encoding="utf-8",
            )
            low_uniform, high_uniform = summary(20.0), summary(80.0)
            with patch.dict(
                "os.environ",
                {
                    **common,
                    "CRAYOTTER_RL_PREFERENCE_VARIANT": "uniform",
                    "CRAYOTTER_RL_ALLOCATOR_WARMUP_PAIRS": "1",
                    "CRAYOTTER_RL_ALLOCATOR_MIN_RELIABILITY": "0",
                },
            ):
                annotate_group_relative_preference_credit(
                    [low_uniform, high_uniform],
                    group_keys=["same", "same"],
                    update_allocator=False,
                    apply_policy_credit=True,
                )

            uniform_credits = [
                segment["allocated_preference_credit"]
                for segment in high_uniform["segment_credit"]["segments"]
            ]
            self.assertTrue(uniform_credits)
            self.assertAlmostEqual(max(uniform_credits), min(uniform_credits), places=6)
            self.assertAlmostEqual(sum(uniform_credits), 0.35, places=6)
            self.assertEqual(
                high_uniform["preference_backprop"]["strategy"],
                "uniform_segment_rank_advantage",
            )
            self.assertFalse(
                high_uniform["preference_backprop"]["raw_judge_score_used_as_policy_reward"]
            )

    def test_grpb_ablation_switches_isolate_lag_gate_and_cap(self) -> None:
        high_events = [
            {
                "tool_name": "cut_video",
                "stage": "rough_cut",
                "success": True,
                "step_reward": 0.2,
                "output_paths": ["clip.mp4"],
            },
            {
                "tool_name": "export_video",
                "stage": "export_repair",
                "success": True,
                "step_reward": 0.2,
                "output_paths": ["final.mp4"],
            },
        ]
        low_events = [dict(item) for item in high_events]
        low_events[0] = {**low_events[0], "success": False, "output_paths": []}

        def summary(events: list[dict], score: float) -> dict:
            stage_credit = compute_stage_credit(events, total_reward=1.0)
            return {
                "total_reward": 1.0,
                "export_success": True,
                "judge_applied": True,
                "judge": {"score": score, "eligible_for_preference": True},
                "stage_credit": stage_credit,
                "segment_credit": compute_segment_credit(events, stage_credit, 1.0),
            }

        def write_state(path: Path, *, pairs: int, accuracy: float, weights: dict) -> None:
            path.write_text(
                json.dumps(
                    {
                        "version": ALLOCATOR_VERSION,
                        "pair_examples": pairs,
                        "update_steps": 3,
                        "calibration_pairs": 100,
                        "calibration_accuracy_ema": accuracy,
                        "calibration_log_loss_ema": 0.2,
                        "weights": weights,
                        "grad_sq": {},
                    }
                ),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "allocator.json"
            common = {
                "CRAYOTTER_RL_SEGMENT_ALLOCATOR_ENABLED": "1",
                "CRAYOTTER_RL_SEGMENT_ALLOCATOR_STATE": str(state_path),
                "CRAYOTTER_RL_PREFERENCE_GROUP_MIN_SIZE": "2",
                "CRAYOTTER_RL_ALLOCATOR_WARMUP_PAIRS": "12",
                "CRAYOTTER_RL_ALLOCATOR_MIN_RELIABILITY": "0",
            }

            write_state(state_path, pairs=11, accuracy=1.0, weights={})
            lagged = [summary(low_events, 20.0), summary(high_events, 80.0)]
            with patch.dict(
                "os.environ",
                {**common, "CRAYOTTER_RL_PREFERENCE_VARIANT": "grpb"},
            ):
                annotate_group_relative_preference_credit(
                    lagged,
                    group_keys=["same", "same"],
                )
            self.assertFalse(lagged[1]["preference_backprop"]["applied"])

            write_state(state_path, pairs=11, accuracy=1.0, weights={})
            unlagged = [summary(low_events, 20.0), summary(high_events, 80.0)]
            with patch.dict(
                "os.environ",
                {**common, "CRAYOTTER_RL_PREFERENCE_VARIANT": "no_lag"},
            ):
                annotate_group_relative_preference_credit(
                    unlagged,
                    group_keys=["same", "same"],
                )
            self.assertTrue(unlagged[1]["preference_backprop"]["applied"])
            self.assertFalse(
                unlagged[1]["preference_backprop"]["policy_used_pre_update_allocator"]
            )

            contrast_weights = {
                "stage::rough_cut::present": 1.0,
                "stage::export_repair::present": -1.0,
            }
            write_state(state_path, pairs=100, accuracy=0.5, weights=contrast_weights)
            no_gate = [summary(low_events, 20.0), summary(high_events, 80.0)]
            with patch.dict(
                "os.environ",
                {**common, "CRAYOTTER_RL_PREFERENCE_VARIANT": "no_reliability"},
            ):
                annotate_group_relative_preference_credit(
                    no_gate,
                    group_keys=["same", "same"],
                    update_allocator=False,
                    apply_policy_credit=True,
                )
            self.assertTrue(no_gate[1]["preference_backprop"]["applied"])
            self.assertFalse(no_gate[1]["preference_backprop"]["reliability_gate_enabled"])

            write_state(state_path, pairs=100, accuracy=1.0, weights=contrast_weights)
            no_cap = [summary(low_events, 20.0), summary(high_events, 80.0)]
            with patch.dict(
                "os.environ",
                {
                    **common,
                    "CRAYOTTER_RL_PREFERENCE_VARIANT": "no_cap_projection",
                    "CRAYOTTER_RL_RANK_CREDIT_MAX_SEGMENT": "0.05",
                },
            ):
                annotate_group_relative_preference_credit(
                    no_cap,
                    group_keys=["same", "same"],
                    update_allocator=False,
                    apply_policy_credit=True,
                )
            no_cap_credits = [
                abs(segment["allocated_preference_credit"])
                for segment in no_cap[1]["segment_credit"]["segments"]
            ]
            self.assertGreater(max(no_cap_credits), 0.05)
            self.assertAlmostEqual(sum(no_cap_credits), 0.35, places=6)

            write_state(state_path, pairs=100, accuracy=0.5, weights=contrast_weights)
            no_safeguards = [summary(low_events, 20.0), summary(high_events, 80.0)]
            with patch.dict(
                "os.environ",
                {
                    **common,
                    "CRAYOTTER_RL_PREFERENCE_VARIANT": "no_safeguards",
                    "CRAYOTTER_RL_RANK_CREDIT_MAX_SEGMENT": "0.05",
                },
            ):
                annotate_group_relative_preference_credit(
                    no_safeguards,
                    group_keys=["same", "same"],
                    update_allocator=False,
                    apply_policy_credit=True,
                )
            combined = no_safeguards[1]["preference_backprop"]
            combined_credits = [
                abs(segment["allocated_preference_credit"])
                for segment in no_safeguards[1]["segment_credit"]["segments"]
            ]
            self.assertTrue(combined["applied"])
            self.assertFalse(combined["reliability_gate_enabled"])
            self.assertEqual(combined["strategy"], "uncapped_allocator_rank_advantage")
            self.assertGreater(max(combined_credits), 0.05)

    def test_export_input_path_is_not_mistaken_for_final_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_video = Path(temp_dir) / "source.mp4"
            final_video = Path(temp_dir) / "final.mp4"
            source_video.write_bytes(b"source")
            final_video.write_bytes(b"final")
            events = [
                {
                    "tool_name": "export_video",
                    "success": True,
                    "arguments": {"input_path": str(source_video)},
                    "output_paths": [str(source_video), str(final_video)],
                    "step_reward": 0.0,
                }
            ]

            with patch.dict("sys.modules", {"cv2": None}):
                reward = compute_episode_reward(
                    tool_events=events,
                    target_duration_seconds=10.0,
                    final_output="done",
                )

        self.assertEqual(Path(reward["final_video_path"]), final_video)

    def test_relative_input_name_does_not_hide_exported_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            final_video = Path(temp_dir) / "same_name.mp4"
            final_video.write_bytes(b"final")
            events = [
                {
                    "tool_name": "export_video",
                    "success": True,
                    "arguments": {
                        "input_path": "same_name.mp4",
                        "output_name": "same_name",
                    },
                    "parsed_result": {"path": str(final_video)},
                    "output_paths": [str(final_video)],
                    "step_reward": 0.0,
                }
            ]

            with patch.dict("sys.modules", {"cv2": None}):
                reward = compute_episode_reward(
                    tool_events=events,
                    target_duration_seconds=10.0,
                    final_output="done",
                )

        self.assertTrue(reward["export_success"])

    def test_valid_export_and_matching_duration_receive_positive_reward(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            final_video = Path(temp_dir) / "final.mp4"
            final_video.write_bytes(b"video")
            events = [
                {
                    "tool_name": "export_video",
                    "success": True,
                    "output_paths": [str(final_video)],
                    "arguments": {"output_path": str(final_video)},
                    "step_reward": 0.1,
                },
                {
                    "tool_name": "inspect_video_duration",
                    "success": True,
                    "arguments": {"video_path": str(final_video)},
                    "duration_seconds": 10.0,
                    "step_reward": 0.05,
                },
            ]

            reward = compute_episode_reward(
                tool_events=events,
                target_duration_seconds=10.0,
                final_output="done",
            )

        self.assertTrue(reward["export_success"])
        self.assertEqual(reward["final_duration_seconds"], 10.0)
        self.assertGreater(reward["total_reward"], 0)
        self.assertIn("stage_credit", reward)

    def test_reported_export_without_file_is_hard_failure(self) -> None:
        events = [
            {
                "tool_name": "export_video",
                "success": True,
                "output_paths": ["/missing/final.mp4"],
                "arguments": {"output_path": "/missing/final.mp4"},
                "step_reward": 0.3,
            }
        ]

        reward = compute_episode_reward(
            tool_events=events,
            target_duration_seconds=10.0,
            final_output="done",
            judge_result={"score": 100},
        )

        self.assertTrue(reward["reported_export_success"])
        self.assertFalse(reward["export_success"])
        self.assertLessEqual(reward["total_reward"], -0.25)

    def test_source_duration_before_export_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            final_video = Path(temp_dir) / "final.mp4"
            final_video.write_bytes(b"not-a-real-video")
            events = [
                {
                    "tool_name": "inspect_video_duration",
                    "success": True,
                    "arguments": {"video_path": "source.mp4"},
                    "duration_seconds": 99.0,
                    "step_reward": 0.0,
                },
                {
                    "tool_name": "export_video",
                    "success": True,
                    "output_paths": [str(final_video)],
                    "arguments": {"output_path": str(final_video)},
                    "step_reward": 0.0,
                },
            ]

            with patch.dict("sys.modules", {"cv2": None}):
                reward = compute_episode_reward(
                    tool_events=events,
                    target_duration_seconds=10.0,
                    final_output="done",
                )

        self.assertIsNone(reward["final_duration_seconds"])
        self.assertEqual(reward["duration_reward"], -0.5)

    def test_long_horizon_revision_components_reward_diagnosis_and_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = Path(temp_dir) / "user_temp" / "previous_versions" / "previous_final_001_r1.mp4"
            source = Path(temp_dir) / "user_temp" / "materials" / "source.mp4"
            final_video = Path(temp_dir) / "temp" / "revision_final.mp4"
            previous.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            final_video.parent.mkdir(parents=True)
            previous.write_bytes(b"previous")
            source.write_bytes(b"source")
            final_video.write_bytes(b"final")
            events = [
                {
                    "tool_name": "inspect_video_duration",
                    "success": True,
                    "arguments": {"video_path": str(previous)},
                    "duration_seconds": 13.0,
                    "step_reward": 0.0,
                },
                {
                    "tool_name": "cut_video",
                    "success": True,
                    "arguments": {"video_path": str(source), "start": 0, "end": 5},
                    "output_paths": [str(Path(temp_dir) / "temp" / "clip.mp4")],
                    "step_reward": 0.0,
                },
                {
                    "tool_name": "export_video",
                    "success": True,
                    "arguments": {"output_path": str(final_video)},
                    "output_paths": [str(final_video)],
                    "step_reward": 0.0,
                },
                {
                    "tool_name": "inspect_video_duration",
                    "success": True,
                    "arguments": {"video_path": str(final_video)},
                    "duration_seconds": 10.0,
                    "step_reward": 0.0,
                },
            ]

            reward = compute_episode_reward(
                tool_events=events,
                target_duration_seconds=10.0,
                final_output="done",
                episode_metadata={
                    "long_horizon_task": True,
                    "previous_version_available": True,
                    "previous_final_target": "previous_versions/previous_final_001_r1.mp4",
                },
            )

        self.assertGreater(reward["long_horizon_reward"], 0)
        self.assertGreater(reward["long_horizon_components"]["revision_diagnosis"], 0)
        self.assertGreater(reward["long_horizon_components"]["source_material_reuse"], 0)

    def test_tool_call_bootstrap_reward_does_not_require_export(self) -> None:
        events = [
            {
                "tool_name": "inspect_video_duration",
                "success": True,
                "arguments": {"video_path": "user_temp/materials/source.mp4"},
                "duration_seconds": 8.0,
                "step_reward": 0.08,
            }
        ]

        reward = compute_episode_reward(
            tool_events=events,
            target_duration_seconds=10.0,
            final_output="",
            episode_metadata={
                "tool_call_bootstrap": True,
                "bootstrap_tool_name": "inspect_video_duration",
            },
        )

        self.assertTrue(reward["tool_call_bootstrap"])
        self.assertFalse(reward["export_success"])
        self.assertGreater(reward["total_reward"], 0)

    def test_tool_call_bootstrap_penalizes_no_tool_call(self) -> None:
        reward = compute_episode_reward(
            tool_events=[],
            target_duration_seconds=10.0,
            final_output="",
            episode_metadata={
                "tool_call_bootstrap": True,
                "bootstrap_tool_name": "inspect_video_duration",
            },
        )

        self.assertLess(reward["total_reward"], 0)

    def test_long_horizon_no_tool_call_hits_failure_cap(self) -> None:
        reward = compute_episode_reward(
            tool_events=[],
            target_duration_seconds=10.0,
            final_output="",
            episode_metadata={"long_horizon_task": True},
        )

        self.assertEqual(reward["failure_cap_info"]["reason"], "no_tool_call")
        terminal = reward["segment_credit"]["segments"]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["stage"], "policy_terminal")
        self.assertEqual(terminal[0]["terminal_behavior"], "no_tool_call")
        self.assertAlmostEqual(
            terminal[0]["segment_reward_total"],
            reward["total_reward"],
            places=6,
        )
        self.assertLessEqual(reward["total_reward"], -5.0)

    def test_long_horizon_shallow_no_export_is_capped(self) -> None:
        events = [
            {
                "tool_name": "inspect_video_duration",
                "success": True,
                "arguments": {"video_path": "user_temp/materials/source.mp4"},
                "duration_seconds": 8.0,
                "step_reward": 0.08,
            },
            {
                "tool_name": "recall_semantic_segments",
                "success": True,
                "arguments": {"query": "campus"},
                "step_reward": 0.08,
            },
        ]

        reward = compute_episode_reward(
            tool_events=events,
            target_duration_seconds=10.0,
            final_output="",
            episode_metadata={"long_horizon_task": True},
        )

        self.assertEqual(reward["failure_cap_info"]["reason"], "below_min_successful_tools")
        self.assertLessEqual(reward["total_reward"], -3.6)

    def test_long_horizon_complete_path_gets_milestone_credit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = Path(temp_dir) / "user_temp" / "previous_versions" / "previous_final_001_r1.mp4"
            source = Path(temp_dir) / "user_temp" / "materials" / "source.mp4"
            clip = Path(temp_dir) / "temp" / "clip.mp4"
            final_video = Path(temp_dir) / "temp" / "revision_final.mp4"
            for path in (previous, source, clip, final_video):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"video")
            events = [
                {
                    "tool_name": "inspect_video_duration",
                    "success": True,
                    "arguments": {"video_path": str(previous)},
                    "duration_seconds": 12.0,
                    "step_reward": 0.08,
                },
                {
                    "tool_name": "recall_semantic_segments",
                    "success": True,
                    "arguments": {"query": "reuse material"},
                    "step_reward": 0.08,
                },
                {
                    "tool_name": "cut_video",
                    "success": True,
                    "arguments": {"video_path": str(source), "start": 0, "end": 5},
                    "output_paths": [str(clip)],
                    "step_reward": 0.08,
                },
                {
                    "tool_name": "build_edit_timeline_from_segments",
                    "success": True,
                    "arguments": {"segments": ["clip"]},
                    "step_reward": 0.08,
                },
                {
                    "tool_name": "export_video",
                    "success": True,
                    "arguments": {"output_path": str(final_video)},
                    "output_paths": [str(final_video)],
                    "step_reward": 0.08,
                },
                {
                    "tool_name": "inspect_video_duration",
                    "success": True,
                    "arguments": {"video_path": str(final_video)},
                    "duration_seconds": 10.0,
                    "step_reward": 0.08,
                },
            ]

            reward = compute_episode_reward(
                tool_events=events,
                target_duration_seconds=10.0,
                final_output="done",
                episode_metadata={
                    "long_horizon_task": True,
                    "previous_version_available": True,
                    "previous_final_target": "previous_versions/previous_final_001_r1.mp4",
                },
            )

        self.assertGreater(reward["milestone_progress_reward"], 0)
        self.assertGreater(reward["milestone_progress_components"]["valid_new_export"], 0)
        self.assertIsNone(reward["failure_cap"])


if __name__ == "__main__":
    unittest.main()
