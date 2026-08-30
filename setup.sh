#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  DEEPSEEK-AGENT — one-command setup
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
║  D E E P S E E K   A G E N T   —  s e t u p  ║
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
    pkg install -y python yaml nodejs 2>/dev/null || true
    ok "Termux packages ready (nodejs = DeepSeek PoW solver)"
elif command -v apt >/dev/null 2>&1; then          # Debian/Ubuntu
    sudo apt update -qq 2>/dev/null || true
    sudo apt install -y python3-pip python3-yaml nodejs 2>/dev/null || true
    ok "apt packages ready (nodejs = DeepSeek PoW solver)"
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
python3 -c "import requests"  2>/dev/null && ok "requests (DeepSeek HTTP)" || warn "requests missing — DeepSeek chat won't work (install requests)"
python3 -c "import numpy"     2>/dev/null && ok "numpy (fast RAG search)" || warn "numpy missing (RAG falls back to slow mode)"
python3 -c "import prompt_toolkit" 2>/dev/null && ok "prompt_toolkit (nice input)" || warn "prompt_toolkit missing (basic input)"
command -v node >/dev/null 2>&1 && ok "nodejs (DeepSeek PoW solver)" || warn "nodejs missing — DeepSeek login/chat won't work (install nodejs)"

# ── 4. Fresh install: purge keys/config from an OLDER install ──
if [ "$UPDATE_MODE" = "1" ]; then
    step "Update mode — keeping your keys and config"
    ok "Keys/config left untouched"
else
    step "Checking for data from an older install"
    if [ -d .deepseek ] || [ -f .env ] || [ -d keys ]; then
        rm -rf .deepseek .env keys
        ok "Old keys/config removed — a fresh wizard will run"
    else
        ok "No previous config — completely fresh install"
    fi
fi

# ── 5. Install the `deepseek` launcher command ─────────────────
step "Installing the 'deepseek' command"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# 5a. Remove ANY existing 'nexus'/'deepseek' command (binary, wrapper, alias)
for d in "${PREFIX:-/nonexistent}/bin" /usr/local/bin /usr/bin "$HOME/.local/bin" "$HOME/bin"; do
    [ -n "$d" ] && [ -f "$d/nexus" ] && rm -f "$d/nexus" && ok "removed old 'nexus' at $d/nexus"
    [ -n "$d" ] && [ -f "$d/deepseek" ] && rm -f "$d/deepseek" && ok "removed old 'deepseek' at $d/deepseek"
done
for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile" "$HOME/.bash_profile"; do
    if [ -f "$rc" ] && grep -q "alias nexus=" "$rc" 2>/dev/null; then
        sed -i '\|alias nexus=|d' "$rc"
        ok "removed old 'alias nexus=…' from $rc (restart your shell)"
    fi
    if [ -f "$rc" ] && grep -q "alias deepseek=" "$rc" 2>/dev/null; then
        sed -i '\|alias deepseek=|d' "$rc"
        ok "removed old 'alias deepseek=…' from $rc (restart your shell)"
    fi
done

# 5b. Pick an install dir that is on PATH (or can be added)
BIN_DIR=""
if [ -n "${PREFIX:-}" ] && [ -d "$PREFIX/bin" ] && [ -w "$PREFIX/bin" ]; then
    BIN_DIR="$PREFIX/bin"                                   # Termux
elif case ":$PATH:" in *":$HOME/.local/bin:"*) true;; *) false;; esac; then
    BIN_DIR="$HOME/.local/bin"                              # Linux/macOS, already on PATH
elif case ":$PATH:" in *":$HOME/bin:"*) true;; *) false;; esac; then
    BIN_DIR="$HOME/bin"
elif [ -w /usr/local/bin ]; then
    BIN_DIR="/usr/local/bin"
else
    mkdir -p "$HOME/.local/bin" && BIN_DIR="$HOME/.local/bin"
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        [ -f "$rc" ] || continue
        grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$rc" || {
            printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
        }
    done
    warn "$HOME/.local/bin added to PATH via your shell rc — restart the shell once"
fi

# 5c. Write the wrapper
printf '#!/usr/bin/env bash\nexec python3 "%s/deepseek.py" "$@"\n' "$REPO_DIR" > "$BIN_DIR/deepseek"
chmod +x "$BIN_DIR/deepseek"
ok "'deepseek' command installed → $BIN_DIR/deepseek"

# ── 6. Sanity: launcher import test ────────────────────────────
step "Self-test"
python3 -c "from deepseek_agent.cli.app import main" 2>/dev/null \
    && ok "Agent core loaded" \
    || die "core import failed — please open an issue"

# ── 7. Launch instructions ─────────────────────────────────────
printf "\n${BOLD}${GREEN}══════════════ SETUP COMPLETE ══════════════${R}\n"
printf """
${BOLD}To launch the agent, just type:${R}

    ${CYAN}deepseek${R}

(and ${CYAN}python3 deepseek.py${R} still works too)

On the first run the agent opens a login wizard and asks for your
${BOLD}DeepSeek${R} account email + password (the official chat.deepseek.com
account). The token is stored on your device and auto-refreshes.
Once logged in, you're ready to go. Enjoy! 🚀
"""
