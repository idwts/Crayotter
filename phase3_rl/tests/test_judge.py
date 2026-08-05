from __future__ import annotations

import unittest

from phase3_rl.judge import JudgeConfig, _parse_json_object, judge_episode


class JudgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_judge_does_not_require_api_key(self) -> None:
        result = await judge_episode(
            user_request="test",
            target_duration_seconds=5.0,
            editing_blueprint="",
            tool_events=[],
            final_output="",
            final_video_path="",
            config=JudgeConfig(
                enabled=False,
                api_key="",
                base_url="https://example.invalid/v1",
                model="judge",
                timeout_seconds=5.0,
                max_frames=0,
            ),
        )

        self.assertEqual(result, {"enabled": False, "reason": "judge_disabled"})

    def test_json_parser_accepts_fenced_response(self) -> None:
        parsed = _parse_json_object('```json\n{"score": 72, "critique": "ok"}\n```')
        self.assertEqual(parsed["score"], 72)


if __name__ == "__main__":
    unittest.main()
