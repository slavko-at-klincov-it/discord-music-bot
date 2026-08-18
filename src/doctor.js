import { access } from 'node:fs/promises';
import { constants } from 'node:fs';
import { assertBotConfig, config } from './config.js';
import { runForStdout } from './process-utils.js';

async function checkExecutable(label, command, args) {
  try {
    if (command.includes('/')) {
      await access(command, constants.X_OK);
    }
    const output = await runForStdout(command, args, { timeoutMs: 10_000 });
    console.log(`OK ${label}: ${output.trim().split('\n')[0]}`);
  } catch (error) {
    console.error(`FAIL ${label}: ${error.message}`);
    process.exitCode = 1;
  }
}

try {
  assertBotConfig();
  console.log('OK env: DISCORD_TOKEN and DISCORD_CLIENT_ID are set.');
} catch (error) {
  console.error(`FAIL env: ${error.message}`);
  process.exitCode = 1;
}

await checkExecutable('yt-dlp', config.ytdlpPath, ['--version']);
await checkExecutable('ffmpeg', config.ffmpegPath, ['-version']);
