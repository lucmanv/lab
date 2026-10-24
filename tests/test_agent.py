"""Testa o ciclo do agente contra uma API simulada (sem chamadas reais)."""

import json

import anthropic
import httpx2 as httpx
import pytest

from code_doc_agent.agent import AgentError, run_agent


def sse(blocks, stop_reason):
    """Constrói uma resposta SSE da Messages API com os blocos dados."""
    events = [("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 0}}})]
    for i, block in enumerate(blocks):
        if block["type"] == "text":
            events.append(("content_block_start", {"type": "content_block_start", "index": i,
                           "content_block": {"type": "text", "text": ""}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                           "delta": {"type": "text_delta", "text": block["text"]}}))
        else:
            events.append(("content_block_start", {"type": "content_block_start", "index": i,
                           "content_block": {"type": "tool_use", "id": block["id"],
                                             "name": block["name"], "input": {}}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                           "delta": {"type": "input_json_delta",
                                     "partial_json": json.dumps(block["input"])}}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    events.append(("message_delta", {"type": "message_delta",
                   "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                   "usage": {"output_tokens": 5}}))
    events.append(("message_stop", {"type": "message_stop"}))
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


def tool(id_, name, **inp):
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


class FakeAPI:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"headers": request.headers,
                              "body": json.loads(request.content)})
        blocks, stop = self.script.pop(0)
        return httpx.Response(200, text=sse(blocks, stop),
                              headers={"content-type": "text/event-stream"})

    def client(self):
        return anthropic.Anthropic(api_key="test", max_retries=0,
                                   http_client=httpx.Client(transport=httpx.MockTransport(self)))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.py").write_text("print('ola')\n")
    return root


def test_full_loop_writes_both_documents(project, tmp_path):
    api = FakeAPI([
        ([tool("t1", "list_directory", path=".", recursive=True),
          tool("t2", "read_file", path="main.py")], "tool_use"),
        ([tool("t3", "write_document", kind="pdd", mode="write", content="# PDD")], "tool_use"),
        ([tool("t4", "write_document", kind="manual", mode="write", content="# Manual")], "tool_use"),
        ([{"type": "text", "text": "Feito."}], "end_turn"),
    ])
    out = tmp_path / "out"
    result = run_agent(project, out, client=api.client(), verbose=False)

    assert (out / "PDD.md").read_text() == "# PDD"
    assert (out / "MANUAL_UTILIZADOR.md").read_text() == "# Manual"
    assert result.summary == "Feito." and result.turns == 4

    first = api.requests[0]
    assert "server-side-fallback-2026-07-01" in first["headers"]["anthropic-beta"]
    assert first["body"]["fallbacks"] == "default"
    assert first["body"]["thinking"] == {"type": "adaptive"}
    assert first["body"]["model"] == "claude-opus-5-5"

    # Os dois resultados de ferramentas vão juntos numa só mensagem.
    second_msgs = api.requests[1]["body"]["messages"]
    results = second_msgs[-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert "main.py" in results[0]["content"]


def test_reminds_when_a_document_is_missing(project, tmp_path):
    api = FakeAPI([
        ([tool("t1", "write_document", kind="pdd", mode="write", content="# PDD")], "tool_use"),
        ([{"type": "text", "text": "Terminei."}], "end_turn"),
        ([tool("t2", "write_document", kind="manual", mode="write", content="# M")], "tool_use"),
        ([{"type": "text", "text": "Agora sim."}], "end_turn"),
    ])
    result = run_agent(project, tmp_path / "out", client=api.client(), verbose=False)
    assert set(result.documents) == {"pdd", "manual"}
    reminder = api.requests[2]["body"]["messages"][-1]["content"]
    assert "Manual de Utilizador" in reminder


def test_tool_errors_are_returned_to_model(project, tmp_path):
    api = FakeAPI([
        ([tool("t1", "read_file", path="../fora.txt")], "tool_use"),
        ([tool("t2", "write_document", kind="pdd", mode="write", content="a"),
          tool("t3", "write_document", kind="manual", mode="write", content="b")], "tool_use"),
        ([{"type": "text", "text": "ok"}], "end_turn"),
    ])
    run_agent(project, tmp_path / "out", client=api.client(), verbose=False)
    err = api.requests[1]["body"]["messages"][-1]["content"][0]
    assert err["is_error"] is True and "fora" in err["content"]


def test_truncated_tool_call_is_not_executed(project, tmp_path):
    api = FakeAPI([
        ([tool("t1", "write_document", kind="pdd", mode="write", content="cortado")], "max_tokens"),
        ([tool("t2", "write_document", kind="pdd", mode="write", content="# PDD"),
          tool("t3", "write_document", kind="manual", mode="write", content="# M")], "tool_use"),
        ([{"type": "text", "text": "ok"}], "end_turn"),
    ])
    out = tmp_path / "out"
    run_agent(project, out, client=api.client(), verbose=False)
    err = api.requests[1]["body"]["messages"][-1]["content"][0]
    assert err["is_error"] is True
    assert (out / "PDD.md").read_text() == "# PDD"


def test_refusal_stops(project, tmp_path):
    api = FakeAPI([([], "refusal")])
    with pytest.raises(AgentError, match="recusou"):
        run_agent(project, tmp_path / "out", client=api.client(), verbose=False)
