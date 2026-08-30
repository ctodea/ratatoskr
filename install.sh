#!/usr/bin/env bash
# Idempotent setup: venv, dependency, config skeleton, systemd user unit.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[[ -d $here/.venv ]] || python3 -m venv "$here/.venv"
"$here/.venv/bin/pip" -q install discord.py

mkdir -p ~/.config/saga2
if [[ ! -f ~/.config/saga2/config.toml ]]; then
  cp "$here/config.example.toml" ~/.config/saga2/config.toml
  echo ">> fill in token/guild_id/owner_id in ~/.config/saga2/config.toml"
fi

mkdir -p ~/.config/systemd/user
ln -sfn "$here/saga2.service" ~/.config/systemd/user/saga2.service
systemctl --user daemon-reload
echo ">> start with: systemctl --user enable --now saga2"
