export const commands = [
  {
    name: 'play',
    description: 'Play a YouTube URL or search query in your current voice channel.',
    type: 1,
    options: [
      {
        name: 'query',
        description: 'YouTube URL or search text',
        type: 3,
        required: true,
      },
    ],
  },
  {
    name: 'skip',
    description: 'Skip the current track.',
    type: 1,
  },
  {
    name: 'stop',
    description: 'Stop playback, clear the queue, and leave the voice channel.',
    type: 1,
  },
  {
    name: 'pause',
    description: 'Pause playback.',
    type: 1,
  },
  {
    name: 'resume',
    description: 'Resume playback.',
    type: 1,
  },
  {
    name: 'queue',
    description: 'Show the current queue.',
    type: 1,
  },
  {
    name: 'nowplaying',
    description: 'Show the currently playing track.',
    type: 1,
  },
  {
    name: 'clear',
    description: 'Delete all non-pinned messages in this channel.',
    type: 1,
    default_member_permissions: '8192',
  },
];
