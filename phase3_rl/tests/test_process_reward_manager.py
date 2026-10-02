from __future__ import annotations

import unittest
from types import SimpleNamespace

from phase3_rl.process_reward_manager import (
    _bounded_segment_position_rewards,
    _event_position_index,
    _is_validation_batch,
    build_process_trainer_metrics,
)


class SegmentRewardPlacementTests(unittest.TestCase):
    def test_trainer_metrics_expose_preference_and_judge_health(self) -> None:
        class Batch(SimpleNamespace):
            def __len__(self):
                return 2

        summaries = [
            {
                "total_reward": -1.0,
                "export_success": True,
                "judge_applied": True,
                "judge": {"score": 70.0, "eligible_for_preference": True},
                "group_relative_preference": {
                    "rank_advantage": 1.0,
                    "raw_judge_score_used_as_policy_reward": False,
                },
                "preference_backprop": {"applied": True},
                "segment_credit": {"preference_credit_sum": 0.2, "segments": [{}, {}]},
                "allocator_update": {
                    "eligible_group_count": 1,
                    "pair_count": 1,
                    "post_update_pair_examples": 8,
                    "post_update_steps": 3,
                    "allocator_reliability": 0.4,
                    "calibration_accuracy_ema": 0.65,
                    "calibration_log_loss_ema": 0.6,
                    "rank_advantage_applied_count": 1,
                    "rank_advantage_group_credit_drift": 0.0,
                },
            },
            {
                "total_reward": 1.0,
                "export_success": True,
                "judge_applied": True,
                "judge": {"score": 80.0, "eligible_for_preference": True},
                "group_relative_preference": {
                    "rank_advantage": -1.0,
                    "raw_judge_score_used_as_policy_reward": False,
                },
                "preference_backprop": {"applied": True},
                "segment_credit": {"preference_credit_sum": -0.2, "segments": [{}]},
                "allocator_update": {},
            },
        ]
        data = Batch(
            meta_info={},
            non_tensor_batch={
                "phase3_episode_reward": summaries,
                "phase3_fixture_path": ["same-task", "same-task"],
            },
        )

        metrics = build_process_trainer_metrics(data, global_step=11)

        self.assertEqual(metrics["global_step"], 11)
        self.assertEqual(metrics["crayotter/preference/comparable_group_count"], 1)
        self.assertEqual(metrics["crayotter/judge/within_group_spread_mean"], 10.0)
        self.assertEqual(metrics["crayotter/allocator/pair_count"], 1)
        self.assertEqual(metrics["crayotter/preference/raw_judge_policy_rate"], 0.0)
        self.assertAlmostEqual(metrics["crayotter/preference/credit_abs_mean"], 0.2)

    def test_disabled_clip_preserves_single_segment_failure_return(self) -> None:
        rewards, residual = _bounded_segment_position_rewards(
            [(6, -5.65, 0.0)],
            event_count=7,
            position_count=6,
            total_reward=-5.65,
            clip_value=0.0,
        )

        self.assertAlmostEqual(sum(rewards.values()), -5.65, places=6)
        self.assertAlmostEqual(residual, 0.0, places=6)

    def test_base_residual_is_distributed_without_moving_preference_credit(self) -> None:
        rewards, residual = _bounded_segment_position_rewards(
            [
                (0, 3.8, 0.3),
                (1, 3.8, -0.3),
                (2, 0.4, 0.0),
            ],
            event_count=3,
            position_count=3,
            total_reward=8.0,
            clip_value=4.0,
        )

        self.assertAlmostEqual(sum(rewards.values()), 8.0, places=6)
        self.assertAlmostEqual(residual, 0.0, places=6)
        self.assertAlmostEqual(rewards[0], 4.0, places=6)
        self.assertAlmostEqual(rewards[1], 3.4, places=6)
        self.assertAlmostEqual(rewards[2], 0.6, places=6)

    def test_event_positions_are_scaled_when_parallel_calls_reduce_turn_count(self) -> None:
        self.assertEqual(_event_position_index(0, 6, 4), 0)
        self.assertEqual(_event_position_index(5, 6, 4), 3)
        self.assertEqual(_event_position_index(3, 6, 4), 2)

    def test_eval_fixture_split_freezes_allocator_without_meta_flag(self) -> None:
        class Batch(SimpleNamespace):
            def __len__(self):
                return 1

        data = Batch(meta_info={}, non_tensor_batch={"data_source": ["crayotter_phase3_eval_long"]})
        summaries = [{"episode_metadata": {"horizon_suite_split": "eval"}}]

        self.assertTrue(_is_validation_batch(data, summaries))

    def test_train_fixture_split_does_not_freeze_allocator(self) -> None:
        class Batch(SimpleNamespace):
            def __len__(self):
                return 1

        data = Batch(meta_info={}, non_tensor_batch={"data_source": ["crayotter_phase3_train_long"]})
        summaries = [{"episode_metadata": {"horizon_suite_split": "train"}}]

        self.assertFalse(_is_validation_batch(data, summaries))


if __name__ == "__main__":
    unittest.main()
