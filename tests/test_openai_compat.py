from sentinel.llm.openai_compat import _to_anthropic, _to_openai_messages


def test_messages_round_trip_tool_use():
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "c1", "name": "get_metric", "input": {"metric": "revenue"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": "{}", "is_error": True}]}]
    out = _to_openai_messages("sys", msgs)
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[2]["tool_calls"][0]["function"]["arguments"] == '{"metric": "revenue"}'
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "ERROR: {}"}


def test_response_to_anthropic_shape():
    r = _to_anthropic({"choices": [{"finish_reason": "tool_calls", "message": {
        "content": None, "tool_calls": [{"id": "c1", "function": {
            "name": "submit_hypotheses", "arguments": '{"hypotheses": []}'}}]}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3}})
    assert r.stop_reason == "tool_use"
    assert r.content[0].model_dump(exclude_none=True) == {
        "type": "tool_use", "id": "c1", "name": "submit_hypotheses", "input": {"hypotheses": []}}
    assert (r.usage.input_tokens, r.usage.output_tokens) == (5, 3)
