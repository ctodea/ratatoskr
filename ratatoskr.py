#!/usr/bin/env python3
"""Ratatoskr - talk to the herdr agents on this machine from a private Discord server.

Named for the squirrel that runs up and down Yggdrasil relaying messages between
the eagle at the crown and Nidhogg at the roots, which is the whole job here.

One text channel per named herdr agent, created automatically when a new agent
appears. A message typed into a channel is injected into that agent's pane via
`herdr agent prompt`, so the pane at the computer shows the conversation live.
Replies are NOT scraped from the terminal: herdr exposes each agent's Claude
Code session id, and Claude Code writes a structured JSONL transcript per
session - Ratatoskr tails that file and posts new turns to the channel. Turns typed
at the computer are mirrored too, so the channel holds the full history of the
chat with that agent no matter where a message was written.
"""

import asyncio
import json
import logging
import subprocess
import sys
import time
import tomllib
from collections import deque
from pathlib import Path

import discord

log = logging.getLogger("ratatoskr")

CONFIG_PATH = Path.home() / ".config/ratatoskr/config.toml"
PROJECTS_DIR = Path.home() / ".claude/projects"
DISCORD_LIMIT = 1900  # hard limit is 2000; leave room for prefixes


# --------------------------------------------------------------------------- #
# herdr

def herdr_agents() -> dict[str, dict]:
    """Named herdr agents, keyed by name. Unnamed panes are not bridged -
    naming an agent (herdr agent rename / herdr-agent) is the opt-in."""
    out = subprocess.run(
        ["herdr", "agent", "list"], capture_output=True, text=True, timeout=10
    )
    if out.returncode != 0:
        raise RuntimeError(f"herdr agent list failed: {out.stderr.strip()}")

    agents = {}
    for a in json.loads(out.stdout)["result"]["agents"]:
        name = a.get("name")
        if not name:
            continue
        agents[name] = {
            "session": a["agent_session"]["value"],
            "cwd": a["cwd"],
            "pane": a["pane_id"],
        }
    return agents


def herdr_prompt(name: str, text: str) -> str | None:
    """Send a prompt into an agent's pane. Returns an error string or None."""
    out = subprocess.run(
        ["herdr", "agent", "prompt", name, text],
        capture_output=True, text=True, timeout=15,
    )
    if out.returncode != 0:
        return out.stderr.strip() or out.stdout.strip() or "herdr agent prompt failed"
    return None


def transcript_path(session: str, cwd: str) -> Path:
    """Claude Code writes transcripts under a directory named after the
    session's working directory with '/' replaced by '-'."""
    return PROJECTS_DIR / cwd.replace("/", "-") / f"{session}.jsonl"


# --------------------------------------------------------------------------- #
# transcript parsing

def turns_from_lines(lines: list[str]):
    """Yield ("user"|"assistant", text) from raw transcript JSONL lines.

    Only real conversation text survives: tool calls, tool results, thinking
    blocks, attachments, and Claude Code's bookkeeping records are dropped.
    """
    for line in lines:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue

        kind = d.get("type")
        if kind not in ("user", "assistant"):
            continue

        content = d.get("message", {}).get("content")
        if isinstance(content, str):
            text = content.strip()
            # Skip slash-command echoes and interrupt markers.
            if text and not text.startswith(("<", "[Request interrupted")):
                yield kind, text
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        yield kind, text


# --------------------------------------------------------------------------- #
# the bridge

class AgentLink:
    """One herdr agent wired to one Discord channel."""

    # If an injected prompt's echo hasn't shown up in the tailed file after
    # this long, we are tailing the wrong file (herdr's session id can be
    # stale for resumed sessions) and go looking for the right one.
    ECHO_DEADLINE = 15.0

    def __init__(self, name: str):
        self.name = name
        self.channel_id: int | None = None
        self.herdr_session: str | None = None  # what herdr reports
        self.session: str | None = None        # what we actually tail
        self.path: Path | None = None
        self.offset = 0
        # Prompts we injected from Discord, awaiting their transcript echo,
        # so they aren't posted back to the channel a second time.
        self.pending: deque[str] = deque(maxlen=20)
        # (text, deadline) pairs driving the wrong-file detection above.
        self.awaiting: list[tuple[str, float]] = []

    def attach(self, session: str, cwd: str):
        """Point at a (possibly new) Claude session. Tailing starts at the end
        of the file: history from before the wiring stays out of the channel."""
        if session == self.herdr_session:
            return  # herdr unchanged; keep whatever rebind() resolved
        self.herdr_session = session
        self.session = session
        path = transcript_path(session, cwd)
        if not path.exists():
            # A /cd mid-session moves cwd but not the transcript directory.
            hits = sorted(PROJECTS_DIR.glob(f"*/{session}.jsonl"))
            if hits:
                path = hits[0]
        self.path = path
        self.offset = path.stat().st_size if path.exists() else 0
        log.info("%s -> session %s (%s)", self.name, session, path)

    def note_injected(self, text: str):
        self.pending.append(text)
        self.awaiting.append((text, time.monotonic() + self.ECHO_DEADLINE))

    def saw_echo(self, text: str):
        self.awaiting = [(t, d) for t, d in self.awaiting if t != text]

    def rebind_if_lost(self) -> bool:
        """When an echo deadline expires, search recent transcripts for the
        injected text and re-attach to the file that actually received it."""
        now = time.monotonic()
        expired = [t for t, d in self.awaiting if d < now]
        if not expired:
            return False
        self.awaiting = [(t, d) for t, d in self.awaiting if d >= now]

        recent = [
            p for p in PROJECTS_DIR.glob("*/*.jsonl")
            if p != self.path and now_mtime_within(p, 300)
        ]
        recent.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        for text in expired:
            # The transcript stores the text JSON-encoded; match either form.
            needles = [json.dumps(text)[1:-1].encode(), text.encode()]
            for path in recent[:15]:
                raw = tail_bytes(path, 512 * 1024)
                pos = -1
                for needle in needles:
                    pos = raw.rfind(needle)
                    if pos != -1:
                        break
                if pos == -1:
                    continue
                line_end = raw.find(b"\n", pos)
                base = path.stat().st_size - len(raw)
                self.path = path
                self.session = path.stem
                self.offset = base + (line_end + 1 if line_end != -1 else len(raw))
                if text in self.pending:
                    self.pending.remove(text)
                log.warning("%s rebound to live transcript %s", self.name, path)
                return True
            log.warning("%s: injected prompt never surfaced in any transcript", self.name)
        return False

    def read_new(self) -> list[str]:
        """Complete new lines since the last read. A partially-written line is
        left for the next poll rather than parsed as garbage."""
        if self.path is None:
            return []
        if not self.path.exists():
            self.offset = 0
            return []

        size = self.path.stat().st_size
        if size < self.offset:  # session restarted / file replaced
            self.offset = 0
        if size == self.offset:
            return []

        with open(self.path, "rb") as f:
            f.seek(self.offset)
            chunk = f.read(size - self.offset)

        keep = chunk.rfind(b"\n") + 1
        if keep == 0:
            return []
        self.offset += keep
        return chunk[:keep].decode("utf-8", errors="replace").splitlines()


class Ratatoskr(discord.Client):
    def __init__(self, cfg: dict):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.cfg = cfg
        self.guild_id: int = cfg["discord"]["guild_id"]
        self.owner_id: int = cfg["discord"]["owner_id"]
        self.category_name: str = cfg["bridge"].get("category", "agents")
        self.exclude: set[str] = set(cfg["bridge"].get("exclude", []))
        self.links: dict[str, AgentLink] = {}  # agent name -> link

    # -- lifecycle ---------------------------------------------------------- #

    async def setup_hook(self):
        asyncio.create_task(self.sync_loop())
        asyncio.create_task(self.tail_loop())

    async def on_ready(self):
        log.info("logged in as %s", self.user)

    # -- channel <-> agent sync -------------------------------------------- #

    async def sync_loop(self):
        await self.wait_until_ready()
        interval = self.cfg["bridge"].get("sync_agents", 30.0)
        while not self.is_closed():
            try:
                await self.sync_agents()
            except Exception:
                log.exception("agent sync failed")
            await asyncio.sleep(interval)

    async def sync_agents(self):
        guild = self.get_guild(self.guild_id)
        if guild is None:
            log.warning("guild %s not visible - is the bot invited?", self.guild_id)
            return

        agents = await asyncio.to_thread(herdr_agents)

        category = discord.utils.get(guild.categories, name=self.category_name)
        if category is None:
            category = await guild.create_category(self.category_name)

        for name, info in agents.items():
            if name in self.exclude:
                continue

            link = self.links.setdefault(name, AgentLink(name))

            if link.channel_id is None:
                channel = discord.utils.get(guild.text_channels, name=channel_name(name))
                if channel is None:
                    channel = await guild.create_text_channel(
                        channel_name(name), category=category,
                        topic=f"herdr agent '{name}' on this machine",
                    )
                    log.info("created #%s", channel.name)
                link.channel_id = channel.id

            link.attach(info["session"], info["cwd"])

        # An agent that disappeared from herdr keeps its channel (it holds the
        # history) but stops being tailed.
        for name in list(self.links):
            if name not in agents:
                log.info("agent %s gone; channel kept, tailing stopped", name)
                del self.links[name]

    # -- Discord -> herdr --------------------------------------------------- #

    async def on_message(self, message: discord.Message):
        if message.author.id != self.owner_id:
            return

        link = next(
            (l for l in self.links.values() if l.channel_id == message.channel.id),
            None,
        )
        if link is None or not message.content.strip():
            return

        text = message.content.strip()
        link.note_injected(text)
        err = await asyncio.to_thread(herdr_prompt, link.name, text)
        if err:
            link.pending.remove(text)
            link.saw_echo(text)
            await message.channel.send(f"⚠️ could not reach `{link.name}`: {err}")

    # -- herdr -> Discord --------------------------------------------------- #

    async def tail_loop(self):
        await self.wait_until_ready()
        interval = self.cfg["bridge"].get("poll_transcripts", 2.0)
        while not self.is_closed():
            for link in list(self.links.values()):
                try:
                    await self.flush_link(link)
                except Exception:
                    log.exception("tail failed for %s", link.name)
            await asyncio.sleep(interval)

    async def flush_link(self, link: AgentLink):
        rebound = await asyncio.to_thread(link.rebind_if_lost)
        lines = await asyncio.to_thread(link.read_new)
        if rebound and link.channel_id is not None:
            ch = self.get_channel(link.channel_id)
            if ch:
                await ch.send("🔁 re-attached to this agent's live transcript")
        if not lines or link.channel_id is None:
            return
        channel = self.get_channel(link.channel_id)
        if channel is None:
            return

        for role, text in turns_from_lines(lines):
            if role == "user":
                # Our own injected prompt echoing back - already on screen.
                # Mid-turn injections arrive wrapped in harness text, so
                # containment counts as a match too.
                mine = next((p for p in link.pending if p == text or p in text), None)
                if mine is not None:
                    link.pending.remove(mine)
                    link.saw_echo(mine)
                    continue
                await send_chunked(channel, text, prefix="💻 ")
            else:
                await send_chunked(channel, text)


def channel_name(agent: str) -> str:
    """Discord channel names: lowercase, digits, dashes."""
    return "".join(c if c.isalnum() else "-" for c in agent.lower()).strip("-")


def now_mtime_within(path: Path, seconds: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime <= seconds
    except OSError:
        return False


def tail_bytes(path: Path, limit: int) -> bytes:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - limit))
            return f.read()
    except OSError:
        return b""


async def send_chunked(channel, text: str, prefix: str = ""):
    body = prefix + text
    for i in range(0, len(body), DISCORD_LIMIT):
        await channel.send(body[i:i + DISCORD_LIMIT])


# --------------------------------------------------------------------------- #

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if not CONFIG_PATH.exists():
        sys.exit(f"no config at {CONFIG_PATH} - copy config.example.toml there")
    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)

    Ratatoskr(cfg).run(cfg["discord"]["token"], log_handler=None)


if __name__ == "__main__":
    main()
