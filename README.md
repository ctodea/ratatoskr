# Ratatoskr

Talk to the herdr agents on this machine from a private Discord server —
one channel per agent, full history, channels created automatically.

Named for the squirrel of Norse myth that runs up and down Yggdrasil carrying
messages between the eagle at the crown and Nidhogg at the roots — which is
precisely and only what this does. Successor to Saga1, an OpenClaw-based build
of the same idea; this one is self-contained and owes it nothing.

## How it works

```
phone ── Discord ── ratatoskr.py ──┬── herdr agent prompt ──> the agent's real pane
                               └── tails the session's JSONL transcript <── replies
```

The trick that makes this clean: **no terminal scraping**. herdr exposes each
agent's Claude Code session id (`herdr agent list` → `agent_session.value`),
and Claude Code writes a structured transcript per session under
`~/.claude/projects/<cwd-with-dashes>/<session>.jsonl`. Ratatoskr injects prompts
through herdr — so the pane at the computer shows the conversation live — and
reads replies from the transcript, where user and assistant turns are typed
JSON rather than a screenful of box-drawing characters.

Because the tailer sees *every* turn, messages typed at the computer are
mirrored into the channel too (quoted, under a small "desde el PC" label), and
prompts sent from Discord are deduplicated when they echo back. The channel ends
up holding the complete history of that agent regardless of where each message
was written.

Only the answer reaches Discord. Text the agent writes between tool calls
(progress notes, `stop_reason: tool_use`) stays in the terminal; while the
agent works the channel shows "typing...". If a turn ends without a final
answer (interrupted), its last note is posted so the turn is not silent. The
compaction summary and other harness-generated "user" records are dropped.

Discord does not render Markdown tables, so they are rewritten as lists, and
long answers are split at paragraph boundaries, closing and reopening a code
block if one is cut. Tests: `.venv/bin/python -m unittest -v`.

Channel sync runs every 30s: a newly named herdr agent gets a channel under
the `agents` category automatically; a vanished agent keeps its channel (it
holds the history) but stops being tailed. Naming an agent is the opt-in —
unnamed panes are never bridged.

## Setup

The one part that cannot be scripted is Discord's side. Once, by hand:

1. Create a personal Discord server (just you in it).
2. https://discord.com/developers/applications → **New Application** → name it
   (e.g. `ratatoskr`).
3. **Bot** tab → enable **Message Content Intent** (required — without it every
   message arrives empty). Copy the **token**.
4. **OAuth2 → URL Generator**: scope `bot`; permissions **View Channels,
   Send Messages, Read Message History, Manage Channels** (Manage Channels is
   what allows auto-creating agent channels). Open the generated URL, invite
   the bot to your server.
5. In Discord: Settings → Advanced → **Developer Mode** on. Right-click the
   server → Copy Server ID; right-click yourself → Copy User ID.

Then on this machine:

```bash
./install.sh                          # venv, deps, config skeleton, systemd unit
$EDITOR ~/.config/ratatoskr/config.toml   # token, guild_id, owner_id
systemctl --user enable --now ratatoskr
journalctl --user -u ratatoskr -f         # watch it create the channels
```

## Security model

- Only messages from `owner_id` are acted on; everything else is ignored.
- The bot token grants control of the bot, not of this machine — but a prompt
  IS remote code execution by proxy (the agents run with your permissions), so
  keep the server private and the token out of git. Config lives in
  `~/.config/ratatoskr/`, never in this repo.
- History starts at wiring time: transcript content from before Ratatoskr attached
  to a session is never posted.

## Notes

- Message cap is handled by chunking at 1900 chars.
- `exclude = ["main"]` in the config keeps a main interactive session out of
  Discord if talking to it from two places at once gets confusing.
- If an agent's pane is restarted (`herdr-agent <name>` spawns a fresh Claude
  session), the next sync re-attaches to the new session id automatically.
