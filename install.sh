#!/usr/bin/env bash
# Nexus Agent installer — Termux (Android) and Linux/macOS
set -e

CYAN='\033[96m'; GREEN='\033[92m'; YELLOW='\033[93m'; RED='\033[91m'; NC='\033[0m'
say()  { echo -e "${CYAN}▸${NC} $1"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
die()  { echo -e "${RED}✕${NC} $1"; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo -e "${CYAN}"
echo " ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗"
echo " ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝"
echo " ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗"
echo " ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║"
echo " ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║"
echo " ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝"
echo -e "${NC} autonomous multi-agent CLI\n"

# ---------- platform ----------
IS_TERMUX=0
[ -n "${PREFIX:-}" ] && [[ "$PREFIX" == *com.termux* ]] && IS_TERMUX=1
if [ $IS_TERMUX -eq 1 ]; then
  say "Termux detected (Android)"
else
  say "Linux/macOS detected"
fi

# ---------- python ----------
command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 \
  || die "Python not found. Termux: pkg install python"
PY=$(command -v python3 || command -v python)
PYV=$($PY -c 'import sys;print("%d.%d"%sys.version_info[:2])')
ok "Python $PYV at $PY"
$PY -c 'import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)' \
  || die "Python 3.9+ required (found $PYV)"

# ---------- system packages ----------
if [ $IS_TERMUX -eq 1 ]; then
  say "Installing Termux packages…"
  pkg install -y python git curl >/dev/null 2>&1 || warn "some pkg installs failed"
  if ! $PY -c 'import numpy' 2>/dev/null; then
    say "Installing numpy via pkg (faster than pip on ARM)…"
    pkg install -y python-numpy >/dev/null 2>&1 || warn "numpy unavailable — RAG will use keyword search"
  fi
  command -v termux-wake-lock >/dev/null 2>&1 || {
    say "Installing termux-api (notifications, wake-lock)…"
    pkg install -y termux-api >/dev/null 2>&1 || true
  }
fi

# ---------- python deps ----------
say "Installing Python dependencies…"
$PY -m pip install --upgrade pip >/dev/null 2>&1 || true
if $PY -m pip install -q -r requirements.txt 2>/dev/null; then
  ok "dependencies installed"
else
  warn "full install failed — installing the core only"
  $PY -m pip install -q rich PyYAML || die "could not install rich/PyYAML"
fi

for mod in rich yaml; do
  $PY -c "import $mod" 2>/dev/null && ok "$mod ready" || die "$mod missing"
done
$PY -c "import numpy" 2>/dev/null && ok "numpy ready (fast vector search)" \
  || warn "no numpy — RAG falls back to keyword search"

# ---------- directories ----------
mkdir -p workspace .nexus/vectors skills logs
ok "directories created"

# ---------- env ----------
if [ ! -f .env ]; then
  cat > .env <<'ENVEOF'
# Nexus Agent — API keys
# Add MULTIPLE keys: when one hits a rate limit the agent switches automatically.
MISTRAL_API_KEY=
MISTRAL_API_KEY_2=
MISTRAL_API_KEY_3=

# Optional extra providers (enable them in config/config.yaml first)
# OPENAI_API_KEY=
# GROQ_API_KEY=

# NEXUS_APPROVAL_MODE=smart     # smart | always | never
# NEXUS_THEME=cyber             # cyber | matrix | mono
# NEXUS_DEBUG=1
ENVEOF
  chmod 600 .env
  ok ".env created"
else
  ok ".env already exists"
fi
[ ! -f .env.example ] && cp .env .env.example && sed -i 's/=.*/=/' .env.example 2>/dev/null || true

# ---------- gitignore ----------
if [ ! -f .gitignore ]; then
  cat > .gitignore <<'GITEOF'
.env
.nexus/
__pycache__/
*.pyc
workspace/
logs/
config/config.local.yaml
.pytest_cache/
GITEOF
  ok ".gitignore created"
fi

chmod +x nexus.py 2>/dev/null || true

# ---------- launcher ----------
BIN=""
if [ $IS_TERMUX -eq 1 ]; then BIN="$PREFIX/bin"
elif [ -d "$HOME/.local/bin" ]; then BIN="$HOME/.local/bin"
fi
if [ -n "$BIN" ] && [ -w "$BIN" ]; then
  cat > "$BIN/nexus" <<LAUNCHEOF
#!/usr/bin/env bash
cd "$ROOT" && exec $PY nexus.py "\$@"
LAUNCHEOF
  chmod +x "$BIN/nexus"
  ok "launcher installed — run 'nexus' from anywhere"
fi

# ---------- self-test ----------
say "Running self-test…"
if $PY -m pytest tests/test_core.py -q 2>/dev/null | tail -1; then
  ok "tests passed"
else
  warn "pytest not installed or some tests failed (pip install pytest)"
fi

# ---------- done ----------
echo
ok "Installation complete"
echo
echo -e "${CYAN}Next steps:${NC}"
echo "  1. Add your API keys:   nano .env"
echo "     (get a key at https://console.mistral.ai)"
echo "  2. Start the agent:     $PY nexus.py"
[ -n "$BIN" ] && echo "                     or:  nexus"
echo "  3. Try:                 nexus \"build me a todo web app\""
echo
if [ $IS_TERMUX -eq 1 ]; then
  echo -e "${YELLOW}Termux tips:${NC}"
  echo "  • termux-wake-lock            keeps long runs alive"
  echo "  • Settings → Battery → Termux → Unrestricted"
  echo "  • termux-setup-storage        to reach /sdcard"
fi
