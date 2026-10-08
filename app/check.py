"""SharePoint への接続設定を順番に確認する。

    python -m app.check

トークン取得 → 付与された権限 → サイト解決 → ページ一覧 → 本文抽出 の順に確かめ、
失敗した段階と考えられる原因を表示する。データは保存しない。
"""

from __future__ import annotations

import base64
import json
import sys

import requests

from .config import settings
from .sharepoint import GraphClient, fetch_page_text, list_pages, resolve_site

TOKEN_HINTS = {
    "AADSTS7000215": "クライアントシークレットが正しくありません。シークレットの「ID」ではなく「値」を設定しているか確認してください。",
    "AADSTS7000222": "クライアントシークレットの有効期限が切れています。新しいシークレットを作成してください。",
    "AADSTS700016": "クライアント ID のアプリがこのテナントに見つかりません。AZURE_CLIENT_ID と AZURE_TENANT_ID を確認してください。",
    "AADSTS90002": "テナントが見つかりません。AZURE_TENANT_ID を確認してください。",
    "AADSTS900023": "AZURE_TENANT_ID の形式が正しくありません。",
}
READ_ROLES = {"Sites.Read.All", "Sites.ReadWrite.All", "Sites.Selected", "Sites.FullControl.All"}


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def fail(msg: str, hint: str) -> None:
    print(f"  [NG] {msg}\n       → {hint}")
    sys.exit(1)


def _roles(token: str) -> list[str]:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload)).get("roles", [])


def _http_hint(e: requests.HTTPError, step: str, roles: list[str]) -> str:
    status = e.response.status_code if e.response is not None else None
    if status == 404:
        return "見つかりません。SHAREPOINT_SITE_URL がサイトのURL(例: https://contoso.sharepoint.com/sites/faq)になっているか確認してください。"
    if status in (401, 403):
        if roles == ["Sites.Selected"]:
            return (
                "Sites.Selected の場合、管理者が対象サイトにこのアプリの read 権限を付与する必要があります"
                "(README の「Entra ID でアプリを登録する」を参照)。"
            )
        return f"{step}の権限がありません。Sites.Read.All(アプリケーションの許可)と管理者の同意を確認してください。"
    return f"HTTP {status}: {e.response.text[:300] if e.response is not None else e}"


def main() -> None:
    print("SharePoint 接続チェック\n")

    print("1. 設定値")
    missing = [
        name
        for name, value in [
            ("AZURE_TENANT_ID", settings.tenant_id),
            ("AZURE_CLIENT_ID", settings.client_id),
            ("AZURE_CLIENT_SECRET", settings.client_secret),
            ("SHAREPOINT_SITE_URL", settings.site_url),
        ]
        if not value
    ]
    if missing:
        fail(f"未設定: {', '.join(missing)}", ".env に値を設定してください(.env.example を参照)。")
    ok(f"サイト: {settings.site_url}")

    print("2. アクセストークンの取得")
    try:
        client = GraphClient(settings.tenant_id, settings.client_id, settings.client_secret)
    except ValueError:
        fail(
            "テナントの認証情報を取得できません",
            "AZURE_TENANT_ID(ディレクトリ (テナント) ID)が正しいか、"
            "このサーバーから https://login.microsoftonline.com に接続できるか確認してください。",
        )
    try:
        token = client._token()
    except RuntimeError as e:
        hint = next((h for code, h in TOKEN_HINTS.items() if code in str(e)), "エラー内容を確認してください。")
        fail(str(e), hint)
    ok("取得できました")

    print("3. 付与されている権限")
    roles = _roles(token)
    if not READ_ROLES & set(roles):
        fail(
            f"サイトを読む権限がありません(現在: {', '.join(roles) or 'なし'})",
            "「API のアクセス許可」で Microsoft Graph の「アプリケーションの許可」に Sites.Read.All "
            "または Sites.Selected を追加し、「管理者の同意を与えます」を押してください。"
            "「委任されたアクセス許可」では動きません。",
        )
    ok(", ".join(roles))

    print("4. サイトの解決")
    try:
        site = resolve_site(client, settings.site_url)
    except requests.HTTPError as e:
        fail("サイトを取得できません", _http_hint(e, "サイト読み取り", roles))
    ok(f"{site.get('displayName')} ({site.get('webUrl')})")

    print("5. サイトページの一覧")
    try:
        pages = list_pages(client, site["id"])
    except requests.HTTPError as e:
        fail("ページ一覧を取得できません", _http_hint(e, "ページ読み取り", roles))
    if not pages:
        fail("ページが 0 件です", "このサイトの「サイトページ」ライブラリに FAQ ページがあるか確認してください。")
    ok(f"{len(pages)} ページ")
    for p in pages[:10]:
        print(f"       - {p.get('title') or p.get('name')}")
    if len(pages) > 10:
        print(f"       ...ほか {len(pages) - 10} ページ")

    print("6. 本文の抽出(先頭のページ)")
    first = pages[0]
    try:
        text = fetch_page_text(client, site["id"], first["id"])
    except requests.HTTPError as e:
        fail("本文を取得できません", _http_hint(e, "ページ読み取り", roles))
    if not text:
        print("  [注意] 本文が空でした。画像や埋め込みのみのページかもしれません。他のページは同期時に確認されます。")
    else:
        ok(f"「{first.get('title')}」 {len(text)} 文字")
        preview = text[:300].replace("\n", "\n       ")
        print(f"       {preview}{'…' if len(text) > 300 else ''}")

    print("\nすべて成功しました。次は `python -m app.sync` で FAQ を取り込んでください。")


if __name__ == "__main__":
    main()
