"""Microsoft Graph 経由で SharePoint のサイトページを取得する。

アプリ専用認証(クライアント資格情報フロー)を使う。Entra ID のアプリ登録に
Microsoft Graph のアプリケーション権限 `Sites.Read.All`(または `Sites.Selected`
+ 対象サイトへの read 付与)と管理者の同意が必要。
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlparse

import msal
import requests
from bs4 import BeautifulSoup

GRAPH = "https://graph.microsoft.com/v1.0"
log = logging.getLogger(__name__)


@dataclass
class Page:
    id: str
    title: str
    url: str
    last_modified: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


class GraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self._app = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        self._session = requests.Session()

    def _token(self) -> str:
        # MSAL がトークンをキャッシュするので毎回呼んでよい
        result = self._app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise RuntimeError(f"Graph のトークン取得に失敗: {result.get('error_description', result)}")
        return result["access_token"]

    def get(self, url: str, params: dict | None = None) -> dict:
        if not url.startswith("http"):
            url = GRAPH + url
        for attempt in range(5):
            resp = self._session.get(
                url, params=params, headers={"Authorization": f"Bearer {self._token()}"}, timeout=60
            )
            if resp.status_code in (429, 503, 504):
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                log.warning("Graph がスロットリング中 (%s)。%s 秒待機します", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    def get_all(self, url: str, params: dict | None = None) -> list[dict]:
        items: list[dict] = []
        data = self.get(url, params)
        items.extend(data.get("value", []))
        while next_link := data.get("@odata.nextLink"):
            data = self.get(next_link)
            items.extend(data.get("value", []))
        return items


def resolve_site(client: GraphClient, site_url: str) -> dict:
    """https://contoso.sharepoint.com/sites/faq -> Graph の site リソース"""
    parsed = urlparse(site_url)
    path = parsed.path.rstrip("/")
    ref = f"/sites/{parsed.hostname}:{path}" if path else f"/sites/{parsed.hostname}"
    return client.get(ref)


def list_pages(client: GraphClient, site_id: str) -> list[dict]:
    return client.get_all(
        f"/sites/{site_id}/pages/microsoft.graph.sitePage",
        params={"$select": "id,name,title,webUrl,lastModifiedDateTime,description"},
    )


def fetch_page_text(client: GraphClient, site_id: str, page_id: str) -> str:
    page = client.get(
        f"/sites/{site_id}/pages/{page_id}/microsoft.graph.sitePage",
        params={"$expand": "canvasLayout"},
    )
    return canvas_to_text(page.get("canvasLayout") or {}, page.get("description") or "")


def _iter_webparts(canvas: dict):
    for section in canvas.get("horizontalSections") or []:
        for column in section.get("columns") or []:
            yield from column.get("webparts") or []
    vertical = canvas.get("verticalSection") or {}
    yield from vertical.get("webparts") or []


def canvas_to_text(canvas: dict, description: str = "") -> str:
    """ページのキャンバスからテキストを抽出する。見出しは '## ' 付きの行にする。"""
    parts: list[str] = []
    for wp in _iter_webparts(canvas):
        odata_type = wp.get("@odata.type", "")
        if odata_type.endswith("textWebPart"):
            text = html_to_text(wp.get("innerHtml") or "")
            if text:
                parts.append(text)
        else:
            # 標準 Web パーツ(折りたたみ式セクション、ヒーロー等)の検索用テキスト
            data = wp.get("data") or {}
            title = data.get("title")
            spc = data.get("serverProcessedContent") or {}
            texts = [t.get("value", "") for t in spc.get("searchablePlainTexts") or []]
            html_texts = [html_to_text(t.get("value", "")) for t in spc.get("htmlStrings") or []]
            body = "\n".join(t for t in [title, *texts, *html_texts] if t and t.strip())
            if body:
                parts.append(body)
    if not parts and description:
        parts.append(description)
    return "\n\n".join(parts).strip()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        tag.insert_before("\n## ")
        tag.insert_after("\n")
    for tag in soup.find_all("li"):
        tag.insert_before("\n- ")
    for tag in soup.find_all(["p", "div", "tr"]):
        tag.insert_after("\n")
    for tag in soup.find_all("br"):
        tag.replace_with("\n")
    for tag in soup.find_all("a"):
        href = tag.get("href")
        if href and href.startswith("http") and tag.get_text(strip=True):
            tag.insert_after(f" ({href})")
    lines = [line.strip() for line in soup.get_text().splitlines()]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def fetch_site_pages(client: GraphClient, site_url: str, cached: dict[str, Page]) -> list[Page]:
    """サイトの全ページを取得する。更新日時が変わっていないページはキャッシュを再利用する。"""
    site = resolve_site(client, site_url)
    site_web_url = site.get("webUrl", site_url).rstrip("/") + "/"
    pages: list[Page] = []
    for meta in list_pages(client, site["id"]):
        page_id = meta["id"]
        modified = meta.get("lastModifiedDateTime", "")
        hit = cached.get(page_id)
        if hit and hit.last_modified == modified:
            pages.append(hit)
            continue
        log.info("取得: %s", meta.get("title") or meta.get("name"))
        text = fetch_page_text(client, site["id"], page_id)
        url = meta.get("webUrl") or ""
        if not url.startswith("http"):
            url = urljoin(site_web_url, url)
        pages.append(
            Page(
                id=page_id,
                title=meta.get("title") or meta.get("name") or "(無題)",
                url=url,
                last_modified=modified,
                text=text,
            )
        )
    return pages
