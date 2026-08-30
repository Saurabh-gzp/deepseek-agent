"""Reference resolution + deterministic environment intents (v1.10.4).

Two jobs:

1. `is_environment_ref(text)` — does the user point at something that only the
   RUNNING environment can answer? Those questions must NEVER be answered from
   the model's world knowledge (live bug: "bro workspace me kya hai" ->
   "Workspace ek terminal emulator hai...").

2. `resolve(goal, ctx)` — LEVEL 0/1 of the tool-escalation model: questions
   whose answer is one deterministic read-only tool call get executed HERE,
   with 0 LLM calls, instead of becoming router → supervisor → worker → critic
   → synthesis (live cost: 15.9s + 16,180 tokens for a directory listing).

Escalation ladder implemented by the engine:
   L0 deterministic runtime state (this module)   -> 0 LLM calls
   L1 single read-only tool        (this module)   -> 0-1 LLM calls
   L2 grounded follow-up on evidence (reflect_*)    -> 1 cheap LLM call
   L3 single agent / L4 full DAG   (existing path)
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ----------------------------------------------------------------------
# 1. ENVIRONMENT-REFERENCE DETECTION
# ----------------------------------------------------------------------
# System-resident entities: things that exist only in THIS runtime state.
ENTITY = (
    r"(?:workspace|work\s*space|current\s+directory|cwd|pwd|"
    r"current\s+(?:project|repo|repository|folder|dir|directory|session|task|server)|"
    r"this\s+(?:project|repo|repository|folder|dir|directory|file)|"
    r"my\s+(?:project|files?|folder|repo|code)|apna?\s+(?:project|folder|files?)|"
    r"installed\s+packages?|running\s+server|current\s+server|"
    r"devices?|storage|battery|ram|memory|sessions?|ports?|"
    r"[a-z0-9_.\-/]+\.(?:py|md|json|html|css|js|ts|txt|yaml|yml|db|png|jpg)|"
    r"file(?:s)?\s+(?:here|yahan)|yahan|here|isme|iss?\s+(?:folder|project|repo|file|code)|"
    r"us\s+(?:folder|project|repo|file)|isi\s+(?:folder|project|repo|file))"
)

STATE_VERB = (
    r"(?:kya\s+(?:hai|hain|pada|para|cha)?rh|what(?:'s|\s+is|\s+are)|list(?:ing)?|show|dekh|"
    r"dikha|check|exist|kitne|how\s+many|count|status|chal\s+raha|running|open|free|used|"
    r"kaunsa|which|where|kahan|find|verify|kha\s+hai)"
)

ENV_QUESTION = re.compile(rf"\b{ENTITY}\b[\s\S]{{0,40}}?\b{STATE_VERB}\b|"
                          rf"\b{STATE_VERB}\b[\s\S]{{0,40}}?\b{ENTITY}\b", re.I)

# Explicit forbidden shape: an environment question the router answered directly.
ENV_GROUND_REQUIRED = ENV_QUESTION

# ----------------------------------------------------------------------
# 2. FOLLOW-UP / ANAPHORA RESOLUTION
# ----------------------------------------------------------------------
PRONOUN = re.compile(
    r"\b(?:isse|ism(e|en)|isko|ikse|iska|isk|usme|use|isko|uska|woh|yah|yeh|ye|voh|"
    r"is|it|this|that|these|those|them|the\s+same|same|"
    r"previous(?:ly)?|earlier|above|just\s+now|abhi|pichl[ao]?|piche|"
    r"jo\s+(?:dekha|mila|banaya|hua|list|files?)|us\s+project|isi|"
    r"aapne\s+bana(?:ya)?|jo\s+bana(?:ya)?)\b", re.I)

REFLECT = re.compile(
    r"\b(?:kya\s+(?:seekh|sikha|sikh)|what\s+(?:did\s+you\s+learn|have\s+you\s+learn)|"
    r"seekh|learn(?:ed)?|samajh|kya\s+important|important\s+hai|kya\s+notice|"
    r"summar(?:y|ize|ise)|short\s+me|bat(?:ao|a)\s+kya|kya\s+ ?mila|what\s+did\s+\w+\s+find|"
    r"categor(?:y|ize|ise)|group\s+kar|divide\s+kar|classify)\b", re.I)

FOLLOWUP_MARK = re.compile(
    r"^\s*(?:aur|fir|phir|also|and|now|abaad|isake\s+baad|usake\s+baad)[,.\s]", re.I)


def has_reference(text: str) -> bool:
    t = text or ""
    return bool(PRONOUN.search(t) or FOLLOWUP_MARK.search(t) or REFLECT.search(t))


# Short confirmations of runtime state: "ab chal raha hai?" / "port khula hai?" /
# "server up hai?". Live bug: the router answered "Haan, sab theek hai! Tu kaisa
# hai?" — grammatically a chat reply, factually a claim about a socket it never checked.
_STATE_NOUN = r"(?:server|port\s*\d*|site|app|api|connection|host|listener|process|service)"
_STATE_OK = r"(?:chal\s*\w*|up\b|live\b|running|khul\w*|bound|band|down|stop\w*|dead|alive|work\w*|\bok\b|\bhit\b|\bready\b|\bactive\b)"
STATE_CONFIRM = re.compile(
    rf"\b(?:{_STATE_NOUN})\b[^?!.]{{0,40}}?\b(?:{_STATE_OK})\b[^?!.]{{0,18}}?(?:hai|hain|\?|$)"
    rf"|\b(?:{_STATE_OK})\b[^?!.]{{0,24}}?\b(?:server|port|site|app)\b[^?!.]{{0,14}}?(?:hai|hain)?\s*\??"
    rf"|\b(?:{_STATE_OK})\b[^?!.]{{0,20}}?\b(?:hai|hain)\s*\??\s*$"
    rf"|\b(?:ab|still|now|actually)\b[^?!.]{{0,24}}?\b(?:{_STATE_NOUN})\b[^?!.]{{0,20}}?(?:hai|hain|\?)", re.I)


def is_state_confirmation(text: str) -> bool:
    t = (text or "").strip()
    if not t or IMPERATIVE.search(t):
        return False
    return bool(len(t.split()) <= 10 and STATE_CONFIRM.search(t))


def needs_grounding(text: str) -> bool:
    """True when only live environment state can answer this (no LLM guessing)."""
    t = (text or "").strip()
    if not t or len(t.split()) > 26:
        return False
    return bool(ENV_GROUND_REQUIRED.search(t)) or is_state_confirmation(t)


LOCATIVE = re.compile(
    r"\b(?:isme|ismen|usme|usmen|isca|isca\s+project|is\s+folder|here|yahan|wahan|"
    r"is\s+(?:project|repo|folder|file|code)|us\s+(?:project|repo|folder|file|code)|"
    r"same\s+(?:project|folder)|isi\s+project)\b", re.I)

# resource nouns that only a filesystem/tool answer can settle
RESOURCE = re.compile(
    r"\b(auth|authentication|login|session|backend|frontend|api|db|database|"
    r"sqlite|postgres|mysql|test|tests|testing|readme|docs?|docker|dockerfile|"
    r"config|settings|env|\.env|router|routes?|server|index|styles?|css|js|json|"
    r"html|py|script|scripts|package\.json|node_modules|venv|git|gitignore|"
    r"model|models|migrations?|templates?|static|assets|images?|fonts?|"
    r"pyproject|requirements|license|license\.md|changelog)\b", re.I)

EXISTS_ASK = re.compile(r"\b(?:hai\b|\bhain\b|kahan|where|exist|present|mil|\bcheck\b|\bkya\b|"
                        r"\bany\b|\bthere\b|\bfile\b|\bfolder\b|ban\s*aaya|banaya)\b", re.I)


INVENTORY = re.compile(
    r"\b(?:koi|koyi|koi\s+bhi|any|some|\d+?\s*(?:file|script|docs?))\b"
    r"[^.?!\n]{0,24}\b(?:files?|scripts?|code|class|function|def\s+\w+|tests?|docs?|"
    r"readme|config|auth\w*|backend|frontend|api|model|route\w*|\w+\.\w{1,5})\b"
    r"|\b(?:files?|scripts?|routes?|models?|tests?)\b[^.?!\n]{0,18}"
    r"\b(?:hai\s*kya|exist|mila|present|bana|hua)\b", re.I)


def is_resource_lookup(text: str) -> bool:
    """"usme auth hai?" / "isme backend kahan hai?" — a question whose only valid
    source of truth is the filesystem, never model knowledge."""
    t = (text or "").strip()
    if not t or len(t.split()) > 14:
        return False
    if LOCATIVE.search(t) and RESOURCE.search(t) and EXISTS_ASK.search(t):
        return True
    # "koi login file hai kya" / "is there any test script" — an INVENTORY ask.
    # Needs an explicit indefinite + a file-ish resource word, so general
    # knowledge questions ("kya tum theek ho") never land here.
    return bool(INVENTORY.search(t) and RESOURCE.search(t))


def wanted_resource(text: str) -> str:
    m = RESOURCE.search(text or "")
    return m.group(0).lower() if m else ""


RECAP = re.compile(
    r"\b(?:session|conversation|ab\s+tak|itna\s+kuch|kya\s+kya|karwaya|kiya\s+tha\s+ya\s+kya|"
    r"what\s+did\s+(?:i|we)\s+(?:ask|do|have)|all\s+(?:the\s+)?(?:questions|tasks)|"
    r"summary\s+of\s+(?:this|our)|what\s+have\s+(?:we|i)\s+done|kitne\s+(?:questions|tasks|baat))\b", re.I)


def is_session_recap(text: str) -> bool:
    """"ab bata maine is session me kya kya karwaya" — needs the WHOLE ledger,
    not the 3-turn recency window (that window made the recap drop 6 of 9 turns)."""
    t = (text or "").strip()
    return bool(t and RECAP.search(t) and not _CONTINUATION.search(t))


def wants_reflection(text: str) -> bool:
    t = text or ""
    return bool(REFLECT.search(t) and (PRONOUN.search(t) or has_reference(t)))


# A follow-up is only safe to answer from the LEDGER when the user is asking
# ABOUT what we saw. 'aur isme contact form add kar' contains 'isme' but wants
# WORK — that must keep going to the supervisor, never get short-circuited.
_CONTINUATION = re.compile(
    r"\b(?:create|build|make|write|add|edit|modify|update|fix|refactor|delete|remove|rename|"
    r"move|copy|install|deploy|publish|send|host|serve|start|stop|restart|commit|push|"
    r"save|generate|run|execute|convert|migrate|implement|test|verify|check\s+the\s+code|"
    r"research|search|google|find\s+online|download|upload|khol|bana|daal|jod|nikal|kar\s*do|"
    r"laga|badha|hata|theek|sahi\s+kar|dikhao\s+code)\b", re.I)


def wants_observation(text: str) -> bool:
    """Follow-up that is talking ABOUT the previous observation."""
    t = (text or "").strip()
    if not t:
        return False
    if _CONTINUATION.search(t):
        return False
    return bool(REFLECT.search(t) or wants_reflection(t)
                or re.search(r"\b(?:important|interesting|kya\s+ mila|what\s+ did|"
                             r"summar|short\s+me|matlab|meaning|odd|odd\s+one|count|"
                             r"kitne|total)\b", t, re.I))


# ----------------------------------------------------------------------
# 3. DETERMINISTIC RESOLVER (L0 / L1)
# ----------------------------------------------------------------------
MAX_ITEMS = 14


def _trunc(items: List[str], note: str = "") -> Tuple[bool, str]:
    shown = items[:MAX_ITEMS]
    more = len(items) - len(shown)
    body = "\n".join(f"  • {x}" for x in shown)
    if more > 0:
        body += f"\n  … +{more} more"
    if note:
        body += f"\n{note}"
    return True, body


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n // 1024} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _list_workspace(ctx: Dict[str, Any]) -> Tuple[bool, str, List[str]]:
    """One deterministic list_dir at the ACTIVE workspace root (never a guess)."""
    root = ctx["workspace"]
    entries: List[Tuple[str, str, int]] = []
    for p in sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if p.name.startswith("__") or p.name in {".git", "node_modules", "__pycache__"}:
            continue
        hidden = p.name.startswith(".")
        kind = "dir" if p.is_dir() else "file"
        label = p.name + ("/" if p.is_dir() else "")
        if not hidden:
            try:
                label += f"  {_fmt_size(p.stat().st_size) if p.is_file() else str(len(list(p.iterdir()))) + ' item(s)'}"
            except OSError:
                pass
        entries.append((label, kind, 1 if hidden else 0))
    if not entries:
        return True, f"Workspace `{root}` is **empty** — nothing created here yet.", []
    visible = [e[0] for e in entries if e[2] == 0]
    hidden = [e[0] for e in entries if e[2] == 1]
    n_dir = sum(1 for e in entries if e[1] == "dir")
    n_file = len(entries) - n_dir
    ok, body = _trunc(visible)
    tail = f"\n  ({len(hidden)} hidden entries not listed)" if hidden else ""
    return ok, (f"Workspace `{root}` — {n_file} file(s), {n_dir} folder(s):\n{body}{tail}"), visible


def _find_project(goal: str, ctx: Dict[str, Any]) -> str:
    """Locate the folder the user MEANS by matching real directory names both
    ways — a goal word inside the folder name ('portfolio' → portfolio-site)
    counts, and so does the reverse. Token overlap, not substring luck."""
    from pathlib import Path
    root = Path(ctx["workspace"])
    dirs: List[str] = []
    for base in (root, root / "projects"):
        if base.is_dir():
            for d in sorted(base.iterdir()):
                if d.is_dir() and not d.name.startswith("."):
                    rel = d.name if base == root else f"projects/{d.name}"
                    dirs.append(rel)
    STOP = {"the", "a", "an", "my", "our", "this", "that", "project", "projects",
            "folder", "repo", "repository", "site", "website", "app", "wala", "wale",
            "wali", "ka", "ki", "ko", "me", "main", "mein", "dekh", "dekho", "dikha",
            "dikhaо", "list", "karo", "karo.", "show", "open", "what", "is", "in", "hai",
            "kya", "aura", "aur", "and", "check", "explore", "structure", "contents",
            "content", "files", "current", "ab", "just", "now", "us", "isme", "iska"}
    g_tokens = [t for t in re.split(r"[^a-z0-9_-]+", (goal or "").lower())
                if len(t) > 2 and t not in STOP]
    best, best_score = "", 0
    g_low = (goal or "").lower()
    for d in dirs:
        name = d.rsplit("/", 1)[-1]
        parts = [x for x in re.split(r"[-_. ]+", name) if x]
        score = 0
        if name in g_low or d in g_low:
            score += 4
        for t in g_tokens:
            if any(t == x or t in x or (len(x) > 3 and x in t) for x in parts):
                score += 2
        if d.startswith("projects/"):
            score += 0.5
        if score > best_score:
            best, best_score = d, score
    if best_score >= 2:
        return best
    m = re.search(r"projects?/([A-Za-z0-9_-]+)", g_low)
    if m:
        cand = f"projects/{m.group(1)}"
        if (root / cand).is_dir():
            return cand
    return ""


def _tree(rel: str, root, max_lines: int = 26) -> str:
    from pathlib import Path
    base = Path(root) / rel
    out: List[str] = []
    if not base.is_dir():
        return ""
    for p in sorted(base.rglob("*")):
        if any(part.startswith(".") for part in p.relative_to(base).parts):
            continue
        if p.is_dir() or p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico"}:
            if p.is_dir():
                continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        out.append(f"  • {p.relative_to(base)}  ({size} B)")
        if len(out) >= max_lines:
            out.append("  …")
            break
    return "\n".join(out)


def _server_state(ctx: Dict[str, Any]) -> Tuple[bool, str, List[str]]:
    import json
    from pathlib import Path
    ws = Path(ctx["workspace"])
    files = [ws / ".deepseek" / "servers.json", ws.parent / ".deepseek" / "servers.json"]
    data: Dict[str, Any] = {}
    src_of: Dict[str, Any] = {}
    for f in (ctx.get("server_registry"),) + tuple(files):
        try:
            if isinstance(f, dict):
                for k, v in f.items():
                    data.setdefault(str(k), v)
                continue
            if f and Path(f).exists():
                for k, v in json.loads(Path(f).read_text() or "{}").items():
                    data.setdefault(str(k), v)
                    src_of[str(k)] = Path(f)
        except Exception:
            continue
    if not data:
        return True, "No harness-tracked server is running right now.", []
    # (registry is the source; the socket probe below confirms each entry)
    import socket
    live, ghosts = [], []
    for port, meta in data.items():
        s = socket.socket()
        s.settimeout(0.35)
        try:
            s.connect(("127.0.0.1", int(port)))
            live.append(f"  • :{port} UP (pid {meta.get('pid', '?')})")
        except OSError:
            pid = int(meta.get("pid") or 0)
            proc_alive = False
            try:
                proc_alive = bool(pid) and Path(f"/proc/{pid}").exists()
            except Exception:
                proc_alive = True
            if proc_alive:
                live.append(f"  • :{port} registered, pid {pid} alive, port not accepting")
            else:
                live.append(f"  • :{port} was tracked but is gone (pruned)")
                ghosts.append(port)
        finally:
            s.close()
    if ghosts:
        # v1.10.5: the resolver converges the registry itself, so a server that
        # died outside the harness stops haunting every later state answer.
        try:
            for gport in ghosts:
                f = src_of.get(str(gport))
                if f and Path(f).exists():
                    d = json.loads(Path(f).read_text() or "{}")
                    d.pop(str(gport), None)
                    Path(f).write_text(json.dumps(d), encoding="utf-8")
        except Exception:
            pass
    return True, "Server state (real socket probe + registry):\n" + "\n".join(live), live


def _supported_kwargs(tool: str, want: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Never send an argument the tool does not accept.

    v1.10.4 live bug: the resolver passed cwd= to git_status (which takes no
    arguments) → 'Bad arguments for git_status'. The registry validates by
    calling the handler, so an over-eager arg is a hard failure, not a warning.
    """
    try:
        import inspect
        t = ctx.get("tool_sig")
        names = set(t(tool)) if callable(t) else set()
    except Exception:
        names = set()
    if not names:
        return {}
    return {k: v for k, v in (want or {}).items() if k in names}


def _git_state(ctx: Dict[str, Any], op: str) -> Tuple[bool, str, List[str]]:
    args = _supported_kwargs(op, {"staged": False, "n": 5}, ctx)
    res = ctx["exec"](op, args)
    ok = res["ok"]
    out = (res["output"] if ok else res["error"])[:2000]
    return ok, out, []


def _memory_state(ctx: Dict[str, Any]) -> Tuple[bool, str, List[str]]:
    mem = ctx.get("memory")
    if not mem:
        return True, "Memory store is disabled in this config.", []
    st = mem.stats()
    facts = mem.recall(None, 8)
    lines = [f"Sessions {st.get('sessions', 0)} · messages {st.get('messages', 0)} · "
             f"tasks {st.get('tasks', 0)} · facts {st.get('facts', 0)}"]
    for f in facts:
        lines.append(f"  • [{f['kind']}] {f['key']}: {str(f['value'])[:70]}")
    return True, "\n".join(lines), lines


def _file_exists(ctx: Dict[str, Any], name: str) -> Tuple[bool, str, List[str]]:
    from pathlib import Path
    root = Path(ctx["workspace"])
    rel = name.strip().lstrip("./").strip("/")
    cand = (root / rel)
    if cand.exists():
        size = _fmt_size(cand.stat().st_size) if cand.is_file() else "dir"
        return True, f"`{rel}` EXISTS ({size}).", [rel]
    hits = [str(p.relative_to(root)) for p in root.rglob(rel) if p.is_file()][:5]
    if hits:
        return True, f"`{rel}` is not at that path, but found:\n" + "\n".join(f"  • {h}" for h in hits), hits
    return True, f"`{rel}` does NOT exist in the workspace.", []


INTENT_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("workspace_list", re.compile(
        # v1.10.4 — "which deterministic read answers this?" Two shapes:
        #   <space-noun> … <listing question>     "workspace me kya hai"
        #   <listing question> … <space-noun>      "what is in the workspace"
        # Deliberately EXCLUDED: reflective follow-ups (handled by the L2 ledger
        # path) and bare 'project' (it appears in too many work requests).
        r"(?<!\w)(?:workspace|work\s*space|folder|dir(?:ectory)?|repo|repository|yahan|here|isme)"
        r"(?![^?!.]{0,60}\b(?:important|seekh|sikh|samajh|meaning|matlab|improve)\b)"
        r"[^?!.]{0,60}?\b(?:kya\s+\w+|kya|files?|items?|folders?|list(?:ing)?|dekh|dikha|show|"
        r"content|kitne|how\s+many|count|what(?:'s|s)?\b)\b"
        r"|(?:^|\s)(?:what(?:'s|s)?|list|show|kitne|how\s+many|which|count)\b[^?!.]{0,60}?"
        r"\b(?:workspace|folder|dir(?:ectory)?|repo|repository|yahan|here|isme|contents?)\b",
        re.I)),
    ("project_tree", re.compile(
        # "<the> <name> <project|folder> … dekh/list"  and  "<folder>/… kya hai".
        # Answers a project INSPECTION without spinning up a 2-task DAG.
        r"\b([a-z0-9][a-z0-9_ -]{1,28}?)\s*(?:wale\s+)?(?:project|folder|repo|site|app|directory)\b"
        r"[^.?!\n]{0,45}?\b(?:dekh|dikha|list|show|open|kya\s+\w+|what|explore|content|structure|banana\s+gaya)\b"
        r"|\b(?:dekh|dikha|list|show)\b[^.?!\n]{0,45}?\b([a-z0-9][a-z0-9_ -]{1,28}?)\s*"
        r"(?:wale\s+)?(?:project|folder|repo|site)\b", re.I)),

    # v1.10.4 FIX: this pattern used to accept a bare 'status' anywhere in the
    # sentence and had a dead '\bpe\b' alternative (so 'proto'+'type' matched
    # it) — a README edit request was answered with a server table. A state
    # answer now requires the verb/adj INSIDE the window after a real runtime
    # noun, and 'status' only counts next to server/port.
    # v1.10.4 — a state question must have the runtime noun AND a state verb/adj
    # within one short window, in either word order. The previous version accepted
    # a bare 'status' anywhere in the sentence (and had a dead '\bpe\b' branch, so
    # 'proto'+'type' matched) — a README edit ask got answered with a server
    # table. Imperatives ('server band kar do') are excluded by is_state_confirmation.
    ("server_state", re.compile(
        r"\b(?:server|port\s*\d*|site|app|api|listener|process)\b[^?!.]{0,30}?"
        r"\b(?:chal\s*(?:rh|rah|ra|)\w*|running|up\b|live\b|down\b|dead|alive|bound|bind\w*|"
        r"ka\s+status|status\s*(?:kya|hai|ho|dekh|bata|kaisa)|kya\s+(?:haal|status|haal)|"
        r"kaunsa\s+port|kitne\s+port)\b"
        r"|\b(?:status|haal|haal)\b[^?!.]{0,16}(?:of|for|ka)\b[^?!.]{0,20}"
        r"\b(?:server|port|site|app|listener)\b"
        r"|\b(?:kitne|kaunsa|which|what|how\s+many)\b[^?!.]{0,22}\bports?\b[^?!.]{0,18}"
        r"\b(?:khul|open|bind|use|chal|busy|free)\w*"
        r"|\b(?:running|live|up|down|listening)\b[^?!.]{0,26}\b(?:server|port)\b"
        # a phrase that OPENS with a bare port number and a state verb ('8131 pe chal
        # raha') is a state ask. Anchored at ^ so 'host karo 8130 pe' stays a write.
        r"|^\s*(?:port\s+)?\d{2,5}\s+pe\s+(?:kya\s+|ab\s+)?(?:chal\s*\w*|server|site|live|up|band|chal)\b",
        re.I)),

    ("git_state", re.compile(
        r"\bgit\b[^?!.]{0,30}\b(status|diff|log|commit|change|hua|push)\b"
        r"|\b(latest|last|recent|aakhri|abhi\s+ki)\b[^?!.]{0,20}\bcommits?\b"
        r"|\bcommits?\b[^?!.]{0,25}\b(?:kya\s+(?:hai|hua)|list|dikh|log)\b", re.I)),
    ("memory_state", re.compile(
        r"\b(memory|sessions?|history|facts?)\b[^?!.]{0,30}\b(kya\s+hai|list|show|kitne|dekh|status)\b", re.I)),
    ("file_exists", re.compile(
        r"\b(?:k(?:ya|y)\s+)?(?:file|folder|folder\s*name)?\s*[\w./-]+\.(?:py|md|json|html|css|js|txt|yaml|yml|db|png|jpg|jpeg|webp)"
        r"\b[^?!.]{0,40}\b(exist|hai|kahan|where|check|verif| mila|found|dikh)\b", re.I)),
]


IMPERATIVE = re.compile(
    r"\b(?:kar\s*(?:do|de|diya|donga|lena|le\s+le)|karo|kar\s+lo|do|de|dijo|"
    r"band\s+kar|chalu\s*kar|"
    r"start\s+kar|stop\s+kar|mar\s+do|kill\s+kar|restart\s+kar|make|create|bana\w*|"
    r"run\s+karo|execute\s+karo)\b", re.I)



def classify(goal: str) -> str:
    g = (goal or "").strip()
    if not g or len(g) > 160:
        return ""
    if (is_state_confirmation(g) and not IMPERATIVE.search(g)
            and re.search(r"\b(?:server|port|site|app|ab|still|now)\b", g, re.I)):
        return "server_state"
    if is_resource_lookup(g):
        return "resource_lookup"
    for name, rx in INTENT_PATTERNS:
        # v1.10.4: the state/read intents describe a QUESTION about the runtime.
        # If the sentence is an instruction ("host karo", "band kar do", "banao"),
        # a regex that merely grazes a noun must not answer it — that ends the
        # turn with a server table instead of doing the work (live bug).
        if name in ("server_state", "workspace_list", "project_tree", "memory_state") \
                and IMPERATIVE.search(g):
            continue
        if rx.search(g):
            return name
    return ""


def _extract_name(goal: str) -> str:
    m = re.search(r"[\w./-]+\.(?:py|md|json|html|css|js|txt|yaml|yml|db|png|jpg|jpeg|webp)", goal or "")
    return m.group(0) if m else ""


def resolve(goal: str, ctx: Dict[str, Any], project_hint: str = "") -> Optional[Dict[str, Any]]:
    """Return a deterministic answer dict, or None if this needs an agent.

    ctx keys: workspace, exec(tool,args)->{ok,output,error}, memory, server_registry,
              ledger, config
    dict: {intent, answer, evidence:[(tool,args,ok,out)], needs_llm}
    """
    intent = classify(goal)
    if not intent:
        return None
    try:
        if intent == "workspace_list":
            ok, ans, items = _list_workspace(ctx)
            ev = [("list_dir", {"path": "."}, True, ans)]
            return {"intent": intent, "answer": ans, "evidence": ev, "needs_llm": False,
                    "count": len(items)}
        if intent == "project_tree":
            target = _find_project(goal, ctx)
            if not target:
                return None
            tree = _tree(target, ctx["workspace"])
            ans = (f"`{target}` — real files on disk:\n{tree}" if tree
                   else f"`{target}` exists but is empty.")
            return {"intent": intent, "answer": ans,
                    "evidence": [("list_dir", {"path": target, "depth": 3}, True, tree)],
                    "needs_llm": False}

        if intent == "resource_lookup":
            return _resource_lookup(goal, ctx, project_hint)

        if intent == "server_state":
            ok, ans, items = _server_state(ctx)
            return {"intent": intent, "answer": ans,
                    "evidence": [("server_registry", {"path": "workspace/.deepseek/servers.json"},
                                  True, ans)], "needs_llm": False}
        if intent == "git_state":
            op = "git_status"
            if re.search(r"\bdiff\b", goal, re.I):
                op = "git_diff"
            elif re.search(r"\blog\b|commit", goal, re.I):
                op = "git_log"
            ok, ans, items = _git_state(ctx, op)
            head = {"git_status": "Git status (workspace repo)",
                    "git_diff": "Git diff --stat",
                    "git_log": "Recent commits"}[op]
            body_txt = (ans or "").strip()
            if ok and not body_txt:
                body_txt = "(no output — clean tree, nothing staged or modified)"
            elif ok and body_txt in ("(empty)", "## master", "## main"):
                body_txt += "\n  (that is the whole output — i.e. the tree is CLEAN)"
            body = body_txt if ok else f"git failed: {ans}"
            return {"intent": intent, "ok": ok,
                    "answer": f"{head}:\n{body}"[:2400],
                    "evidence": [(op, {}, ok, ans)], "needs_llm": False}
        if intent == "memory_state":
            ok, ans, items = _memory_state(ctx)
            return {"intent": intent, "answer": ans, "evidence": [("recall", {}, True, ans)],
                    "needs_llm": False}
        if intent == "file_exists":
            name = _extract_name(goal)
            if not name:
                return None
            ok, ans, items = _file_exists(ctx, name)
            return {"intent": intent, "answer": ans,
                    "evidence": [("find_files", {"pattern": name}, ok, ans)], "needs_llm": False}
    except Exception as e:  # noqa: BLE001 — a resolver must never break the run
        return {"intent": f"{intent}_error", "answer": f"(resolver error: {e})",
                "evidence": [], "needs_llm": False}
    return None


def _resource_lookup(goal: str, ctx: Dict[str, Any], project_hint: str = "") -> Optional[Dict[str, Any]]:
    """Answer 'usme auth hai?' from an ACTUAL recursive search of the real tree.

    Scopes to the project the conversation is about (project_hint / the last
    observed project) so the answer is precise, and says NOT FOUND out loud
    rather than letting a language model be optimistic about it.
    """
    from pathlib import Path
    root = Path(ctx["workspace"])
    res = wanted_resource(goal)
    if not res:
        return None
    scope = ""
    for cand in (project_hint, ctx.get("project") or "", _find_project(goal, ctx)):
        if cand and (root / cand).is_dir():
            scope = cand
            break
    base = (root / scope) if scope else root
    if not base.is_dir():
        return None
    aliases = {
        "auth": ["auth", "login", "signin", "session", "jwt", "oauth", "password"],
        "backend": ["server", "api", "app.py", "main.py", "server.py", "wsgi", "app"],
        "db": ["db", "sqlite", "database", "schema", "model"],
        "test": ["test", "spec"],
        "config": ["config", "settings", "env", "ini", "toml"],
        "docker": ["docker", "dockerfile", "compose"],
        "docs": ["doc", "readme", "guide"],
        "static": ["static", "assets", "public", "public/"],
    }.get(res, [res])
    CODE_EXT = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb",
                ".php", ".html", ".css", ".sql", ".toml", ".cfg", ".ini"}
    DOC_EXT = {".md", ".txt", ".rst"}
    NEGATE = re.compile(r"\b(?:no|not|without|none|absent|missing|nahi|nahi|abhi\s+nahi|"
                        r"yet\s+to\s+be|planned|todo|not\s+yet)\b", re.I)
    hits: List[str] = []          # evidence WITH code behind it
    doc_only: List[str] = []      # only a README/doc mentions it
    negated: List[str] = []       # the only mention says it is absent
    scanned = 0
    try:
        files = [q for q in base.rglob("*") if q.is_file() and not q.parts[-1].startswith(".")]
    except OSError:
        files = []
    for q in files:
        if scanned > 400:
            break
        scanned += 1
        rel = str(q.relative_to(base))
        low_name = rel.lower()
        if any(a in low_name for a in aliases):
            hits.append(f"{rel}  (file name matches)")
            continue
        if q.suffix.lower() in (CODE_EXT | DOC_EXT) and q.stat().st_size < 400_000:
            try:
                body = q.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            low = body.lower()
            for a in aliases:
                i = low.find(a)
                if i < 0:
                    continue
                line = body[:i].count("\n")
                snippet = body.splitlines()[line].strip()[:150] if line < len(body.splitlines()) else ""
                neg = bool(NEGATE.search(snippet))
                rec = f"{rel}:{line + 1}  {snippet}" if snippet else rel
                if q.suffix.lower() in DOC_EXT:
                    # a README mention is NOT implementation — report it as such
                    (negated if neg else doc_only).append(rec + ("  [says it is ABSENT]" if neg else ""))
                else:
                    hits.append(rec + "  [negated]" if neg else rec)
                break
    where = f"`{scope}`" if scope else "the whole workspace"
    uniq = list(dict.fromkeys(hits))[:8]
    if uniq:
        ans = (f"Haan — `{res}` actually {where} me mila (code-level evidence):\n"
               + "\n".join(f"  • {h}" for h in uniq))
        if doc_only:
            ans += "\n  (aur doc-only mentions: " + "; ".join(doc_only[:3]) + ")"
    elif doc_only:
        ans = (f"`{res}` ka sirf **documentation mention** hai, code me koi "
               f"implementation nahi mila:\n"
               + "\n".join(f"  • {h}" for h in doc_only[:5])
               + f"\n  (maine {scanned} files scan kiye — yeh ek haan/nahi ka "
                 f"difference hai, isliye alag se bata raha hoon.)")
    elif negated:
        ans = (f"Nahi — jo bhi `{res}` ka zikr mila, usne khud kaha ki yeh **present "
               f"nahi** hai:\n" + "\n".join(f"  • {h}" for h in negated[:4])
               + f"\n  ({scanned} files scanned; koi code-level match nahi.)")
    else:
        ans = (f"Nahi — maine {where} ka **poora tree** scan kiya "
               f"({scanned} files checked) aur `{res}` se kuch match nahi hua. "
               f" Matlab: yeh abhi present nahi hai (guess nahi, actual search result).")
    return {"intent": "resource_lookup", "answer": ans,
            "evidence": [("resource_search", {"path": scope or ".", "pattern": res},
                          True, ans)], "needs_llm": False, "count": len(hits)}
