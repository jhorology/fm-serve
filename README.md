# fm-serve

Apple Foundation Models CLI (`fm serve`) を **常駐運用** するための薄いラッパです。

| 項目 | 内容 |
| :--- | :--- |
| 本体 | `/usr/bin/fm serve` (On-device Apple Foundation Model `system`) |
| 公開 API | OpenAI 互換 Chat Completions (`:1976`) |
| 常駐 | macOS **LaunchAgent** (`KeepAlive` + `RunAtLoad`) |
| スリープ抑制 | API アクセス時に IOKit Power Assertion を 1 時間延長 (m4max-llm と同じ方式) |

> Foundation Models は **ログイン中のユーザセッション** が必要なため、
> LaunchDaemon (`/Library/LaunchDaemons`) ではなく LaunchAgent
> (`~/Library/LaunchAgents`) を使います。launchd の `KeepAlive` でプロセス監視・再起動します。

## セットアップ

```bash
cd ~/Documents/Sources/fm-serve
mise install
mise exec -- uv sync
./run.sh --check
```

事前に `fm available` で System model が使えることを確認してください。

## 日常運用

```bash
./service.sh start              # バックグラウンド起動 (:1976)
./service.sh status
./service.sh log -f
./service.sh stop

# ログイン時から常駐 (推奨)
./service.sh install            # LaunchAgent 登録 + 起動
./service.sh uninstall          # 自動起動解除

# LAN / Tailscale から使う場合 (認証なし)
./service.sh start --public
./service.sh install --public
```

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
curl -s http://127.0.0.1:1976/health | python3 -m json.tool
curl -s http://127.0.0.1:1976/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"pcc","messages":[{"role":"user","content":"Hello"}]}'
```

### スリープ抑制 (KeepAlive)

プロキシが API リクエスト (`/v1/models`, `/v1/chat/completions` 等) を受けるたびに、
macOS の **システムスリープ抑制** (`PreventUserIdleSystemSleep`) を **1 時間延長**します。
画面オフは許容。最終アクセスから 1 時間アクセスがなければ自動解除。`stop` 時も解除されます。

`GET /health` には `keepalive` フィールドが含まれます。

## API

既定 URL: `http://127.0.0.1:1976`

| エンドポイント | 内容 |
| :--- | :--- |
| `GET /health` | 稼働状態・ upstream (`fm`)・keepalive |
| `GET /v1/models` | モデル一覧 (`system`) |
| `POST /v1/chat/completions` | チャット (stream 対応) |

例:

```bash
curl -s http://127.0.0.1:1976/v1/models | jq
curl -s http://127.0.0.1:1976/v1/chat/completions \
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

## 参考

常駐・keepalive の設計は `~/Documents/Sources/m4max-llm` と
`~/Documents/Sources/m2air-llm` (LaunchAgent) を踏襲しています。
