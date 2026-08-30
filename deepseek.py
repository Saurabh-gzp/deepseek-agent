#!/usr/bin/env python3
"""DeepSeek-Agent launcher — `python deepseek.py` or `./deepseek.py "goal"`."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_dotenv()
    try:
        from deepseek_agent.cli.app import main as cli_main
    except ImportError as e:
        print(f"Missing dependency: {e}\nRun:  pip install -r requirements.txt")
        return 1
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
