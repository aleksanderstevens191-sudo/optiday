#!/usr/bin/env bash
# Turn a fresh Ubuntu/Debian server you control into OptiDay's private AI brain.
#
#   scp deploy/vm/setup-vm.sh you@your-server:   &&   ssh you@your-server 'bash setup-vm.sh qwen2.5:14b'
#
# Ollama stays bound to 127.0.0.1 on the server, so it is never reachable from the
# internet. Your Mac talks to it through an encrypted SSH tunnel (optiday connect-vm).
# Prompts are processed in memory; Ollama does not store them.
set -euo pipefail
MODEL="${1:-qwen2.5:14b}"

echo "== Installing Ollama"
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi

echo "== Locking Ollama to localhost only"
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=127.0.0.1:11434"\nEnvironment="OLLAMA_KEEP_ALIVE=30m"\n' \
  | sudo tee /etc/systemd/system/ollama.service.d/optiday-private.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
sudo systemctl restart ollama
for _ in $(seq 1 30); do curl -fs http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done

echo "== Downloading $MODEL"
ollama pull "$MODEL"

echo "== Firewall: allow SSH only"
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow OpenSSH >/dev/null
  sudo ufw --force enable >/dev/null
fi

cat <<EOF

Server ready. On your Mac, run:

  optiday connect-vm $(whoami)@<this-server-ip> --model $MODEL
  optiday doctor

(Your Mac's SSH key must be able to log in here without a password:
  ssh-copy-id $(whoami)@<this-server-ip>)
EOF
