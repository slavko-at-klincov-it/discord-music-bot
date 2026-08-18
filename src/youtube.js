import { config } from './config.js';
import { runForStdout } from './process-utils.js';

const URL_PATTERN = /^https?:\/\//i;
const TIMESTAMP_TOKEN_PATTERN = /(\d+)([hms])/gi;

export function parseTimestampSeconds(value) {
  if (!value) return null;

  const normalized = String(value).trim().toLowerCase().replace(/\s+/g, '');
  if (!normalized) return null;
  if (/^\d+$/.test(normalized)) return Number.parseInt(normalized, 10);

  if (normalized.includes(':')) {
    const parts = normalized.split(':');
    if (parts.length >= 2 && parts.length <= 3 && parts.every((part) => /^\d+$/.test(part))) {
      const values = parts.map((part) => Number.parseInt(part, 10));
      if (values.length === 2) {
        const [minutes, seconds] = values;
        return minutes * 60 + seconds;
      }
      const [hours, minutes, seconds] = values;
      return hours * 3600 + minutes * 60 + seconds;
    }
    return null;
  }

  const matches = [...normalized.matchAll(TIMESTAMP_TOKEN_PATTERN)];
  TIMESTAMP_TOKEN_PATTERN.lastIndex = 0;
  if (!matches.length || matches.map((match) => match[0]).join('') !== normalized) {
    return null;
  }

  return matches.reduce((total, match) => {
    const amount = Number.parseInt(match[1], 10);
    const unit = match[2].toLowerCase();
    if (unit === 'h') return total + amount * 3600;
    if (unit === 'm') return total + amount * 60;
    return total + amount;
  }, 0);
}

export function extractStartSeconds(query) {
  if (!URL_PATTERN.test(query)) return null;

  let parsed;
  try {
    parsed = new URL(query);
  } catch {
    return null;
  }

  for (const rawParams of [parsed.search.slice(1), parsed.hash.slice(1)]) {
    if (!rawParams) continue;

    const params = new URLSearchParams(rawParams);
    for (const name of ['t', 'start', 'time_continue']) {
      const seconds = parseTimestampSeconds(params.get(name));
      if (seconds && seconds > 0) return seconds;
    }

    if (!rawParams.includes('=')) {
      const seconds = parseTimestampSeconds(rawParams);
      if (seconds && seconds > 0) return seconds;
    }
  }

  return null;
}

export async function resolveTrack(query, requestedBy) {
  const input = URL_PATTERN.test(query) ? query : `ytsearch1:${query}`;
  const startSeconds = extractStartSeconds(query);
  const stdout = await runForStdout(
    config.ytdlpPath,
    [
      '--dump-single-json',
      '--no-playlist',
      '--default-search',
      'ytsearch',
      '--format',
      'bestaudio/best',
      input,
    ],
    { timeoutMs: 30_000 },
  );

  const info = JSON.parse(stdout);
  const entry = Array.isArray(info.entries) ? info.entries.find(Boolean) : info;

  if (!entry) {
    throw new Error('No YouTube result found.');
  }

  return {
    title: entry.title || 'Unknown title',
    url: entry.webpage_url || entry.original_url || query,
    duration: Number.isFinite(entry.duration) ? entry.duration : null,
    requestedBy,
    startSeconds,
  };
}

export function formatDuration(seconds) {
  if (!seconds) return 'live/unknown';
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}
