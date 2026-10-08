"""FAQ を根拠に Claude で回答を生成する(ストリーミング)。"""

from __future__ import annotations

import html
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import anthropic

from .config import Settings
from .index import Chunk, FaqIndex

log = logging.getLogger(__name__)

SYSTEM_TEMPLATE = """\
あなたは{company}の社内FAQアシスタントです。社員からの問い合わせに、SharePoint の FAQ マニュアルサイトの内容だけを根拠に日本語で回答します。

# 回答ルール
- 根拠は <faq> 内の文書のみです。文書に書かれていないことを推測や一般論で補わないでください。
- 根拠にした文書の番号を、該当する文の末尾に [1] のように付けてください。複数なら [1][3] のように並べます。
- FAQ に答えが見つからない、または情報が足りない場合は、その旨を正直に伝え、「{contact}」へ問い合わせるよう案内してください。関連しそうな FAQ があれば紹介して構いません。
- 質問があいまいで回答が分かれる場合(雇用形態・拠点などで異なる場合)は、それぞれのケースを示すか、確認の質問をしてください。
- 手順は番号付きリスト、条件や対象は箇条書きにするなど、読みやすく簡潔にまとめてください。前置きは不要です。すぐに回答を書き始めてください。
- 申請期限・金額・連絡先などの数値や固有名詞は文書の記載どおりに書いてください。
- 業務と無関係な依頼や、FAQ の範囲外の相談には応じず、その旨を丁寧に伝えてください。"""


@dataclass
class Source:
    number: int
    title: str
    url: str

    def to_dict(self) -> dict:
        return {"number": self.number, "title": self.title, "url": self.url}


def _format_docs(chunks: list[Chunk]) -> tuple[str, list[Source]]:
    """チャンクをページ単位にまとめて番号を振る。"""
    by_page: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_page.setdefault(c.page_id, []).append(c)
    blocks, sources = [], []
    for n, page_chunks in enumerate(by_page.values(), start=1):
        first = page_chunks[0]
        body = "\n\n".join(
            (f"## {c.heading}\n{c.text}" if c.heading else c.text) for c in page_chunks
        )
        blocks.append(f'<document number="{n}" title="{html.escape(first.title)}">\n{body}\n</document>')
        sources.append(Source(n, first.title, first.url))
    return "<faq>\n" + "\n".join(blocks) + "\n</faq>", sources


def _search_query(messages: list[dict]) -> str:
    # 「それは派遣社員も対象？」のような追い質問に備え、直近2つのユーザー発話で検索する
    user_turns = [m["content"] for m in messages if m["role"] == "user"]
    return "\n".join(user_turns[-2:])


class FaqChat:
    def __init__(self, cfg: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.cfg = cfg
        self.client = client or anthropic.AsyncAnthropic()
        self.system_prompt = SYSTEM_TEMPLATE.format(company=cfg.company_name, contact=cfg.fallback_contact)

    def build_request(self, index: FaqIndex, messages: list[dict]) -> tuple[list[dict], list[dict], list[Source]]:
        """(system ブロック, messages, 出典リスト) を返す。"""
        system: list[dict] = [{"type": "text", "text": self.system_prompt}]
        full_context = 0 < index.total_chars <= self.cfg.full_context_max_chars

        if full_context:
            # FAQ 全体をシステムプロンプトに入れてキャッシュする。検索漏れが起きない。
            docs, sources = _format_docs(index.chunks)
            system.append({"type": "text", "text": docs, "cache_control": {"type": "ephemeral"}})
            return system, messages, sources

        hits = index.search(_search_query(messages), self.cfg.search_top_k)
        docs, sources = _format_docs(hits) if hits else ("<faq>\n(該当する文書は見つかりませんでした)\n</faq>", [])
        # 検索結果は最新のユーザー発話にだけ添える。過去ターンの内容は変えないのでキャッシュが効く。
        *history, last = messages
        latest = {"role": "user", "content": f"{docs}\n\n<question>\n{last['content']}\n</question>"}
        system[-1]["cache_control"] = {"type": "ephemeral"}
        return system, [*history, latest], sources

    async def stream_answer(self, index: FaqIndex, messages: list[dict]) -> AsyncIterator[dict]:
        """イベント dict を順に返す: sources → delta... → done / error"""
        system, api_messages, sources = self.build_request(index, messages)
        yield {"type": "sources", "sources": [s.to_dict() for s in sources]}

        extra: dict = {}
        if self.cfg.claude_fallbacks:
            # 安全分類器による辞退時にサーバー側で別モデルに切り替えて回答を続ける
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            async with self.client.beta.messages.stream(
                model=self.cfg.claude_model,
                max_tokens=16000,
                system=system,
                messages=api_messages,
                output_config={"effort": self.cfg.claude_effort},
                **extra,
            ) as stream:
                async for text in stream.text_stream:
                    yield {"type": "delta", "text": text}
                final = await stream.get_final_message()
        except anthropic.RateLimitError:
            yield {"type": "error", "message": "現在混み合っています。しばらくしてから再度お試しください。"}
            return
        except anthropic.APIStatusError as e:
            log.exception("Claude API エラー (status=%s)", e.status_code)
            yield {"type": "error", "message": "回答の生成中にエラーが発生しました。"}
            return
        except anthropic.APIConnectionError:
            log.exception("Claude API 接続エラー")
            yield {"type": "error", "message": "AIサービスに接続できませんでした。"}
            return

        if final.stop_reason == "refusal":
            yield {"type": "error", "message": f"この質問にはお答えできません。{self.cfg.fallback_contact}へお問い合わせください。"}
            return
        if final.stop_reason == "max_tokens":
            yield {"type": "delta", "text": "\n\n(回答が長いため途中で終了しました)"}
        log.info(
            "usage input=%s cache_read=%s cache_write=%s output=%s",
            final.usage.input_tokens,
            final.usage.cache_read_input_tokens,
            final.usage.cache_creation_input_tokens,
            final.usage.output_tokens,
        )
        yield {"type": "done"}
