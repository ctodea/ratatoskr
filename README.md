# Ratatoskr

A private Discord bridge to the Claude Code agents running in
[herdr](#herdr) on one Linux workstation. Each named agent gets its own Discord
channel. A message typed in the channel becomes a prompt in that agent's
terminal pane, and the agent's answer comes back to the channel.

Named for the squirrel of Norse myth that runs up and down Yggdrasil carrying
messages between the eagle at the crown and the dragon at the roots.

## Why it exists

The author runs several long-lived, named Claude Code agents side by side in
herdr on his workstation: for example a Kubernetes platform agent, a
business-ops agent and a desktop-config agent, each working in its own
repository. Ratatoskr lets him keep talking to them from his phone while he is
away from the computer, through a private Discord server where every agent has
its own channel:

```
#k8s-platform     #business-ops     #desktop-config
```

When he is back at the desk the same conversations are on screen in the panes,
and anything he typed at the computer meanwhile is already in the channel, so
each channel holds the complete history with that agent.

### Transcript tailing, not terminal scraping

Reading a terminal screen means parsing box-drawing characters, spinners,
redraws and wrapped lines, and guessing where an answer starts and ends.
Ratatoskr avoids all of that:

- **Prompts go in through herdr** (`herdr agent prompt <name> <text>`), so the
  pane at the computer shows the conversation live, exactly as if it had been
  typed there.
- **Answers come out of Claude Code's own session transcript.** herdr reports
  each agent's Claude Code session id, and Claude Code appends every turn as
  structured JSON to `~/.claude/projects/<cwd-with-dashes>/<session>.jsonl`.
  Ratatoskr tails that file, so it knows exactly which record is a user
  prompt, which is a tool call and which is the final answer.

## Architecture

```
  phone / desktop Discord client
              |
              v
     private Discord server  (one channel per agent, owner-only)
              ^       |
   answers,   |       |  owner's messages (gateway, Message Content intent)
   typing     |       v
   +----------------------------------------------------------+
   | ratatoskr.py  (systemd user service on the workstation)  |
   |                                                          |
   |  sync loop, every 30 s:  herdr agent list  -> channels   |
   |  on message:             herdr agent prompt <name> <txt> |
   |  tail loop, every 2 s:   read new transcript lines       |
   +----------------------------------------------------------+
         |                                      ^
         | herdr agent prompt                   | new JSONL lines
         v                                      |
   herdr pane running Claude Code  --writes-->  ~/.claude/projects/
   (agent "k8s-platform", ...)                  <cwd-dashed>/<session>.jsonl
```

Everything is in one file, `ratatoskr.py` (about 600 lines), with tests in
`test_ratatoskr.py`. The only dependency is `discord.py`.

## Requirements

- **Linux with systemd user services** (`systemctl --user`). Developed on Arch
  Linux (Omarchy), but nothing in the code is distribution-specific.
- **Python 3.11 or newer** (uses `tomllib`). Tested on Python 3.14.
- **herdr**, with the agents you want to bridge given a name, and the `herdr`
  binary on the PATH of your systemd user manager.
- **Claude Code** running inside those herdr panes (it writes the transcripts).
- **A Discord account**, and a server of your own that only you are in.

## 1. Discord setup

This is done once, by hand, in the browser and the Discord app. Labels below
match the Discord Developer Portal as of October 2026.

### 1.1 Create the application and the bot

1. Open <https://discord.com/developers/applications> and log in.
2. Click **New Application**, give it a name (for example `ratatoskr`), accept
   the terms and click **Create**.
3. In the left sidebar open **Bot**. New applications already have a bot user.
4. Under **Token** click **Reset Token**, confirm, and copy the token
   somewhere safe right away. Discord shows it only once; if you lose it, reset
   it again (the old one stops working).
5. Still on **Bot**, under **Privileged Gateway Intents**, turn on
   **Message Content Intent** and click **Save Changes**. Without it the bot
   cannot read what you type and refuses to start (see
   [Troubleshooting](#troubleshooting)). The other two privileged intents
   (Presence, Server Members) are not needed.
6. Optional but recommended: turn **Public Bot** off so nobody else can add
   your bot to a server. If the portal refuses with an error about a default
   authorization link, first open **Installation** in the sidebar, set
   **Install Link** to **None**, save, and then turn Public Bot off.

### 1.2 Invite the bot to your private server

1. If you do not have one yet, create a server in the Discord app (the **+**
   in the server list, **Create My Own**, **For me and my friends**). Do not
   invite anyone else to it.
2. In the Developer Portal open **OAuth2**, then **URL Generator**.
3. Under **Scopes** tick only **bot**.
4. Under **Bot Permissions** tick exactly these three, which are all the code
   uses:

   | Permission      | Why                                                     |
   |-----------------|---------------------------------------------------------|
   | Manage Channels | create the `agents` category and one channel per agent  |
   | View Channels   | see the channels and receive your messages               |
   | Send Messages   | post answers and errors, and show the typing indicator  |

   Do not grant Administrator. The resulting permissions integer is `3088`, so
   the URL looks like
   `https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&permissions=3088&scope=bot`
   (the Application ID is on the **General Information** page).
5. Copy the generated URL at the bottom of the page, open it in a browser where
   you are logged in to Discord, choose your server and click **Authorize**.
   The bot appears, offline, in the server's member list.

### 1.3 Copy the server ID and your user ID

1. In the Discord app open **User Settings** (the cog next to your name),
   **Advanced**, and turn on **Developer Mode**.
2. Right-click your server's icon and choose **Copy Server ID**. This is
   `guild_id`.
3. Right-click your own name (in the member list or on a message of yours) and
   choose **Copy User ID**. This is `owner_id`.

These IDs are not secret on their own, but they identify you; keep them in the
local config, not in the repository.

## 2. Install and run

```bash
git clone https://github.com/ctodea/ratatoskr.git
cd ratatoskr
./install.sh
```

`install.sh` is idempotent and does four things:

1. creates `.venv/` in the checkout and installs the pinned dependency from
   `requirements.txt` (set `PYTHON=/path/to/python3.x` if `python3` is older
   than 3.11);
2. copies `config.example.toml` to `~/.config/ratatoskr/config.toml` if that
   file does not exist yet, and makes it readable only by you (`chmod 600`);
3. renders `ratatoskr.service` into `~/.config/systemd/user/ratatoskr.service`
   with the path of your checkout, so the clone can live anywhere (without
   spaces in the path);
4. runs `systemctl --user daemon-reload`.

Then fill in the config and start the service:

```bash
$EDITOR ~/.config/ratatoskr/config.toml     # token, guild_id, owner_id
systemctl --user enable --now ratatoskr
journalctl --user -u ratatoskr -f           # watch it log in and create channels
```

A healthy start logs `logged in as <bot name>`, then one
`<agent> -> session <id> (<path>)` line per named agent, and `created #<agent>`
the first time it sees an agent.

### Configuration

`~/.config/ratatoskr/config.toml`:

| Key                         | Default                   | Meaning                                              |
|-----------------------------|---------------------------|------------------------------------------------------|
| `discord.token`             | required                  | bot token from the Developer Portal                  |
| `discord.guild_id`          | required                  | your server's ID                                     |
| `discord.owner_id`          | required                  | your user ID; messages from anyone else are ignored |
| `bridge.category`           | `"agents"`                | category the agent channels are created under       |
| `bridge.sync_agents`        | `30.0`                    | seconds between `herdr agent list` scans            |
| `bridge.poll_transcripts`   | `2.0`                     | seconds between transcript polls                     |
| `bridge.exclude`            | `[]`                      | agent names never bridged, e.g. `["main"]`          |
| `bridge.mirror_label`       | `"typed at the computer"` | grey label above prompts mirrored from the computer |

The config is read once at start; restart the service after editing it.

### Updating

```bash
cd ratatoskr
git pull
./install.sh                          # picks up dependency or unit changes
systemctl --user restart ratatoskr
```

### Uninstalling

```bash
systemctl --user disable --now ratatoskr
rm ~/.config/systemd/user/ratatoskr.service
systemctl --user daemon-reload
rm -r ~/.config/ratatoskr             # deletes the token too
```

## 3. How it behaves

- **One channel per named agent.** Every 30 seconds the bot runs
  `herdr agent list`. Each agent with a name gets a text channel under the
  `agents` category, named after the agent in lowercase with anything other
  than letters and digits turned into `-`. Naming an agent is the opt-in:
  unnamed panes are never bridged, and names in `exclude` are skipped. An
  existing channel with the right name is reused.
- **Agents that go away keep their channel.** The channel holds the history;
  it simply stops being tailed. If the agent comes back, it is picked up again.
- **A new session is followed automatically.** If an agent's pane is restarted
  with a fresh Claude Code session, the next sync sees the new session id and
  switches to that transcript.
- **Your messages become prompts.** A message from `owner_id` in an agent's
  channel is sent with `herdr agent prompt`. If herdr fails, the bot answers
  with `could not reach <agent>: <error>`.
- **Only final answers are posted.** Text the agent writes between tool calls
  ("now I check the tests") stays in the terminal. While the agent works, the
  channel shows Discord's typing indicator instead, refreshed every 8 seconds
  whenever new transcript lines show it is still mid-turn. If a turn ends
  without a final answer (for example it was interrupted), its last progress
  note is posted so the turn is not silent. Tool calls, tool output, thinking,
  compaction summaries and other harness records never reach Discord.
- **Turns typed at the computer are mirrored.** A prompt typed directly in the
  pane appears in the channel as a quote under a small grey label
  (`bridge.mirror_label`). Prompts sent from Discord are recognised when they
  echo back in the transcript and are not posted twice.
- **Formatting is adapted to Discord.** Markdown tables, which Discord does
  not render, become bullet lists; horizontal rules are dropped. Long answers
  are split below Discord's 2000 character limit at paragraph boundaries, and
  a code block that has to be cut is closed and reopened in the next message.
- **Stale sessions are rebound.** For resumed sessions herdr's session id can
  point at an old transcript file. If a prompt sent from Discord has not shown
  up in the tailed file within 15 seconds, the bot searches the transcripts
  modified in the last five minutes for that text, switches to the file that
  received it, and posts `re-attached to this agent's live transcript`.
- **History starts when the bot attaches.** On start, and on each new session,
  tailing begins at the end of the transcript. Older turns are never posted,
  and turns that happen while the bot is stopped are not posted later either.

### Limits

- **Owner-only.** Exactly one Discord user can drive the agents.
- **One machine.** The bot bridges the herdr instance on the machine it runs
  on. For a second machine, create a second bot (and ideally a second
  category or server); two processes sharing one token would interfere with each other.
- **Text only.** Only the message text is forwarded. Attachments, images,
  embeds and reactions are ignored, and the bot posts plain text only.
- **No interactive prompts.** If Claude Code stops to ask for a tool permission
  or a choice in the pane, that question is not relayed; answer it at the
  computer (or run those agents in a permission mode that does not ask).
- **Polling.** Answers arrive up to `poll_transcripts` seconds late, new agents
  up to `sync_agents` seconds late.

## 4. Security notes

- **Your Discord account becomes a remote shell.** Whoever can post as
  `owner_id` in that server can make your agents run commands with your
  permissions on your workstation. Protect the Discord account with two-factor
  authentication and keep the server private, with no other members.
- **What the bot token gives.** An attacker with the token can log in as the
  bot: read every channel it can see (that is, the full history of your agent
  conversations, including anything the agents printed), post fake answers,
  and create or delete channels. They cannot send prompts to the agents
  through your running bot, because it only acts on messages whose author is
  `owner_id`, and Discord, not the sender, sets the author. If the token leaks,
  reset it on the **Bot** page at once, update the config and restart.
- **Keep the token out of git.** The config lives in `~/.config/ratatoskr/`,
  outside the repository, and `.gitignore` also excludes any stray
  `config.toml`. `install.sh` makes the config readable only by you.
- **Discord sees everything.** Messages are not end-to-end encrypted. Do not
  bridge agents whose conversations must not leave the machine; list them in
  `exclude`.
- **Ignored, not answered.** Messages from other users are dropped silently,
  so the bot gives nothing away to someone who ends up in the server.

## 5. Adapting it to another setup

The parts below are specific to the author's environment. Each one is a small,
well-defined seam in `ratatoskr.py`.

### herdr

The bridge depends on two herdr commands:

- `herdr agent list` must print JSON with
  `result.agents[]`, each having `name`, `agent_session.value` (the Claude
  Code session id), `cwd` and `pane_id`. See `herdr_agents()`.
- `herdr agent prompt <name> <text>` must type the text into that agent's
  pane. See `herdr_prompt()`.

Name an agent with herdr to have it bridged. To use a different terminal
multiplexer (tmux, zellij), replace those two functions: one must return the
running agents with their session id and working directory, the other must
deliver a prompt to a pane (for tmux, `tmux send-keys`).

### Claude Code transcripts

Transcripts are found at `~/.claude/projects/<cwd>/<session>.jsonl`, where
`<cwd>` is the agent's working directory with `/` replaced by `-`. If that file
does not exist (for example after a `/cd`, or if Claude Code's directory naming
differs), the bot falls back to looking up `<session>.jsonl` in every project
directory. The parser relies on these record shapes: `type` of `user`,
`assistant` or `system` (`subtype: turn_duration` marks the end of a turn),
`message.content` text blocks, and `message.stop_reason` (`tool_use` means the
agent is still working). If a Claude Code update changes them, the tests in
`test_ratatoskr.py` show the expected input.

### Omarchy, Arch and systemd

Nothing in the code depends on Omarchy or Arch. What matters:

- **systemd user services.** The service runs only while your user session
  exists. That is fine here, because herdr itself runs in your session. If you
  want it to start at boot without a login, run `loginctl enable-linger`.
- **PATH.** The service calls `herdr` by name, so it must be on the PATH of the
  user manager (`systemctl --user show-environment | grep PATH`). If it is not,
  add it in a drop-in, which survives re-running `install.sh`:

  ```bash
  systemctl --user edit ratatoskr
  # [Service]
  # Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin
  ```

- **Without systemd**, run it in the foreground or under any supervisor:
  `.venv/bin/python ratatoskr.py`.

## 6. Tests

```bash
.venv/bin/python -m unittest -v
```

The tests cover transcript parsing (final answers only, dropped harness
records, unwrapping of pasted content, echo detection), the Discord formatting
(tables, code blocks, chunking) and the config defaults. They need neither
Discord nor herdr.

## Troubleshooting

Start with `journalctl --user -u ratatoskr -f`.

| Symptom in the log | Cause and fix |
|---|---|
| `ClientConnectorDNSError: Cannot connect to host discord.com` / `Temporary failure in name resolution`, right after login or boot | The network was not up yet. Expected; the unit restarts after 5 seconds and the second attempt connects. In a user manager `network-online.target` usually does not exist, so it cannot wait for the network. Only investigate if it keeps failing. |
| `PrivilegedIntentsRequired` / "requesting privileged intents that have not been explicitly enabled", and the service keeps restarting | Message Content Intent is off. Turn it on (section 1.1, step 5) and restart. |
| `LoginFailure: Improper token has been passed` | Wrong or reset token. Reset it on the Bot page, paste it into the config, restart. |
| `no config at ~/.config/ratatoskr/config.toml` | Run `./install.sh` or copy `config.example.toml` there. |
| `guild <id> not visible - is the bot invited?` | The bot is not in that server, or `guild_id` is wrong (copy it again with Developer Mode on). |
| `agent sync failed` with `herdr agent list failed`, or `FileNotFoundError: 'herdr'` | herdr is not running or not on the service's PATH. See [PATH](#omarchy-arch-and-systemd). |
| An agent has no channel | It has no name in herdr, it is listed in `exclude`, or the next sync (30 s) has not run yet. |
| Your messages are ignored, nothing in the log | `owner_id` is not your user ID, or you wrote in a channel that is not an agent channel. |
| `<agent> rebound to live transcript ...` and a "re-attached" message in the channel | herdr reported a stale session id; the bot found the live transcript. No action needed. |
| `<agent>: injected prompt never surfaced in any transcript` | The prompt reached herdr but no transcript modified in the last five minutes contains it, for example the pane was busy in a dialog, or the agent is not Claude Code. Check the pane at the computer. |
| Answers stop arriving after restarting an agent's pane | They resume at the next sync (30 s), when the new session id is picked up. If the pane was resumed into an older session, send one message: the rebind above finds the right file. |
| Turns from while the bot was stopped are missing | By design: tailing starts at the end of the transcript when the bot attaches. |
| A channel was renamed by hand and a new one appeared | Channels are matched by name when the bot (re)starts. Keep the agent's name or rename the agent in herdr. |

## License

MIT, see [LICENSE](LICENSE).
