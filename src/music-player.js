import { spawn } from 'node:child_process';
import {
  AudioPlayerStatus,
  createAudioPlayer,
  createAudioResource,
  entersState,
  getVoiceConnection,
  joinVoiceChannel,
  NoSubscriberBehavior,
  StreamType,
  VoiceConnectionStatus,
} from '@discordjs/voice';
import { config } from './config.js';
import { killProcess, runForStdout } from './process-utils.js';
import { formatDuration } from './youtube.js';

class GuildMusicPlayer {
  constructor(guildId) {
    this.guildId = guildId;
    this.queue = [];
    this.current = null;
    this.connection = null;
    this.ytdlp = null;
    this.ffmpeg = null;
    this.stopping = false;
    this.audioPlayer = createAudioPlayer({
      behaviors: {
        noSubscriber: NoSubscriberBehavior.Pause,
      },
    });

    this.audioPlayer.on(AudioPlayerStatus.Idle, () => {
      if (!this.stopping) void this.playNext();
    });

    this.audioPlayer.on('error', (error) => {
      console.error(`Audio player error in guild ${this.guildId}:`, error);
      this.cleanupProcesses();
      if (!this.stopping) void this.playNext();
    });
  }

  async connect(voiceChannel) {
    const existing = getVoiceConnection(this.guildId);
    if (existing) {
      this.connection = existing;
    } else {
      this.connection = joinVoiceChannel({
        channelId: voiceChannel.id,
        guildId: voiceChannel.guild.id,
        adapterCreator: voiceChannel.guild.voiceAdapterCreator,
        selfDeaf: true,
      });
    }

    this.connection.subscribe(this.audioPlayer);
    await entersState(this.connection, VoiceConnectionStatus.Ready, 20_000);
  }

  enqueue(track) {
    this.queue.push(track);
    if (this.audioPlayer.state.status === AudioPlayerStatus.Idle && !this.current) {
      void this.playNext();
    }
  }

  async playNext() {
    this.cleanupProcesses();
    this.current = this.queue.shift() || null;

    if (!this.current) {
      this.connection?.destroy();
      this.connection = null;
      return;
    }

    try {
      const resource = await this.createResource(this.current);
      this.audioPlayer.play(resource);
    } catch (error) {
      console.error(`Failed to start track in guild ${this.guildId}:`, error);
      this.cleanupProcesses();
      if (!this.stopping) void this.playNext();
    }
  }

  async resolveStreamUrl(track) {
    const stdout = await runForStdout(
      config.ytdlpPath,
      [
        '--no-playlist',
        '--format',
        'bestaudio/best',
        '--get-url',
        track.url,
      ],
      { timeoutMs: 30_000 },
    );

    const streamUrl = stdout.trim().split(/\r?\n/).find(Boolean);
    if (!streamUrl) {
      throw new Error(`yt-dlp returned no stream URL for ${track.url}`);
    }
    return streamUrl;
  }

  async createResource(track) {
    const streamUrl = await this.resolveStreamUrl(track);
    const inputArgs = [
      '-hide_banner',
      '-loglevel',
      'warning',
      '-reconnect',
      '1',
      '-reconnect_streamed',
      '1',
      '-reconnect_delay_max',
      '5',
    ];
    if (track.startSeconds && track.startSeconds > 0) {
      inputArgs.push('-ss', String(track.startSeconds));
    }
    inputArgs.push('-i', streamUrl);

    this.ffmpeg = spawn(
      config.ffmpegPath,
      [
        ...inputArgs,
        '-f',
        's16le',
        '-ar',
        '48000',
        '-ac',
        '2',
        'pipe:1',
      ],
      { stdio: ['pipe', 'pipe', 'pipe'] },
    );

    this.ffmpeg.stderr.on('data', (chunk) => {
      const text = chunk.toString().trim();
      if (text) console.warn(`ffmpeg: ${text}`);
    });

    this.ffmpeg.on('error', (error) => console.error('ffmpeg failed:', error));

    return createAudioResource(this.ffmpeg.stdout, {
      inputType: StreamType.Raw,
      metadata: track,
    });
  }

  skip() {
    this.cleanupProcesses();
    this.audioPlayer.stop(true);
  }

  pause() {
    return this.audioPlayer.pause();
  }

  resume() {
    return this.audioPlayer.unpause();
  }

  stop() {
    this.stopping = true;
    this.queue = [];
    this.current = null;
    this.cleanupProcesses();
    this.audioPlayer.stop(true);
    this.connection?.destroy();
    this.connection = null;
    this.stopping = false;
  }

  cleanupProcesses() {
    killProcess(this.ytdlp);
    killProcess(this.ffmpeg);
    this.ytdlp = null;
    this.ffmpeg = null;
  }

  queueSummary() {
    const lines = [];
    if (this.current) {
      lines.push(`Now: ${this.current.title} (${formatDuration(this.current.duration)})`);
    }
    if (this.queue.length === 0) {
      lines.push('Queue is empty.');
    } else {
      lines.push(
        ...this.queue.slice(0, 10).map((track, index) => {
          return `${index + 1}. ${track.title} (${formatDuration(track.duration)})`;
        }),
      );
      if (this.queue.length > 10) lines.push(`...and ${this.queue.length - 10} more.`);
    }
    return lines.join('\n');
  }
}

export class MusicManager {
  constructor() {
    this.players = new Map();
  }

  get(guildId) {
    if (!this.players.has(guildId)) {
      this.players.set(guildId, new GuildMusicPlayer(guildId));
    }
    return this.players.get(guildId);
  }

  delete(guildId) {
    const player = this.players.get(guildId);
    player?.stop();
    this.players.delete(guildId);
  }
}
