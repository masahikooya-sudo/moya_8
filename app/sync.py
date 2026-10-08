"""SharePoint からFAQページを同期して検索インデックスを作り直す。

    python -m app.sync            # SharePoint から同期
    python -m app.sync --sample   # sample/faq_pages.json で動作確認(Azure 設定不要)
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .config import Settings, settings
from .index import FaqIndex
from .sharepoint import GraphClient, Page, fetch_site_pages

log = logging.getLogger(__name__)
SAMPLE_FILE = Path(__file__).resolve().parent.parent / "sample" / "faq_pages.json"


def pages_path(cfg: Settings) -> Path:
    return cfg.data_dir / "pages.json"


def index_path(cfg: Settings) -> Path:
    return cfg.data_dir / "index.json"


def _load_cached_pages(cfg: Settings) -> dict[str, Page]:
    path = pages_path(cfg)
    if not path.exists():
        return {}
    return {d["id"]: Page(**d) for d in json.loads(path.read_text("utf-8"))}


def _save(cfg: Settings, pages: list[Page]) -> FaqIndex:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    path = pages_path(cfg)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([p.to_dict() for p in pages], ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)
    index = FaqIndex.from_pages(pages)
    index.save(index_path(cfg))
    log.info("同期完了: %d ページ / %d チャンク", len(pages), len(index.chunks))
    return index


def sync_from_sharepoint(cfg: Settings = settings) -> FaqIndex:
    if not cfg.sharepoint_configured:
        raise RuntimeError("SharePoint の接続設定(AZURE_* / SHAREPOINT_SITE_URL)がありません")
    client = GraphClient(cfg.tenant_id, cfg.client_id, cfg.client_secret)
    pages = fetch_site_pages(client, cfg.site_url, _load_cached_pages(cfg))
    return _save(cfg, [p for p in pages if p.text])


def sync_from_sample(cfg: Settings = settings) -> FaqIndex:
    pages = [Page(**d) for d in json.loads(SAMPLE_FILE.read_text("utf-8"))]
    return _save(cfg, pages)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", action="store_true", help="サンプルデータでインデックスを作る")
    args = parser.parse_args()
    if args.sample:
        sync_from_sample()
    else:
        sync_from_sharepoint()


if __name__ == "__main__":
    main()
