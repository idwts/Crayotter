from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from .fixture import load_fixture


KNOWN_PARAMETER_SCHEMAS = {
    "inspect_video_duration": {
        "type": "object",
        "properties": {
            "video_path": {
                "type": "string",
                "description": (
                    "Workspace-relative or absolute path to the video file to inspect. "
                    "For fixture material use paths under user_temp/materials/."
                ),
            }
        },
        "required": ["video_path"],
        "additionalProperties": False,
    }
}


KNOWN_PROPERTY_SCHEMAS = {
    ("add_transition", "transition_plan"): {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": True,
        },
        "description": (
            "Optional per-cut transition plan. Each item may contain transition_type, "
            "duration, and related transition metadata."
        ),
    }
}


def _get_phase3_tool_names() -> list[str]:
    from .tool_catalog import get_phase3_tool_names

    tool_names = get_phase3_tool_names()
    if not tool_names:
        raise RuntimeError("Crayotter Phase 3 tool catalog is empty")
    return tool_names


def _get_openai_tool_schemas(tool_names: list[str]) -> list[dict]:
    from .tool_catalog import get_openai_tool_schemas

    schemas = get_openai_tool_schemas(tool_names)
    schema_names = {
        str(schema.get("function", {}).get("name") or "")
        for schema in schemas
        if isinstance(schema, dict)
    }
    missing = sorted(set(tool_names) - schema_names)
    extra = sorted(schema_names - set(tool_names))
    if missing or extra:
        raise RuntimeError(
            f"Phase 3 tool schema mismatch: missing={missing}, extra={extra}"
        )
    for schema in schemas:
        function = schema.get("function", {})
        name = function.get("name")
        if not name or not isinstance(function.get("parameters"), dict):
            raise RuntimeError(f"Invalid Phase 3 tool schema: {schema!r}")
        if name in KNOWN_PARAMETER_SCHEMAS:
            function["parameters"] = KNOWN_PARAMETER_SCHEMAS[name]
        _normalize_parameters_schema(name, function.get("parameters"))
    return schemas


def _normalize_parameters_schema(tool_name: str | None, schema: object) -> None:
    if not isinstance(schema, dict):
        return
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for property_name, property_schema in properties.items():
            if not isinstance(property_schema, dict):
                continue
            known_schema = KNOWN_PROPERTY_SCHEMAS.get((tool_name or "", property_name))
            if known_schema is not None:
                property_schema.clear()
                property_schema.update(known_schema)
            else:
                _collapse_nullable_schema(property_schema)
                if not any(key in property_schema for key in ("type", "anyOf", "oneOf", "allOf", "enum")):
                    property_schema["type"] = "string"
            _normalize_parameters_schema(tool_name, property_schema)
    items = schema.get("items")
    if isinstance(items, dict):
        _normalize_parameters_schema(tool_name, items)


def _collapse_nullable_schema(schema: dict) -> None:
    for union_key in ("anyOf", "oneOf"):
        variants = schema.get(union_key)
        if not isinstance(variants, list):
            continue
        typed_variants = [
            variant.get("type")
            for variant in variants
            if isinstance(variant, dict) and variant.get("type") != "null"
        ]
        if len(typed_variants) == 1:
            schema["type"] = typed_variants[0]
            schema.pop(union_key, None)
        elif typed_variants:
            schema["type"] = "string"
            schema.pop(union_key, None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a verl native tool config from Crayotter tool schemas.")
    parser.add_argument(
        "--fixture",
        default="",
        help="Backward-compatible single fixture id.",
    )
    parser.add_argument(
        "--fixtures",
        nargs="*",
        default=[],
        help="Fixture ids whose allowed tool sets should be merged.",
    )
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "generated" / "tool_config.yaml"))
    args = parser.parse_args()

    if args.fixture and args.fixtures:
        parser.error("Use either --fixture or --fixtures, not both.")
    if args.fixtures:
        tool_names = list(
            dict.fromkeys(
                tool_name
                for fixture_id in args.fixtures
                for tool_name in load_fixture(fixture_id).allowed_tools
            )
        )
    elif args.fixture:
        tool_names = load_fixture(args.fixture).allowed_tools
    else:
        tool_names = _get_phase3_tool_names()

    schemas = _get_openai_tool_schemas(tool_names)
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
