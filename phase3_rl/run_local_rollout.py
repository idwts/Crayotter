from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .fixture import list_fixtures, load_fixture
from .local_env import Phase3RolloutEnv
from .policies import OpenAIToolPolicy, ScriptedPolicy
from .tool_runtime import load_api_config_from_env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local Phase 3 rollout with reward and trace dump.")
    parser.add_argument("--fixture", default="local_smoke", help="Fixture id or absolute path to fixture.json")
    parser.add_argument(
        "--policy",
        choices=("scripted", "openai"),
        default="scripted",
        help="Use the fixture scripted policy or a real OpenAI-compatible model.",
    )
    parser.add_argument(
        "--episode-base-dir",
        default=str(Path(__file__).resolve().parent / "runs" / "local"),
        help="Directory that stores per-episode runtime roots.",
    )
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--tool-timeout-seconds", type=int, default=900)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model-name", default="")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--list-fixtures", action="store_true")
    return parser.parse_args()


def build_policy(args: argparse.Namespace, fixture) -> object:
    if args.policy == "scripted":
        return ScriptedPolicy(fixture.scripted_turns)

    api_config = load_api_config_from_env()
    api_key = args.api_key or api_config["api_key"]
    base_url = args.base_url or api_config["base_url"]
    model_name = args.model_name or api_config["model_name"]
    return OpenAIToolPolicy(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )


def main() -> int:
    args = parse_args()
    if args.list_fixtures:
        print(json.dumps({"fixtures": list_fixtures()}, ensure_ascii=False, indent=2))
        return 0

    fixture = load_fixture(args.fixture)
    policy = build_policy(args, fixture)
    env = Phase3RolloutEnv(
        fixture=fixture,
        policy=policy,
        episode_base_dir=args.episode_base_dir,
        python_executable=sys.executable,
        tool_timeout_seconds=args.tool_timeout_seconds,
    )
    result = env.run(max_turns=args.max_turns)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
