#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  NEXUS AGENT — one-command setup
#  Usage:   bash setup.sh          (fresh install: deps + key purge)
#           bash setup.sh --update (update: deps only, KEEP your keys)
# ═══════════════════════════════════════════════════════════════
set -u
cd "$(dirname "$0")"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; CYAN=$'\033[36m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; R=$'\033[0m'
step() { printf "\n${CYAN}▸ %s${R}\n" "$1"; }
ok()   { printf "  ${GREEN}✓${R} %s\n" "$1"; }
warn() { printf "  ${YELLOW}⚠${R} %s\n" "$1"; }
die()  { printf "  ${RED}✗ %s${R}\n" "$1"; exit 1; }

UPDATE_MODE=0
[ "${1:-}" = "--update" ] && UPDATE_MODE=1

printf "\n${BOLD}╔══════════════════════════════════════════╗
║   N E X U S   A G E N T   —   s e t u p   ║
╚══════════════════════════════════════════╝${R}\n"

# ── 1. Python check ────────────────────────────────────────────
step "Python check"
if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 not found — installing..."
    if command -v pkg >/dev/null 2>&1; then      # Termux
        pkg install -y python || die "pkg install python failed"
    elif command -v apt >/dev/null 2>&1; then
        sudo apt update && sudo apt install -y python3 python3-pip || die "apt install failed"
    else
        die "please install python3 first (https://python.org)"
    fi
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
ok "Python $PYVER"

# ── 2. System deps (platform-aware) ────────────────────────────
step "System packages"
if command -v pkg >/dev/null 2>&1; then           # Termux (Android)
    pkg install -y python yaml 2>/dev/null || true
    ok "Termux packages ready"
elif command -v apt >/dev/null 2>&1; then          # Debian/Ubuntu
    sudo apt update -qq 2>/dev/null || true
    sudo apt install -y python3-pip python3-yaml 2>/dev/null || true
    ok "apt packages ready"
elif command -v dnf >/dev/null 2>&1; then          # Fedora
    sudo dnf install -y python3-pip python3-pyyaml 2>/dev/null || true
    ok "dnf packages ready"
elif command -v pacman >/dev/null 2>&1; then       # Arch
    sudo pacman -S --noconfirm python-pip python-yaml 2>/dev/null || true
    ok "pacman packages ready"
else
    warn "unknown package manager — continuing with pip"
fi

# ── 3. Python dependencies ─────────────────────────────────────
step "Python dependencies (rich, PyYAML, numpy, prompt_toolkit)"
PIP_FLAGS=""
python3 -c "import rich, yaml" 2>/dev/null || PIP_FLAGS="--user"
pip3 install $PIP_FLAGS -r requirements.txt 2>/dev/null \
  || pip3 install $PIP_FLAGS --break-system-packages -r requirements.txt \
  || die "pip install failed — try manually: pip3 install -r requirements.txt"
python3 -c "import rich"      2>/dev/null && ok "rich"       || warn "rich missing (UI will be plain)"
python3 -c "import yaml"      2>/dev/null && ok "PyYAML"     || warn "PyYAML missing"
python3 -c "import numpy"     2>/dev/null && ok "numpy (fast RAG search)" || warn "numpy missing (RAG falls back to slow mode)"
python3 -c "import prompt_toolkit" 2>/dev/null && ok "prompt_toolkit (nice input)" || warn "prompt_toolkit missing (basic input)"

# ── 4. Fresh install: purge keys/config from an OLDER install ──
if [ "$UPDATE_MODE" = "1" ]; then
    step "Update mode — keeping your keys and config"
    ok "Keys/config left untouched"
else
    step "Checking for data from an older install"
    if [ -d .nexus ] || [ -f .env ] || [ -d keys ]; then
        rm -rf .nexus .env keys
        ok "Old keys/config removed — a fresh wizard will run"
    else
        ok "No previous config — completely fresh install"
    fi
fi

# ── 5. Sanity: launcher import test ────────────────────────────
step "Self-test"
python3 -c "from nexus.cli.app import main" 2>/dev/null \
    && ok "Agent core loaded" \
    || die "core import failed — please open an issue"

# ── 6. Launch instructions ─────────────────────────────────────
printf "\n${BOLD}${GREEN}══════════════ SETUP COMPLETE ══════════════${R}\n"
printf """
${BOLD}To launch the agent, type:${R}

    ${CYAN}python3 nexus.py${R}

On the first run the agent opens an API-key wizard itself and
asks for your ${BOLD}Mistral AI${R} key (free: console.mistral.ai).
Once the key is in, you're ready to go. Enjoy! 🚀
"""
