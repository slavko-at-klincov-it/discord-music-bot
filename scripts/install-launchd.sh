#!/usr/bin/env bash
set -euo pipefail

LABEL="com.slavko.discord-music-bot"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SOURCE="${PROJECT_DIR}/launchd/${LABEL}.plist"
TARGET="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
PYTHON="${PROJECT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Missing executable: ${PYTHON}" >&2
  echo "Create the project runtime with: npm run setup:python" >&2
  exit 1
fi

if [[ ! -x /opt/homebrew/bin/yt-dlp ]]; then
  echo "Missing executable: /opt/homebrew/bin/yt-dlp" >&2
  echo "Install it with: brew install yt-dlp" >&2
  exit 1
fi

if [[ ! -x /opt/homebrew/bin/ffmpeg ]]; then
  echo "Missing executable: /opt/homebrew/bin/ffmpeg" >&2
  echo "Install it with: brew install ffmpeg" >&2
  exit 1
fi

mkdir -p "${HOME}/Library/LaunchAgents"
ESCAPED_PROJECT_DIR="${PROJECT_DIR//\\/\\\\}"
ESCAPED_PROJECT_DIR="${ESCAPED_PROJECT_DIR//&/\\&}"
ESCAPED_PROJECT_DIR="${ESCAPED_PROJECT_DIR//#/\\#}"
sed "s#__PROJECT_DIR__#${ESCAPED_PROJECT_DIR}#g" "${SOURCE}" > "${TARGET}"
plutil -lint "${TARGET}" >/dev/null

launchctl bootout "${DOMAIN}" "${TARGET}" 2>/dev/null || true
launchctl bootstrap "${DOMAIN}" "${TARGET}"
launchctl enable "${DOMAIN}/${LABEL}"
launchctl kickstart -k "${DOMAIN}/${LABEL}"

echo "Installed and started ${LABEL}."
echo "Project: ${PROJECT_DIR}"
echo "Logs: /tmp/discord-music-bot.out.log and /tmp/discord-music-bot.err.log"
