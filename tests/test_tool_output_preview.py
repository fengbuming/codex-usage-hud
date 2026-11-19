import pytest

from codex_usage_hud.core.parser import JsonlSessionParser, _activity_from_record


@pytest.mark.parametrize("output_type", ["function_call_output", "custom_tool_call_output"])
def test_structured_tool_return_stays_in_activity_and_round_history(output_type):
    output = {"result": {"count": 42, "passed": True}}
    payload = {"type": output_type, "call_id": "audit-call", "output": output}
    item = {"type": "response_item", "payload": payload}

    assert _activity_from_record(item).detail == f"audit-call {output}"
    assert JsonlSessionParser().round_activity_entry(item["type"], output_type, payload) == (
        "input", "工具返回", str(output),
    )


def test_structured_tool_preview_bounds_large_fields_and_hides_nested_images():
    class LargeText(str):
        def __repr__(self) -> str:
            raise AssertionError("large field must be sliced before formatting")

    parser = JsonlSessionParser()
    payload = {
        "type": "function_call_output",
        "output": {"result": {
            "preview": {"image_url": "data:image/png;base64,IMAGE_SECRET" + "A" * 2_000_000},
            "log": LargeText("log-start " + "B" * 2_000_000),
        }},
    }
    entry = parser.round_activity_entry("response_item", payload["type"], payload)
    assert entry is not None
    text = entry[2]
    assert "[图片输出]" in text and "log-start" in text
    assert "IMAGE_SECRET" not in text
    assert len(text) <= 2000 and text.endswith("...")

    payload["output"] = "standard text " * 2000
    assert parser.round_activity_entry("response_item", payload["type"], payload) == (
        "input", "工具返回", payload["output"].rstrip(),
    )
