#!/usr/bin/env bash
# Idempotent setup: venv, dependency, config skeleton, systemd user unit.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[[ -d $here/.venv ]] || python3 -m venv "$here/.venv"
"$here/.venv/bin/pip" -q install discord.py

mkdir -p ~/.config/ratatoskr
if [[ ! -f ~/.config/ratatoskr/config.toml ]]; then
  cp "$here/config.example.toml" ~/.config/ratatoskr/config.toml
  echo ">> fill in token/guild_id/owner_id in ~/.config/ratatoskr/config.toml"
fi

mkdir -p ~/.config/systemd/user
ln -sfn "$here/ratatoskr.service" ~/.config/systemd/user/ratatoskr.service
systemctl --user daemon-reload
echo ">> start with: systemctl --user enable --now ratatoskr"
