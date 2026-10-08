# 社内FAQチャット

SharePoint の FAQ マニュアルサイト(サイトページ)の内容をもとに、社員からの問い合わせに Claude が回答するチャット形式の Web アプリです。

- 回答は FAQ に書かれている内容だけを根拠にし、文末に出典番号 `[1]` を付けます。回答の下に参照した FAQ ページへのリンクが表示されます。
- FAQ に答えがない場合は、推測で答えずに問い合わせ窓口(`FALLBACK_CONTACT`)を案内します。
- 回答はストリーミングで少しずつ表示されます。会話の流れを踏まえた追加の質問(「それはパート社員も対象？」など)にも対応します。

## 仕組み

```
SharePoint サイトページ ──(Microsoft Graph / 定期同期)──▶ data/pages.json ─▶ 検索インデックス (data/index.json)
                                                                              │
社員のブラウザ ──質問──▶ FastAPI ──関連FAQ + 質問──▶ Claude API ──回答(ストリーミング)──▶ ブラウザ
```

| ファイル | 役割 |
|---|---|
| `app/sharepoint.py` | Graph API でサイトページを取得し、本文テキスト(見出し・箇条書き付き)を抽出 |
| `app/index.py` | ページを見出し単位のチャンクに分割し、BM25(日本語は文字 bi-gram)で検索 |
| `app/sync.py` | 同期処理。更新日時が変わったページだけ再取得する |
| `app/chat.py` | プロンプトを組み立て、Claude で回答を生成 |
| `app/main.py` | Web サーバー(`/api/chat` と画面)。起動中は定期的に自動同期 |
| `static/` | チャット画面 |

**FAQ の渡し方は分量で自動的に切り替わります。**

- FAQ 全体が `FULL_CONTEXT_MAX_CHARS`(既定 6 万文字)以下 → FAQ 全文をプロンプトに入れます。検索漏れがなく最も正確です。プロンプトキャッシュが効くため、2 回目以降の入力コストは約 1/10 になります。
- それより多い場合 → 質問に関連するチャンクを上位 `SEARCH_TOP_K` 件検索して渡します。

## セットアップ

### 1. Entra ID(Azure AD)でアプリを登録する

1. [Entra 管理センター](https://entra.microsoft.com/) →「アプリの登録」→「新規登録」で登録します(リダイレクト URI は不要)。
2. 「証明書とシークレット」でクライアントシークレットを作成し、値を控えます。
3. 「API のアクセス許可」→ Microsoft Graph →「アプリケーションの許可」から、次のどちらかを追加します。追加後、管理者の同意を付与してください。
   - **`Sites.Selected`(推奨)**: FAQ サイトだけを読めるようにします。追加の手順として、管理者が対象サイトに `read` 権限を付与する必要があります(Graph の `POST /sites/{site-id}/permissions`、または PnP PowerShell の `Grant-PnPAzureADAppSitePermission -Permissions Read`)。
   - `Sites.Read.All`: テナント内のすべてのサイトを読めます。設定は簡単ですが、権限は広くなります。
4. 「概要」に表示されるテナント ID とクライアント ID を控えます。

### 2. Claude API キーを用意する

[Claude Console](https://platform.claude.com/) で API キーを発行します。

### 3. インストールと設定

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # .env を編集して各値を設定
```

### 4. 同期と起動

```bash
python -m app.sync                 # SharePoint から FAQ を取り込む(初回)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

ブラウザで http://localhost:8000 を開きます。起動中は `SYNC_INTERVAL_MINUTES` ごとに自動で再同期します。`/api/status` では取り込んだページ数と、現在の動作モード(全文 / 検索)を確認できます。

Azure の設定をする前に動作を試したい場合は、サンプルの FAQ を使えます(Claude API キーは必要です)。

```bash
python -m app.sync --sample
```

## 主な設定(.env)

| 変数 | 既定値 | 説明 |
|---|---|---|
| `CLAUDE_MODEL` | `claude-opus-5-5` | 使用するモデル |
| `CLAUDE_EFFORT` | `low` | 推論の深さ(`low` / `medium` / `high`)。複雑な規程の解釈が多い場合は `medium` にします |
| `CLAUDE_FALLBACKS` | `true` | 安全分類器が回答を辞退した場合に、サーバー側で別モデルに切り替えて回答を続けます |
| `SHAREPOINT_SITE_URL` | | 例: `https://contoso.sharepoint.com/sites/faq` |
| `SYNC_INTERVAL_MINUTES` | `60` | 自動同期の間隔(分)。`0` にすると無効 |
| `FULL_CONTEXT_MAX_CHARS` | `60000` | FAQ 全文をプロンプトに入れる上限の文字数 |
| `SEARCH_TOP_K` | `8` | 検索モードで参照するチャンク数 |
| `COMPANY_NAME` / `FALLBACK_CONTACT` | | 回答の文面で使う会社名と問い合わせ先 |

## 本番運用に向けて

- **社員認証**: このアプリ自体はログイン機能を持ちません。Azure App Service に配置して「認証(Easy Auth)」で Entra ID サインインを必須にするか、社内ネットワーク内のリバースプロキシで保護してください。
- **データの扱い**: FAQ 本文と質問は、回答を生成するために Claude API に送信されます。外部 API の利用について社内規程を確認してください。
- **FAQ の書き方**: 「## 質問文」の見出しの下に回答を書くと、見出し単位で検索されるため精度が上がります。折りたたみ式セクションなどの標準 Web パーツも、検索用テキストを取り込みます。
- **ログ**: 各回答のトークン使用量(キャッシュヒット数を含む)がサーバーログに出力されます。

## テスト

```bash
pip install -r requirements-dev.txt
pytest
```
