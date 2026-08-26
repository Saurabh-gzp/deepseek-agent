#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  NEXUS AGENT — one-command setup
#  Usage:   bash setup.sh
#  Flow:    deps install → purani keys/config purge → launch help
# ═══════════════════════════════════════════════════════════════
set -u
cd "$(dirname "$0")"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; CYAN=$'\033[36m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; R=$'\033[0m'
step() { printf "\n${CYAN}▸ %s${R}\n" "$1"; }
ok()   { printf "  ${GREEN}✓${R} %s\n" "$1"; }
warn() { printf "  ${YELLOW}⚠${R} %s\n" "$1"; }
die()  { printf "  ${RED}✗ %s${R}\n" "$1"; exit 1; }

printf "\n${BOLD}╔══════════════════════════════════════════╗
║   N E X U S   A G E N T   —   s e t u p   ║
╚══════════════════════════════════════════╝${R}\n"

# ── 1. Python check ────────────────────────────────────────────
step "Python check"
if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 nahi mila — installing..."
    if command -v pkg >/dev/null 2>&1; then      # Termux
        pkg install -y python || die "pkg install python failed"
    elif command -v apt >/dev/null 2>&1; then
        sudo apt update && sudo apt install -y python3 python3-pip || die "apt install failed"
    else
        die "python3 install karo pehle (https://python.org)"
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
    warn "unknown package manager — pip se aage badh rahe hain"
fi

# ── 3. Python dependencies ─────────────────────────────────────
step "Python dependencies (rich, PyYAML, numpy, prompt_toolkit)"
PIP_FLAGS=""
python3 -c "import rich, yaml" 2>/dev/null || PIP_FLAGS="--user"
pip3 install $PIP_FLAGS -r requirements.txt 2>/dev/null \
  || pip3 install $PIP_FLAGS --break-system-packages -r requirements.txt \
  || die "pip install fail — manually: pip3 install -r requirements.txt"
python3 -c "import rich"      2>/dev/null && ok "rich"       || warn "rich missing (UI plain dikhega)"
python3 -c "import yaml"      2>/dev/null && ok "PyYAML"     || warn "PyYAML missing"
python3 -c "import numpy"     2>/dev/null && ok "numpy (RAG fast-search)" || warn "numpy missing (RAG slow fallback)"
python3 -c "import prompt_toolkit" 2>/dev/null && ok "prompt_toolkit (nice input)" || warn "prompt_toolkit missing (basic input)"

# ── 4. Purge stale keys/config from an OLDER install ───────────
step "Purani install ka data check"
if [ -d .nexus ] || [ -f .env ] || [ -d keys ]; then
    rm -rf .nexus .env keys
    ok "Purani keys/config delete ho gayi — fresh wizard chalega"
else
    ok "Koi purani config nahi — bilkul fresh install"
fi

# ── 5. Sanity: launcher import test ────────────────────────────
step "Self-test"
python3 -c "from nexus.cli.app import main" 2>/dev/null \
    && ok "Agent core load ho gaya" \
    || die "core import fail — issue kholo: https://github.com/issues"

# ── 6. Launch instructions ─────────────────────────────────────
printf "\n${BOLD}${GREEN}══════════════ SETUP COMPLETE ══════════════${R}\n"
printf """
${BOLD}Agent launch karne ke liye type karo:${R}

    ${CYAN}python3 nexus.py${R}

Pehle run pe agent khud API key wizard kholega —
apni ${BOLD}Mistral AI${R} key maangega (free: console.mistral.ai).
Key daalte hi agent ready hai. Enjoy! 🚀
"""
