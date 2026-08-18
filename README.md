# Discord Music Bot for macOS

A self-hosted Discord music bot for macOS. It accepts slash and prefix commands,
resolves YouTube audio with `yt-dlp`, and delivers audio to Discord voice channels
through FFmpeg.

> This project is intended for private servers where you have the necessary
> permissions. Do not commit Discord tokens, browser cookies, or other account
> credentials.

## Features

- `/play` and `m!play` support for YouTube URLs and search terms
- Queue controls: skip, pause, resume, stop, and clear
- YouTube timestamp support, including `t=`, `start=`, and `#t=` links
- Optional rhythm-based similar-track suggestions and autoplay
- FFmpeg sound presets: `bass`, `jazz`, `rock`, `wow`, and `flat`
- macOS `launchd` installer for persistent operation
- Diagnostics for Discord configuration, `yt-dlp`, FFmpeg, Python, and playback

## Requirements

- macOS with Homebrew
- Node.js 22 or later
- Python 3.12
- FFmpeg and `yt-dlp`
- A Discord application and bot token

Install the system dependencies:

```bash
brew install node python@3.12 ffmpeg yt-dlp
```

## Install

```bash
git clone https://github.com/slavko-at-klincov-it/discord-music-bot.git
cd discord-music-bot
npm ci
npm run setup:python
cp .env.example .env
```

Edit `.env` and enter your own Discord values:

```dotenv
DISCORD_TOKEN=your-bot-token
DISCORD_CLIENT_ID=your-application-id
DISCORD_GUILD_ID=your-test-server-id
YT_DLP_PATH=/opt/homebrew/bin/yt-dlp
FFMPEG_PATH=/opt/homebrew/bin/ffmpeg
```

`.env` is deliberately ignored by Git. Never publish it.

## Discord setup

1. Create an application and bot in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Add `DISCORD_TOKEN` and `DISCORD_CLIENT_ID` to `.env`.
3. Invite the bot with the `bot` and `applications.commands` scopes.
4. Grant at least **View Channels**, **Send Messages**, **Connect**, and **Speak**.
5. Enable **Message Content Intent** if you want to use `m!` prefix commands.
6. Set `DISCORD_GUILD_ID` while developing so slash-command changes are available immediately in that server.

## Start and verify

```bash
npm run doctor
npm run doctor:python
npm run test:python
npm run smoke:youtube
npm run register
npm start
```

The YouTube smoke test resolves a track and asks FFmpeg to decode two seconds of
audio. It does not connect to a Discord voice channel.

## Commands

Slash commands:

```text
/play <URL or search>
/queue
/skip
/pause
/resume
/stop
/clear
/nowplaying
```

Prefix commands:

```text
m!play <URL or search>
m!now <URL or search>
m!queue | m!skip | m!pause | m!resume | m!stop | m!clear | m!np
m!bass | m!jazz | m!rock | m!wow | m!flat
m!similar [URL or search]
m!similar play <URL or search>
m!autoplay on | off | status
```

`m!clear` clears only pending music. `/clear` deletes non-pinned messages in the
current channel and requires the **Manage Messages** permission for both the
invoking member and the bot.

## Run continuously with launchd

After the checks above pass, install the user LaunchAgent:

```bash
chmod +x scripts/install-launchd.sh
./scripts/install-launchd.sh
```

The installer derives the current checkout path automatically, creates a
project-specific LaunchAgent, and starts it. Stop it with:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.slavko.discord-music-bot.plist
```

If the project moves, rebuild the virtual environment and reinstall the agent
from the new checkout:

```bash
npm run setup:python
./scripts/install-launchd.sh
```

## Troubleshooting

```bash
npm run doctor:python
npm run smoke:youtube -- "never gonna give you up"
brew upgrade yt-dlp
```

YouTube changes frequently. Keep `yt-dlp` current if playback begins returning
HTTP 403 errors.

## Security and privacy

- Keep `.env`, browser cookies, and logs private.
- The repository intentionally ignores local runtime data, audio-analysis cache,
  virtual environments, dependencies, and the local operational handoff file.
- Use the bot only where you are authorized to stream and play the requested
  content.

## License

No open-source license has been selected yet. Publishing this repository does
not grant reuse rights beyond those required to view it on GitHub.
