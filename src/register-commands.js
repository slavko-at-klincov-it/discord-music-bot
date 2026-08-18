import { commands } from './commands.js';
import { assertBotConfig, config } from './config.js';

assertBotConfig();

const endpoint = config.guildId
  ? `https://discord.com/api/v10/applications/${config.clientId}/guilds/${config.guildId}/commands`
  : `https://discord.com/api/v10/applications/${config.clientId}/commands`;

const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), 20_000);

const response = await fetch(endpoint, {
  method: 'PUT',
  headers: {
    Authorization: `Bot ${config.token}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify(commands),
  signal: controller.signal,
});
clearTimeout(timeout);

const body = await response.json().catch(() => null);

if (!response.ok) {
  throw new Error(
    `Discord command registration failed (${response.status}): ${JSON.stringify(body)}`,
  );
}

console.log(
  config.guildId
    ? `Registered ${commands.length} guild command(s) for ${config.guildId}.`
    : `Registered ${commands.length} global command(s).`,
);
