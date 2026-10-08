from dataclasses import replace

from app.chat import FaqChat
from app.config import load_settings
from app.index import FaqIndex
from app.sharepoint import Page

PAGES = [
    Page("a", "休暇FAQ", "https://x/a", "", "## 有給休暇の申請\n3営業日前までに申請"),
    Page("b", "経費FAQ", "https://x/b", "", "## 経費精算の締め日\n毎月末日締め"),
]


def _chat(**overrides) -> FaqChat:
    return FaqChat(replace(load_settings(), **overrides), client=object())


def test_full_context_mode_puts_all_docs_in_cached_system():
    chat = _chat(full_context_max_chars=10_000)
    system, messages, sources = chat.build_request(FaqIndex.from_pages(PAGES), [{"role": "user", "content": "締め日は？"}])
    assert len(system) == 2 and system[1]["cache_control"] == {"type": "ephemeral"}
    assert "有給休暇" in system[1]["text"] and "経費精算" in system[1]["text"]
    assert messages == [{"role": "user", "content": "締め日は？"}]
    assert [s.number for s in sources] == [1, 2]


def test_search_mode_attaches_hits_to_latest_question_only():
    chat = _chat(full_context_max_chars=0, search_top_k=1)
    history = [
        {"role": "user", "content": "有給休暇は？"},
        {"role": "assistant", "content": "3営業日前までです [1]"},
        {"role": "user", "content": "経費精算の締め日は？"},
    ]
    system, messages, sources = chat.build_request(FaqIndex.from_pages(PAGES), history)
    assert len(system) == 1
    assert messages[:2] == history[:2]
    assert "<faq>" in messages[2]["content"] and "経費精算の締め日は？" in messages[2]["content"]
    assert [s.title for s in sources] == ["経費FAQ"]


class _FakeStream:
    def __init__(self, chunks, stop_reason):
        self._chunks, self._stop = chunks, stop_reason

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    async def text_stream(self):
        for c in self._chunks:
            yield c

    async def get_final_message(self):
        from types import SimpleNamespace

        usage = SimpleNamespace(input_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=1)
        return SimpleNamespace(stop_reason=self._stop, usage=usage)


class _FakeClient:
    def __init__(self, stop_reason="end_turn"):
        self.kwargs = None
        self.stop_reason = stop_reason
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.kwargs = kwargs
                return _FakeStream(["末日", "締めです [1]"], outer.stop_reason)

        class _Beta:
            messages = _Messages()

        self.beta = _Beta()


async def _collect(chat, index, messages):
    return [e async for e in chat.stream_answer(index, messages)]


def test_stream_answer_events_and_request_shape():
    import asyncio

    client = _FakeClient()
    chat = FaqChat(replace(load_settings(), claude_effort="low"), client=client)
    events = asyncio.run(_collect(chat, FaqIndex.from_pages(PAGES), [{"role": "user", "content": "締め日は？"}]))
    assert [e["type"] for e in events] == ["sources", "delta", "delta", "done"]
    assert client.kwargs["output_config"] == {"effort": "low"}
    assert client.kwargs["fallbacks"] == "default"
    assert "thinking" not in client.kwargs


def test_stream_answer_reports_refusal():
    import asyncio

    chat = FaqChat(load_settings(), client=_FakeClient(stop_reason="refusal"))
    events = asyncio.run(_collect(chat, FaqIndex.from_pages(PAGES), [{"role": "user", "content": "x"}]))
    assert events[-1]["type"] == "error"
