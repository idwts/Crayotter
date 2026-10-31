from __future__ import annotations

import unittest
from langchain_core.tools import tool

from script.tool_protocol import ToolRegistry, normalize_tool_result


@tool
def echo(value: str, count: int = 1) -> str:
    """Repeat text for contract tests."""
    return value * count


class ToolProtocolTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry([echo], phase_names={"phase3": {"echo"}})

    def test_legacy_adapter_retains_result_and_arguments(self):
        legacy = self.registry.langchain_tools()[0]
        self.assertIs(legacy, echo)
        result = self.registry.call("echo", {"value": "hello", "count": 2})
        self.assertEqual(result.text, legacy.invoke({"value": "hello", "count": 2}))
        self.assertEqual(result.status, "success")

    def test_schemas_share_typed_function_signature(self):
        definition = self.registry.definitions()[0]
        descriptor = definition.to_mcp()
        self.assertEqual(definition.phases, ("phase3",))
        self.assertEqual(descriptor["inputSchema"]["required"], ["value"])
        self.assertFalse(descriptor["inputSchema"]["additionalProperties"])
        self.assertIn("status", descriptor["outputSchema"]["properties"])

    def test_unknown_missing_extra_and_wrong_type_arguments(self):
        for name, arguments, code in [
            ("missing", {}, "unknown_tool"), ("echo", {}, "invalid_arguments"),
            ("echo", {"value": "hi", "unexpected": 1}, "invalid_arguments"),
            ("echo", {"value": "hi", "count": "2"}, "invalid_arguments"),
            ("echo", [], "invalid_arguments"),
        ]:
            with self.subTest(name=name, arguments=arguments):
                result = self.registry.call(name, arguments)
                self.assertTrue(result.is_error)
                self.assertEqual(result.error.code, code)

    def test_error_normalization_does_not_scan_analysis_body(self):
        for text in ["剪辑出错: invalid path", "检测失败: missing file", '{"status":"error","message":"bad input"}']:
            self.assertTrue(normalize_tool_result(text).is_error)
        for text in ["分析结果: 视频描绘失败后重试的故事", '{"status":"success","description":"error example"}']:
            self.assertFalse(normalize_tool_result(text).is_error)

    def test_json_data_and_exceptions_are_structured(self):
        result = normalize_tool_result('{"status":"success","path":"/real/output.mp4"}')
        self.assertEqual(result.data["path"], "/real/output.mp4")
        @tool
        def broken() -> str:
            """Fail deliberately."""
            raise RuntimeError("render failed")
        result = ToolRegistry([broken]).call("broken")
        self.assertTrue(result.is_error)
        self.assertEqual(result.error.message, "render failed")

    def test_duplicate_names_are_rejected(self):
        with self.assertRaises(ValueError):
            ToolRegistry([echo, echo])


if __name__ == "__main__":
    unittest.main()
