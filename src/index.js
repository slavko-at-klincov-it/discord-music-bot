import {
  Client,
  GatewayIntentBits,
  InteractionContextType,
  MessageFlags,
} from 'discord.js';
import { assertBotConfig, config } from './config.js';
import { MusicManager } from './music-player.js';
import { resolveTrack, formatDuration } from './youtube.js';

assertBotConfig();

const client = new Client({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
});

const music = new MusicManager();

client.once('ready', () => {
  console.log(`Logged in as ${client.user.tag}.`);
});

client.on('interactionCreate', async (interaction) => {
  if (!interaction.isChatInputCommand()) return;
  if (interaction.context === InteractionContextType.BotDM) {
    await interaction.reply({
      content: 'Use this command inside a server voice channel.',
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  try {
    switch (interaction.commandName) {
      case 'play':
        await handlePlay(interaction);
        break;
      case 'skip':
        await handleSkip(interaction);
        break;
      case 'stop':
        await handleStop(interaction);
        break;
      case 'pause':
        await handlePause(interaction);
        break;
      case 'resume':
        await handleResume(interaction);
        break;
      case 'queue':
        await handleQueue(interaction);
        break;
      case 'nowplaying':
        await handleNowPlaying(interaction);
        break;
      default:
        await interaction.reply({
          content: 'Unknown command.',
          flags: MessageFlags.Ephemeral,
        });
    }
  } catch (error) {
    console.error(error);
    const content = `Command failed: ${error.message}`;
    if (interaction.deferred || interaction.replied) {
      await interaction.editReply(content);
    } else {
      await interaction.reply({ content, flags: MessageFlags.Ephemeral });
    }
  }
});

async function handlePlay(interaction) {
  const voiceChannel = interaction.member?.voice?.channel;
  if (!voiceChannel) {
    await interaction.reply({
      content: 'Join a voice channel first.',
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  await interaction.deferReply();
  const query = interaction.options.getString('query', true);
  const player = music.get(interaction.guildId);
  await player.connect(voiceChannel);

  const track = await resolveTrack(query, interaction.user.username);
  player.enqueue(track);

  await interaction.editReply(
    `Queued: ${track.title} (${formatDuration(track.duration)})`,
  );
}

async function handleSkip(interaction) {
  const player = music.get(interaction.guildId);
  player.skip();
  await interaction.reply('Skipped.');
}

async function handleStop(interaction) {
  music.delete(interaction.guildId);
  await interaction.reply('Stopped and cleared the queue.');
}

async function handlePause(interaction) {
  const ok = music.get(interaction.guildId).pause();
  await interaction.reply(ok ? 'Paused.' : 'Nothing is playing.');
}

async function handleResume(interaction) {
  const ok = music.get(interaction.guildId).resume();
  await interaction.reply(ok ? 'Resumed.' : 'Nothing is paused.');
}

async function handleQueue(interaction) {
  await interaction.reply(music.get(interaction.guildId).queueSummary());
}

async function handleNowPlaying(interaction) {
  const current = music.get(interaction.guildId).current;
  await interaction.reply(
    current
      ? `Now playing: ${current.title} (${formatDuration(current.duration)})`
      : 'Nothing is playing.',
  );
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));

function shutdown(signal) {
  console.log(`Received ${signal}, shutting down.`);
  for (const guildId of music.players.keys()) {
    music.delete(guildId);
  }
  client.destroy();
  process.exit(0);
}

await client.login(config.token);
