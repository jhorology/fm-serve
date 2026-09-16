#!/usr/bin/env bash
# ==============================================================================
# service.sh - fm-serve lifecycle + LaunchAgent (login always-on)
# ==============================================================================
# Apple Foundation Models (`fm serve`) を常駐運用する。
# 公開ポートの前に keepalive 付きプロキシを置き、API アクセス中は
# システムスリープを抑制する (画面オフは許容)。
#
# 注: Foundation Models はログイン中のユーザセッションが必要なため、
# LaunchDaemon (/Library/LaunchDaemons) ではなく LaunchAgent を使う。
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOGS_DIR="${FM_SERVE_LOGS_DIR:-$HOME/Library/Logs/fm-serve}"
LOG_FILE="$LOGS_DIR/service.log"
LOCAL_LOGS_DIR="$SCRIPT_DIR/logs"
PID_FILE="$SCRIPT_DIR/.service.pid"
HOST_FILE="$SCRIPT_DIR/.service.host"
DEFAULT_PORT="${FM_SERVE_PORT:-1976}"
BIND_HOST="${FM_SERVE_HOST:-127.0.0.1}"
PLIST_LABEL="com.user.fm-serve"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
LAUNCHER_BIN="$HOME/.local/bin/fm-serve"
LOAD_TIMEOUT="${FM_SERVE_LOAD_TIMEOUT:-45}"

mkdir -p "$LOGS_DIR" "$LOCAL_LOGS_DIR"
# ローカルの logs/service.log からも参照できるようにシンボリックリンクを作成
if [[ ! -e "$LOCAL_LOGS_DIR/service.log" ]]; then
  ln -sf "$LOG_FILE" "$LOCAL_LOGS_DIR/service.log" 2>/dev/null || true
fi

is_public() { [[ "$1" == "0.0.0.0" || "$1" == "::" ]]; }

reachable_addrs() {
  local iface ip
  for iface in $(ifconfig -l); do
    [[ "$iface" == en* ]] || continue
    ip=$(ipconfig getifaddr "$iface" 2>/dev/null || true)
    [[ -n "$ip" ]] && echo "LAN($iface) $ip"
  done
  if command -v tailscale >/dev/null 2>&1; then
    ip=$(tailscale ip -4 2>/dev/null | head -n 1 || true)
    [[ -n "$ip" ]] && echo "Tailscale $ip"
  fi
  return 0
}

rotate_log_if_needed() {
  mkdir -p "$LOGS_DIR"
  if [[ -f "$LOG_FILE" ]]; then
    local size
    size=$(stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)
    if ((size > 10 * 1024 * 1024)); then
      mv "$LOG_FILE" "${LOG_FILE}.1"
    fi
  fi
}

get_server_pid() {
  local pid port_pid
  pid=$(cat "$PID_FILE" 2>/dev/null || true)
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "$pid"
    return 0
  fi
  port_pid=$(lsof -ti tcp:"$DEFAULT_PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)
  if [[ -n "$port_pid" ]]; then
    echo "$port_pid" >"$PID_FILE"
    echo "$port_pid"
    return 0
  fi
  return 1
}

wait_for_health() {
  local timeout="${1:-$LOAD_TIMEOUT}" start
  start=$(date +%s)
  echo -n ">> 起動待機"
  until curl -sf "http://127.0.0.1:${DEFAULT_PORT}/health" >/dev/null 2>&1; do
    if (($(date +%s) - start >= timeout)); then
      echo " [タイムアウト]  tail -n 40 $LOG_FILE"
      return 1
    fi
    echo -n "."
    sleep 1
  done
  echo " [完了]"
}

launchagent_domain() {
  echo "gui/$(id -u)"
}

launchagent_loaded() {
  launchctl print "$(launchagent_domain)/${PLIST_LABEL}" >/dev/null 2>&1 || \
    launchctl list 2>/dev/null | grep -q "$PLIST_LABEL"
}

launchagent_bootout() {
  launchctl bootout "$(launchagent_domain)/${PLIST_LABEL}" 2>/dev/null || \
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
}

launchagent_bootstrap() {
  launchctl bootstrap "$(launchagent_domain)" "$PLIST_PATH" 2>/dev/null || \
    launchctl load "$PLIST_PATH"
}

cmd_start() {
  local pid
  if pid=$(get_server_pid); then
    local running_host
    running_host=$(cat "$HOST_FILE" 2>/dev/null || echo "127.0.0.1")
    echo "既に稼働中: PID $pid  (${running_host}:${DEFAULT_PORT})"
    if [[ "$running_host" != "$BIND_HOST" ]]; then
      echo "   ⚠ バインド先が異なります。変更するには: ./service.sh restart $(is_public "$BIND_HOST" && echo --public)"
    fi
    return 0
  fi

  local fm_bin="${FM_BIN:-/usr/bin/fm}"
  if [[ ! -x "$fm_bin" ]] && ! command -v "$fm_bin" >/dev/null 2>&1; then
    echo "❌ $fm_bin が見つかりません (Apple Foundation Models CLI が必要です)" >&2
    return 1
  fi

  rotate_log_if_needed
  echo ">> 起動: ${BIND_HOST}:${DEFAULT_PORT}  (ログ: $LOG_FILE)"
  nohup mise exec -- uv run fm-serve --host "$BIND_HOST" --port "$DEFAULT_PORT" \
    --fm-bin "$fm_bin" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "$BIND_HOST" >"$HOST_FILE"
  wait_for_health
  cmd_status
}

cmd_stop() {
  local pid
  # LaunchAgent 経由なら一時的に unload して KeepAlive 再起動を防ぐ
  if [[ -f "$PLIST_PATH" ]] && launchagent_loaded; then
    echo ">> LaunchAgent を一時停止 (bootout)..."
    launchagent_bootout
  fi

  if ! pid=$(get_server_pid); then
    rm -f "$PID_FILE" "$HOST_FILE"
    echo "停止済み: :${DEFAULT_PORT}"
    return 0
  fi
  echo ">> 停止中: PID $pid ..."
  # プロキシが子の fm serve も終了させる
  kill "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  kill -9 "$pid" 2>/dev/null || true
  # 万一バックエンドが残っていたら掃除
  local leftover
  leftover=$(lsof -ti tcp:"$DEFAULT_PORT" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$leftover" ]]; then
    kill $leftover 2>/dev/null || true
  fi
  # 一時的ポートの fm serve が SIGKILL で孤立した場合の掃除
  local fm_leftover
  fm_leftover=$(pgrep -f "fm serve --host" 2>/dev/null || true)
  if [[ -n "$fm_leftover" ]]; then
    kill $fm_leftover 2>/dev/null || true
  fi
  rm -f "$PID_FILE" "$HOST_FILE"
  echo "✓ 停止"
}

cmd_restart() {
  cmd_stop || true
  # stop で unload した場合、手動 start で立ち上げる (install 済みなら再 load も可)
  if [[ -f "$PLIST_PATH" ]]; then
    # 環境を plist に反映し直して load
    cmd_install
  else
    cmd_start
  fi
}

cmd_status() {
  local pid host health_json ka_info any_public=0 label ip
  echo "fm-serve 状態  (Apple Foundation Models)"
  if pid=$(get_server_pid); then
    host=$(cat "$HOST_FILE" 2>/dev/null || echo "127.0.0.1")
    health_json=$(curl -sf "http://127.0.0.1:${DEFAULT_PORT}/health" 2>/dev/null || true)
    if [[ -n "$health_json" ]]; then
      ka_info=$(
        python3 -c "
import sys, json
d=json.loads(sys.stdin.read())
k=d.get('keepalive',{})
act=k.get('active')
rem=int(k.get('remaining_sec',0)//60)
print(f'スリープ抑制: {\"有効 (残り \"+str(rem)+\"分)\" if act else \"アイドル解除\"}')
" <<<"$health_json" 2>/dev/null || echo ""
      )
      echo "  【稼働中】  PID: $pid  bind ${host}:${DEFAULT_PORT}  http://127.0.0.1:${DEFAULT_PORT}/v1  [$ka_info]"
      if is_public "$host"; then
        any_public=1
        while read -r label ip; do
          [[ -n "$ip" ]] && printf "              %-14s http://%s:%s/v1\n" "$label" "$ip" "$DEFAULT_PORT"
        done < <(reachable_addrs)
      fi
    else
      echo "  【応答なし】 PID: $pid  :${DEFAULT_PORT}"
    fi
  else
    echo "  【停止中】  :${DEFAULT_PORT}"
  fi

  if [[ -f "$PLIST_PATH" ]]; then
    if launchagent_loaded; then
      echo "  LaunchAgent: 登録済・ロード中  ($PLIST_PATH)"
    else
      echo "  LaunchAgent: 登録済・アンロード  ($PLIST_PATH)"
    fi
  else
    echo "  LaunchAgent: 未登録  (./service.sh install でログイン時自動起動)"
  fi

  if ((any_public)); then
    echo "  ⚠ 公開バインド中 (認証なし)。初回は macOS ファイアウォールの許可ダイアログが出ることがあります。"
  fi
}

cmd_log() {
  local follow=""
  for arg in "$@"; do
    case "$arg" in
    -f | --follow) follow=1 ;;
    esac
  done
  if [[ ! -f "$LOG_FILE" ]]; then
    echo "ログなし: $LOG_FILE"
    return 0
  fi
  tail -n 40 ${follow:+-f} "$LOG_FILE"
}

cmd_install() {
  mkdir -p "$HOME/Library/LaunchAgents" "$LOGS_DIR" "$HOME/.local/bin"
  # 既存インスタンスを停止 (ポート競合・KeepAlive クラッシュループ防止)
  if get_server_pid >/dev/null; then
    echo ">> 既存インスタンスを停止中..."
    cmd_stop
  fi

  # macOS システム設定（ログイン項目）で「bash」ではなく「fm-serve」と表示させるため、
  # TCC 保護外 (~/.local/bin) に実行スクリプトを配置する
  cat <<EOF >"$LAUNCHER_BIN"
#!/bin/bash -l
set -euo pipefail
cd "${SCRIPT_DIR}"
exec mise exec -- uv run fm-serve "\$@"
EOF
  chmod +x "$LAUNCHER_BIN"

  # 現在の BIND_HOST / PORT を plist に焼き込む
  cat <<EOF >"$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>WorkingDirectory</key>
    <string>${HOME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${LAUNCHER_BIN}</string>
        <string>--host</string>
        <string>${BIND_HOST}</string>
        <string>--port</string>
        <string>${DEFAULT_PORT}</string>
        <string>--fm-bin</string>
        <string>${FM_BIN:-/usr/bin/fm}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>FM_SERVE_HOST</key>
        <string>${BIND_HOST}</string>
        <key>FM_SERVE_PORT</key>
        <string>${DEFAULT_PORT}</string>
    </dict>
</dict>
</plist>
EOF
  echo "✓ LaunchAgent を作成しました: $PLIST_PATH"
  echo "  (Foundation Models はユーザセッション必須のため LaunchDaemon ではなく LaunchAgent)"
  launchagent_bootout
  launchagent_bootstrap
  echo "$BIND_HOST" >"$HOST_FILE"
  wait_for_health || true
  cmd_status
}

cmd_uninstall() {
  if [[ -f "$PLIST_PATH" ]]; then
    launchagent_bootout
    rm -f "$PLIST_PATH" "$LAUNCHER_BIN"
    echo "✓ 自動起動の登録を解除しました。"
  else
    echo "登録されていません。"
  fi
}

cmd_help() {
  cat <<EOF
fm-serve — Apple Foundation Models (\`fm serve\`) 常駐サービス

使用法: ./service.sh <command> [options]   (引数なし → status)

  start [--public]     バックグラウンド起動
  stop                 停止
  restart [--public]   停止 → 起動 (LaunchAgent 登録済なら再 load)
  status               稼働状態 / keepalive / LaunchAgent
  log [-f]             ログ表示 (-f でフォロー)

  install [--public]   ログイン時自動常駐を登録 (LaunchAgent + KeepAlive)
                       登録先: ~/Library/LaunchAgents/${PLIST_LABEL}.plist
  uninstall            自動起動を解除

オプション:
  --public             0.0.0.0 にバインド (LAN / Tailscale、認証なし)

スリープ抑制 (KeepAlive / IOKit):
  /v1/* など API リクエスト受信のたびにシステムスリープ抑制を 1 時間延長。
  1 時間アクセスがないと自動解除。画面オフは許容。

環境変数:
  FM_SERVE_HOST        バインド先 (例: 127.0.0.1 / 0.0.0.0)
  FM_SERVE_PORT        公開ポート (既定: 1976)
  FM_SERVE_KEEPALIVE_TIMEOUT  アイドル秒 (既定: 3600)

エンドポイント:
  GET  /health
  GET  /v1/models
  POST /v1/chat/completions
EOF
}

action="${1:-status}"
shift || true

args=()
for arg in "$@"; do
  case "$arg" in
  --public) BIND_HOST="0.0.0.0" ;;
  *) args+=("$arg") ;;
  esac
done
set -- ${args[@]+"${args[@]}"}

case "$action" in
start) cmd_start ;;
stop) cmd_stop ;;
restart) cmd_restart ;;
status) cmd_status ;;
log) cmd_log "$@" ;;
install) cmd_install ;;
uninstall) cmd_uninstall ;;
help | -h | --help) cmd_help ;;
*)
  echo "❌ 不明なコマンド: $action" >&2
  cmd_help >&2
  exit 1
  ;;
esac
