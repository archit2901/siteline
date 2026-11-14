#!/usr/bin/env bash
# One-time setup of an Ubuntu 24.04 EC2 box for Siteline (run as the ubuntu user, or paste as EC2 user data).
# Installs Docker + the compose plugin and clones the repo. Then copy .env and data/ in (see infra/README.md) and run:
#   cd ~/siteline && SITE_ADDRESS=<ip-with-dashes>.sslip.io docker compose -f docker-compose.prod.yml up -d --build
set -euo pipefail

REPO=${REPO:-https://github.com/archit2901/siteline.git}
BRANCH=${BRANCH:-main}
HOME_DIR=${HOME_DIR:-/home/ubuntu}

sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ubuntu

if [ ! -d "$HOME_DIR/siteline" ]; then
  sudo -u ubuntu git clone --branch "$BRANCH" "$REPO" "$HOME_DIR/siteline"
fi
sudo -u ubuntu mkdir -p "$HOME_DIR/siteline/data" "$HOME_DIR/siteline/runs"
echo "Bootstrap done. Copy .env and data/ into $HOME_DIR/siteline, then start the stack (infra/README.md)."
