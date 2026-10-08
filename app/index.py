"""FAQ ページをチャンクに分割し、BM25 で検索する。

日本語は形態素解析器なしでも精度が出やすい「文字 bi-gram」でトークン化する。
外部の検索サービスや埋め込みモデルが不要なので、そのまま社内サーバーで動く。
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .sharepoint import Page

CHUNK_MAX_CHARS = 1200
_CJK = r"぀-ヿ㐀-䶿一-鿿豈-﫿々ー"
_TOKEN_RE = re.compile(rf"[{_CJK}]+|[a-z0-9]+")


@dataclass
class Chunk:
    page_id: str
    title: str
    url: str
    heading: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    for run in _TOKEN_RE.findall(text):
        if run[0].isascii():
            tokens.append(run)
        elif len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def chunk_page(page: Page, max_chars: int = CHUNK_MAX_CHARS) -> list[Chunk]:
    """見出し(## )単位で区切り、長すぎる節は段落単位でさらに分割する。"""
    sections: list[tuple[str, str]] = []
    heading, buf = "", []
    for line in page.text.splitlines():
        if line.startswith("## "):
            if "".join(buf).strip():
                sections.append((heading, "\n".join(buf).strip()))
            heading, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        sections.append((heading, "\n".join(buf).strip()))

    chunks: list[Chunk] = []
    for heading, body in sections:
        piece = ""
        for para in body.split("\n\n"):
            if piece and len(piece) + len(para) > max_chars:
                chunks.append(Chunk(page.id, page.title, page.url, heading, piece.strip()))
                piece = ""
            piece += para + "\n\n"
        if piece.strip():
            chunks.append(Chunk(page.id, page.title, page.url, heading, piece.strip()))
    return chunks


class FaqIndex:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self._tfs = [Counter(tokenize(f"{c.title}\n{c.heading}\n{c.text}")) for c in chunks]
        self._lens = [sum(tf.values()) for tf in self._tfs]
        self._avg = (sum(self._lens) / len(self._lens)) if self._lens else 0.0
        df: Counter = Counter()
        for tf in self._tfs:
            df.update(tf.keys())
        n = len(chunks)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    @property
    def total_chars(self) -> int:
        return sum(len(c.text) for c in self.chunks)

    def search(self, query: str, top_k: int = 8) -> list[Chunk]:
        q = set(tokenize(query))
        if not q or not self.chunks:
            return []
        scored = []
        for i, tf in enumerate(self._tfs):
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * self._lens[i] / (self._avg or 1))
            for t in q:
                f = tf.get(t)
                if f:
                    score += self._idf[t] * f * (self.k1 + 1) / (f + norm)
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        return [self.chunks[i] for _, i in scored[:top_k]]

    # ---- 永続化 ----
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps([c.to_dict() for c in self.chunks], ensure_ascii=False), "utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "FaqIndex":
        if not path.exists():
            return cls([])
        return cls([Chunk(**d) for d in json.loads(path.read_text("utf-8"))])

    @classmethod
    def from_pages(cls, pages: list[Page]) -> "FaqIndex":
        return cls([c for p in pages for c in chunk_page(p)])
