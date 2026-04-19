from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from .fixture import load_fixture
from .tool_catalog import get_openai_tool_schemas, get_phase3_tool_names


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a verl native tool config from Crayotter tool schemas.")
    parser.add_argument("--fixture", default="", help="Optional fixture id to use fixture.allowed_tools.")
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "generated" / "tool_config.yaml"))
    args = parser.parse_args()

    if args.fixture:
        tool_names = load_fixture(args.fixture).allowed_tools
    else:
        tool_names = get_phase3_tool_names()

    schemas = get_openai_tool_schemas(tool_names)
    payload = {"tools": []}
    for schema in schemas:
        payload["tools"].append(
            {
                "class_name": "phase3_rl.verl_tools.CrayotterSubprocessTool",
                "config": {
                    "type": "native",
                    "tool_name": schema["function"]["name"],
                },
                "tool_schema": schema,
            }
        )

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
