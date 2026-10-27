# fm-serve

Apple Foundation Models CLI (`fm serve`) を **常駐運用** するための薄いラッパです。

| 項目 | 内容 |
| :--- | :--- |
| 本体 | `/usr/bin/fm serve` (On-device Apple Foundation Model `system`) |
| 公開 API | OpenAI 互換 Chat Completions (`:1976`) |
| 常駐 | macOS **LaunchAgent** (`KeepAlive` + `RunAtLoad`) |
| 接続範囲 | localhost / Tailscale IPv4 のみ / 全インターフェースから選択 |
| スリープ抑制 | 既定は API アクセス時に 1 時間延長。`--no-keepalive` で無効化可能 |

> Foundation Models は **ログイン中のユーザセッション** が必要なため、
> LaunchDaemon (`/Library/LaunchDaemons`) ではなく LaunchAgent
> (`~/Library/LaunchAgents`) を使います。launchd の `KeepAlive` でプロセス監視・再起動します。

## セットアップ

```bash
cd ~/.local/share/fm-serve  # リポジトリの配置先に合わせて変更
mise install
mise exec -- uv sync
./run.sh --check
```

事前に `fm available` で System model が使えることを確認してください。

### 常時起動サーバーとして使う

Tailscale 接続済みの Mac で、外部アクセスを Tailscale に限定し、
PCC を有効にして常駐させる場合:

```bash
tailscale ip -4  # Tailscale CLI が利用でき、IPv4 が取得できることを確認
./service.sh install --tailscale --terminal --no-keepalive
./service.sh status
```

`install` は既存サービスを再起動して設定を保存します。以後はログイン時に自動起動し、
`./service.sh restart` でもバインド先・Terminal 起動・スリープ抑制の設定を継承します。
`--no-keepalive` 使用時は、Mac 自体のスリープ設定を OS 側で管理してください。

## 日常運用

```bash
./service.sh start              # バックグラウンド起動 (未登録時は localhost:1976)
./service.sh restart            # 保存した設定で再起動
./service.sh status
./service.sh log -f
./service.sh stop

# ログイン時から常駐 (推奨)
./service.sh install            # LaunchAgent 登録 + 起動
./service.sh uninstall          # 自動起動解除

# 外部からのアクセスを Tailscale に限定
./service.sh install --tailscale
./service.sh restart             # バインド先・Terminal・keepalive 設定を継承

# LAN にも公開する場合 (認証なし)
./service.sh start --public
./service.sh install --public
```

### 公開範囲を選ぶ

| オプション | 待ち受け先 | 用途 |
| :--- | :--- | :--- |
| `--localhost` | `127.0.0.1` | 同じ Mac からのみ接続 |
| `--tailscale` | `tailscale ip -4` で取得した IPv4 | 外部アクセスを Tailscale に限定 |
| `--public` | `0.0.0.0` | LAN を含む全 IPv4 インターフェースに公開 |

いずれも `start` / `restart` / `install` で指定できます。同時指定はできません。
バインド先の優先順位は、上記オプション → 明示した `FM_SERVE_HOST` →
登録済み LaunchAgent の設定 → `127.0.0.1` です。

`--tailscale` は `tailscale ip -4` で取得した IPv4 のみにバインドします。
LAN の IP や `127.0.0.1` では待ち受けません。同じ Mac からも表示された
Tailscale IP の URL を使用してください。`status` と起動待機もこの IP に接続します。
Tailscale CLI がない場合や IP を取得できない場合は、既存サービスを停止せず中止します。
取得した IP は LaunchAgent に保存されます。Tailscale IP が変わった場合は
`./service.sh install --tailscale` で更新してください。
`--public` との併用はできません。ローカル限定に戻すには `install --localhost`。

接続元も Tailscale に接続し、サーバーの `status` に表示された URL を指定します。
API 独自の認証はありません。接続を許可する端末・ユーザーは Tailscale 側のアクセス制御で管理してください。

サーバー自身で接続確認する例:

```bash
export FM_SERVE_URL="http://$(tailscale ip -4):1976"
curl --noproxy '*' -fsS "$FM_SERVE_URL/health" | python3 -m json.tool
curl --noproxy '*' -fsS "$FM_SERVE_URL/v1/models" | python3 -m json.tool
```

別の端末では `FM_SERVE_URL` にサーバーの Tailscale IP を設定します。
`$(tailscale ip -4)` は、そのコマンドを実行した端末自身の IP を返します。

### PCC を使う (macOS 27.2 で検証)

```bash
./service.sh install --terminal    # ログイン時に Terminal.app 経由で自動起動
./service.sh restart               # Terminal 起動の設定を継承
./service.sh stop                  # プロキシ・fm・専用ウィンドウを終了
```

LaunchAgent がプロキシを起動し、AppleScript で Terminal.app に `fm serve` の
直接起動を依頼します。通常の Python 子プロセス起動では、Terminal.app から
`./run.sh` を実行しても PCC は利用できません。`--terminal` は手動の `start`
や `./run.sh` でも指定できます。通常起動に戻す場合は `install --no-terminal`。

Terminal.app の専用ウィンドウが常駐中は開きます。最小化しても PCC の生成が
成功することを macOS 27.2 で確認済みです。fm の出力はそのウィンドウ、
プロキシの出力はサービスログに表示されます。Terminal.app を終了したり fm を
停止した場合、プロキシも終了し、LaunchAgent の KeepAlive で再起動します。
ログイン後の GUI セッションが必要です。初回に Terminal.app の制御を求める
macOS のダイアログが表示された場合は許可してください。

`GET /v1/models` は利用不可の `pcc` も列挙します。利用可否は `/health` の
`upstream.models` にある `pcc.available` で確認します。

```bash
curl --noproxy '*' -fsS "${FM_SERVE_URL:-http://127.0.0.1:1976}/health" | python3 -m json.tool
curl --noproxy '*' -fsS "${FM_SERVE_URL:-http://127.0.0.1:1976}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"pcc","messages":[{"role":"user","content":"Hello"}]}'
```

### スリープ抑制 (KeepAlive)

プロキシが API リクエスト (`/v1/models`, `/v1/chat/completions` 等) を受けるたびに、
macOS の **システムスリープ抑制** (`PreventUserIdleSystemSleep`) を **1 時間延長**します。
画面オフは許容。最終アクセスから 1 時間アクセスがなければ自動解除。`stop` 時も解除されます。

`GET /health` には `keepalive` フィールドが含まれます。

常時起動サーバーなど、スリープを OS 側で管理する場合は抑制を無効化できます。

```bash
./service.sh install --no-keepalive  # 常駐設定に保存して起動 (既存サービスは再起動)
./service.sh restart                # 無効化設定を継承
./service.sh start --no-keepalive    # 手動起動時のみ無効化
./service.sh restart --keepalive     # スリープ抑制を再び有効化
```

launchd の `KeepAlive` (プロセス監視・自動再起動) は引き続き有効です。
`./run.sh --no-keepalive` でもスリープ抑制を無効化できます。
`/health` の `keepalive.enabled` と `keepalive.active` がともに `false` なら無効化済みです。

## API

未設定時の URL: `http://127.0.0.1:1976`。Tailscale 限定時は
`http://<サーバーの Tailscale IPv4>:1976` を使用します。
OpenAI 互換クライアントの Base URL は、この URL に `/v1` を付けた値です。

| エンドポイント | 内容 |
| :--- | :--- |
| `GET /health` | 稼働状態・ upstream (`fm`)・keepalive |
| `GET /v1/models` | モデル一覧 (`system`, `pcc`。利用可否は `/health` で確認) |
| `POST /v1/chat/completions` | チャット (stream 対応) |

例:

```bash
curl --noproxy '*' -fsS "${FM_SERVE_URL:-http://127.0.0.1:1976}/v1/models" | python3 -m json.tool
curl --noproxy '*' -fsS "${FM_SERVE_URL:-http://127.0.0.1:1976}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"system","messages":[{"role":"user","content":"Hello"}]}'
```

## 構成

```
fm-serve (公開 :1976)  ──proxy + keepalive──►  fm serve (localhost エフェメラルポート)
         ▲
   LaunchAgent (login / KeepAlive)
```

`fm` 本体には手を入れず、前面の FastAPI プロキシでスリープ抑制とヘルス集約だけを行います。

## 環境変数

| 変数 | 既定 | 意味 |
| :--- | :--- | :--- |
| `FM_SERVE_HOST` | `127.0.0.1` | 公開バインド |
| `FM_SERVE_PORT` | `1976` | 公開ポート |
| `FM_SERVE_KEEPALIVE_TIMEOUT` | `3600` | アイドル秒 |
| `FM_BIN` | `/usr/bin/fm` | fm バイナリ |
| `FM_SERVE_TERMINAL` | 登録済み設定、なければ `0` | `1` で Terminal.app 経由の起動 |
| `FM_SERVE_LOAD_TIMEOUT` | `45` | `service.sh` の起動待機秒数 |
| `FM_SERVE_LOGS_DIR` | `~/Library/Logs/fm-serve` | `service.sh` のログ出力先 |

## 状態確認・トラブルシュート

```bash
./service.sh status
./service.sh log -f
lsof -nP -iTCP:1976 -sTCP:LISTEN  # 実際の待ち受け先を確認
```

Tailscale 限定時は `100.x.x.x:1976` のような Tailscale IPv4 が表示されます。
`*:1976` なら全インターフェースで待ち受けています。
ログは既定で `~/Library/Logs/fm-serve/service.log` に保存され、
リポジトリの `logs/service.log` からも参照できます。

| 状況 | 確認・対処 |
| :--- | :--- |
| `--tailscale` で IP を取得できない | `tailscale` が PATH にあるか、Tailscale に接続済みか確認 |
| Tailscale IP が変わった | `./service.sh install --tailscale` で保存済み IP を更新 |
| localhost から接続できない | Tailscale 限定時はサーバー自身も Tailscale IP を使用 |
| 別の端末から接続できない | 接続元の Tailscale 接続・アクセス制御と、サーバーの `status` を確認 |
| PCC が利用不可 | `install --terminal` と `/health` の `upstream.models` を確認 |
| keepalive 無効化を確認したい | `/health` の `keepalive.enabled` と `keepalive.active` がともに `false` か確認 |

## 参考

常駐・keepalive の設計は `~/Documents/Sources/m4max-llm` と
`~/Documents/Sources/m2air-llm` (LaunchAgent) を踏襲しています。
