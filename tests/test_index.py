from app.index import FaqIndex, chunk_page, tokenize
from app.sharepoint import Page, canvas_to_text, html_to_text


def _page(text: str, pid: str = "p1") -> Page:
    return Page(id=pid, title="FAQ", url="https://example/p", last_modified="", text=text)


def test_tokenize_japanese_bigrams_and_ascii():
    assert tokenize("有給休暇 PC") == ["有給", "給休", "休暇", "pc"]
    # 全角英数は NFKC で半角化される
    assert tokenize("ＶＰＮ") == ["vpn"]


def test_chunk_page_splits_by_heading():
    chunks = chunk_page(_page("## 質問A\n回答A\n\n## 質問B\n回答B"))
    assert [(c.heading, c.text) for c in chunks] == [("質問A", "回答A"), ("質問B", "回答B")]


def test_chunk_page_splits_long_sections():
    body = "\n\n".join(["あ" * 500] * 5)
    chunks = chunk_page(_page(f"## 長い節\n{body}"), max_chars=1200)
    assert len(chunks) > 1
    assert all(len(c.text) <= 1200 for c in chunks)


def test_search_ranks_relevant_chunk_first():
    index = FaqIndex.from_pages(
        [
            _page("## 有給休暇の申請\n勤怠システムから申請します", "a"),
            _page("## 経費精算の締め日\n毎月末日締めです", "b"),
        ]
    )
    assert index.search("有給休暇を申請したい")[0].page_id == "a"
    assert index.search("経費の締め日は？")[0].page_id == "b"
    assert index.search("zzz") == []


def test_html_to_text_keeps_headings_and_lists():
    text = html_to_text("<h2>質問</h2><p>回答です</p><ul><li>手順1</li><li>手順2</li></ul>")
    assert "## 質問" in text
    assert "- 手順1" in text and "- 手順2" in text


def test_canvas_to_text_reads_text_and_standard_webparts():
    canvas = {
        "horizontalSections": [
            {"columns": [{"webparts": [
                {"@odata.type": "#microsoft.graph.textWebPart", "innerHtml": "<h3>Q1</h3><p>A1</p>"},
                {"@odata.type": "#microsoft.graph.standardWebPart",
                 "data": {"serverProcessedContent": {"searchablePlainTexts": [{"key": "t", "value": "補足情報"}]}}},
            ]}]}
        ]
    }
    text = canvas_to_text(canvas)
    assert "## Q1" in text and "A1" in text and "補足情報" in text
