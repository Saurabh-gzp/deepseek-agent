"""Slash-command autocomplete — `/` type karo aur saare commands hint ke saath
dikhne lagte hain (bade agents jaisa). prompt_toolkit optional hai; nahi mile
to rich fallback chalega.

Features:
  * "/"                                  → poora command menu + description
  * "/sk"                                → matching commands (/skills, /skill)
  * "/skill web_development/<tab>"       → skill ids complete hote hain
  * "/agent coder <tab>"                 → agent names
  * "/mode smart <tab>"                  → modes
  * arrow-up/down history (persist in .nexus/history)
"""
from __future__ import annotations

from typing import Dict, List, Optional

try:                                       # optional dep — graceful fallback
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    _HAS_PT = True
except ImportError:                        # pragma: no cover
    _HAS_PT = False


COMMANDS: Dict[str, str] = {
    "/help": "saare commands dikhao",
    "/key": "🔑 key manager menu (add/delete/test keys)",
    "/keys": "key health table (quick view)",
    "/status": "usage stats — calls, tokens, models",
    "/skills": "skills list/search",
    "/skill": "ek skill ka poora playbook dekho",
    "/rag": "knowledge base stats",
    "/index": "file/folder ko RAG me index karo",
    "/forget-index": "RAG index clear",
    "/memory": "memory stats + facts",
    "/remember": "preference save karo (k=v)",
    "/sessions": "purane sessions list",
    "/resume": "session resume karo",
    "/tools": "tools + kaunse agent ko kya mila",
    "/projects": "workspace ke project folders",
    "/plan": "sirf plan banao, execute nahi",
    "/auto": "full autonomous orchestration force karo",
    "/agent": "ek specific agent solo chalao",
    "/cd": "workspace change karo",
    "/mode": "approval mode (smart/always/never)",
    "/verbose": "step-by-step output toggle",
    "/clear": "screen clear",
    "/exit": "band karo",
}


class NexusCompleter(Completer if _HAS_PT else object):
    def __init__(self, arg_hints: Optional[Dict[str, List[str]]] = None):
        self.arg_hints: Dict[str, List[str]] = arg_hints or {}

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        parts = text.split(" ", 1)
        if len(parts) == 1:                              # command name phase
            for cmd, desc in COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text),
                                     display_meta=desc)
        else:                                            # argument phase
            opts = self.arg_hints.get(parts[0]) or []
            frag = parts[1]
            for o in opts:
                if o.startswith(frag):
                    yield Completion(o, start_position=-len(frag))


def make_prompt_session(completer, history_path=None):
    """prompt_toolkit session — menu-style completions + persistent history."""
    if not _HAS_PT:
        return None
    hist = FileHistory(str(history_path)) if history_path else None
    return PromptSession(completer=completer, history=hist,
                         complete_while_typing=True,
                         reserve_space_for_menu=8)
