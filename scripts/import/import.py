#!/usr/bin/env python3
"""Import a screened sample of giantswarm/agent-memory into agentmemory and Cartographer.

Subcommands:
  fetch         download the listed files from the private source repo with `gh`
  agentmemory   import the files into an agentmemory 0.9.32 server (idempotent)
  cartographer  import the files into a local clone of the Cartographer KB repo

The file list (sample-files.txt) is the only company-related data in this repo.
Everything this script downloads or generates must stay outside of it: fetch and the
agentmemory state file refuse to write inside a git work tree that contains this
script.

Python 3.10+, standard library only. Run `import.py <command> -h` for flags.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LIST = SCRIPT_DIR / "sample-files.txt"
SOURCE_REPO = "giantswarm/agent-memory"
AGENT_ID = "agent-memory-import"
PROJECT = SOURCE_REPO
FORBIDDEN_AREAS = ("customers", "people")
ALLOWED_AREAS = ("company", "topics", "teams")

# OKF `type` -> agentmemory Memory.type (pattern|preference|architecture|bug|workflow|fact).
# Anything else falls back to "fact", which is also the server's own fallback.
AGENTMEMORY_TYPE = {
    "reference": "fact",
    "topic": "pattern",
    "project": "architecture",
}
# OKF `type` -> Cartographer concept type (PascalCase, the convention in data-plane.md).
CARTOGRAPHER_TYPE = {
    "reference": "Reference",
    "topic": "Topic",
    "project": "Project",
}
# agentmemory supersedes an existing memory of the same agentId/project when the
# word-set Jaccard similarity of the contents is above this (src/functions/remember.ts).
SUPERSEDE_JACCARD = 0.7
NEAR_JACCARD = 0.4
FRONTMATTER_ORDER = ("type", "title", "description", "tags", "timestamp", "related", "resource")


class ImportError_(Exception):
    """A fatal, user-facing error."""


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- file list


def validate_path(p: str) -> str | None:
    """Return a reason the path is refused, or None when it is acceptable."""
    if p.startswith("/") or "\\" in p:
        return "must be repo-relative with forward slashes"
    parts = p.split("/")
    if any(seg in ("", ".", "..") for seg in parts):
        return "must not contain empty, '.' or '..' segments"
    if parts[0] in FORBIDDEN_AREAS:
        return f"paths under {parts[0]}/ are never imported"
    if parts[0] not in ALLOWED_AREAS:
        return f"unknown area {parts[0]!r} (allowed: {', '.join(ALLOWED_AREAS)})"
    if parts[0] == "teams" and len(parts) != 3:
        return "team files must be teams/<slug>/<name>.md"
    if parts[0] != "teams" and len(parts) != 2:
        return f"{parts[0]} files must be {parts[0]}/<name>.md"
    if not p.endswith(".md"):
        return "only .md files are imported"
    if parts[-1] == "index.md":
        return "area index.md files are generated and skipped"
    return None


def load_list(path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """Read the file list. Returns (accepted paths, [(refused path, reason)])."""
    if not path.is_file():
        die(f"file list not found: {path}")
    accepted: list[str] = []
    refused: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.endswith(".md"):
            line += ".md"
        reason = validate_path(line)
        if reason:
            refused.append((line, reason))
            continue
        if line in seen:
            continue
        seen.add(line)
        accepted.append(line)
    return accepted, refused


def area_of(path: str) -> str:
    parts = path.split("/")
    return f"team:{parts[1]}" if parts[0] == "teams" else parts[0]


def map_of(path: str) -> str:
    """Cartographer map for a source path: company, topics or team-<slug>."""
    parts = path.split("/")
    return f"team-{parts[1]}" if parts[0] == "teams" else parts[0]


def kb_path_of(path: str) -> str:
    """Path of the concept in the staged corpus / under the KB's data/ dir."""
    return f"{map_of(path)}/{path.split('/')[-1]}"


def guard_outside_this_repo(target: Path, what: str) -> None:
    """Refuse to put company content inside the git work tree holding this script."""
    try:
        top = subprocess.run(
            ["git", "-C", str(SCRIPT_DIR), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return
    repo = Path(top).resolve()
    t = target.resolve()
    if t == repo or repo in t.parents:
        die(f"{what} {target} is inside the public repo {repo}: choose a directory outside it")


# --------------------------------------------------------------------------- OKF parsing


@dataclass
class Doc:
    path: str
    fields: dict[str, object]          # key -> str | list[str]
    raw_keys: list[str]                # key order as found
    body: str

    @property
    def okf_type(self) -> str:
        return str(self.fields.get("type", "")).strip()

    @property
    def title(self) -> str:
        return str(self.fields.get("title", "")).strip()

    @property
    def description(self) -> str:
        return str(self.fields.get("description", "")).strip()

    @property
    def tags(self) -> list[str]:
        v = self.fields.get("tags", [])
        return [t for t in (v if isinstance(v, list) else split_flow(str(v))) if t]

    @property
    def related(self) -> list[str]:
        v = self.fields.get("related", [])
        if isinstance(v, list):
            return v
        return split_flow(str(v)) if str(v).strip() else []


def unquote(raw: str) -> str:
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] == "'":
        inner = s[1:-1]
        if "'" not in inner.replace("''", ""):
            return inner.replace("''", "'")
    if len(s) >= 2 and s[0] == s[-1] == '"':
        inner = s[1:-1]
        if not re.search(r'(?<!\\)"', inner):
            return inner.replace('\\"', '"').replace("\\\\", "\\")
    return s


def split_flow(raw: str) -> list[str]:
    """Split a one-line flow list `[a, "b", [c](/d.md)]` into unquoted items.

    Commas inside quotes, brackets or parentheses do not split, so markdown links
    with commas in their label survive.
    """
    s = raw.strip()
    if not (s.startswith("[") and s.endswith("]")):
        return [unquote(x) for x in s.split(",") if x.strip()]
    s = s[1:-1]
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'" and not "".join(buf).strip():
            quote = ch
            buf.append(ch)
            continue
        if ch in "[(":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if "".join(buf).strip():
        items.append("".join(buf))
    return [unquote(x) for x in items if x.strip()]


FM_KEY = re.compile(r"^([A-Za-z_][\w-]*):(?:[ \t]+(.*))?[ \t]*$")
LIST_KEYS = {"tags", "related"}


def parse_doc(path: str, text: str) -> Doc:
    """Parse the lenient OKF frontmatter used by agent-memory.

    It is YAML-like but not always valid YAML (for example an unquoted title that
    starts with a quote, or a flow list of markdown links), so it is parsed line by
    line: `key: value` at column 0, block-list items `- x` under an empty key,
    indented continuation lines appended to the previous scalar.
    """
    text = text.replace("\r\n", "\n").lstrip("﻿")
    if not text.startswith("---\n"):
        raise ImportError_(f"{path}: no frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ImportError_(f"{path}: unterminated frontmatter")
    fm_text = text[4:end + 1]
    rest = text[end + 4:]
    body = rest[1:] if rest.startswith("\n") else rest
    fields: dict[str, object] = {}
    order: list[str] = []
    cur: str | None = None
    for n, line in enumerate(fm_text.split("\n"), start=2):
        if not line.strip() or line.startswith("#"):
            continue
        m = FM_KEY.match(line)
        if m:
            cur = m.group(1)
            if cur in fields:
                raise ImportError_(f"{path}:{n}: duplicate key {cur!r}")
            fields[cur] = (m.group(2) or "").strip()
            order.append(cur)
            continue
        item = re.match(r"^\s*-\s+(.*)$", line)
        if item and cur is not None and (isinstance(fields[cur], list) or fields[cur] == ""):
            if not isinstance(fields[cur], list):
                fields[cur] = []
            fields[cur].append(unquote(item.group(1)))  # type: ignore[union-attr]
            continue
        if line[:1] in (" ", "\t") and cur is not None and isinstance(fields[cur], str):
            fields[cur] = f"{fields[cur]} {line.strip()}".strip()
            continue
        raise ImportError_(f"{path}:{n}: cannot parse frontmatter line")
    for k, v in list(fields.items()):
        if isinstance(v, str):
            fields[k] = split_flow(v) if (k in LIST_KEYS and v) else unquote(v)
    for req in ("type", "title"):
        if not fields.get(req):
            raise ImportError_(f"{path}: frontmatter has no {req!r}")
    return Doc(path=path, fields=fields, raw_keys=order, body=body)


def norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[`*_\"'’‘“”]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def split_leading_h1(body: str, title: str) -> tuple[str, str]:
    """Drop the H1 that opens the body (by the source format it repeats the title).

    Returns (body without it, "exact" | "variant" | ""): "variant" when the heading is
    a reworded/shortened title, "" when the body does not open with an H1.
    """
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith("# "):
            kind = "exact" if norm_title(line[2:]) == norm_title(title) else "variant"
            return "\n".join(lines[:i] + lines[i + 1:]).lstrip("\n"), kind
        break
    return body, ""


# --------------------------------------------------------------------------- links

LINK_RE = re.compile(
    r"(?P<bang>!?)\[(?P<label>(?:[^\[\]\n]|\[[^\[\]\n]*\])*)\]"
    r"\((?P<target>[^()\s]+(?:\([^()\s]*\))?)(?P<title>\s+\"[^\"]*\")?\)"
)
SINGLE_LINK_RE = re.compile(r"^\[(?P<label>.*)\]\((?P<target>[^()\s]+)\)$")


def resolve_target(src_path: str, target: str) -> tuple[str, str] | None:
    """Resolve a link target to (repo path, '#fragment'); None for external links."""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
        return None
    path, _, frag = target.partition("#")
    if not path:
        return None
    if path.startswith("/"):
        rp = posixpath.normpath(path.lstrip("/"))
    else:
        rp = posixpath.normpath(posixpath.join(posixpath.dirname(src_path), path))
    if rp.startswith("../") or rp == "..":
        return None
    return rp, (f"#{frag}" if frag else "")


@dataclass
class LinkStats:
    kept: int = 0          # target in sample, rewritten or kept as a link
    plain: int = 0         # target outside the sample -> plain text
    external: int = 0      # http(s), mailto, anchors
    sensitive: int = 0     # target under customers/ or people/ (also made plain text)

    def add(self, other: "LinkStats") -> None:
        self.kept += other.kept
        self.plain += other.plain
        self.external += other.external
        self.sensitive += other.sensitive


def rewrite_links(text: str, src_path: str, sample: set[str], new_href, stats: LinkStats) -> str:
    """Rewrite markdown links outside code fences/spans.

    new_href(target_repo_path, fragment) returns the replacement href for an
    in-sample target. Links to anything outside the sample become their label.
    """
    out_lines: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        stripped = line.lstrip()
        fm = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence:
            out_lines.append(line)
            if fm and stripped.startswith(fence):
                fence = None
            continue
        if fm:
            fence = fm.group(1)
            out_lines.append(line)
            continue
        # keep inline code spans untouched
        parts = re.split(r"(`[^`]*`)", line)
        for i, part in enumerate(parts):
            if i % 2 == 1:
                continue
            parts[i] = LINK_RE.sub(lambda m: _sub_link(m, src_path, sample, new_href, stats), part)
        out_lines.append("".join(parts))
    return "\n".join(out_lines)


def _sub_link(m: re.Match, src_path: str, sample: set[str], new_href, stats: LinkStats) -> str:
    target = m.group("target")
    res = resolve_target(src_path, target)
    if res is None:
        stats.external += 1
        return m.group(0)
    rp, frag = res
    if rp.split("/")[0] in FORBIDDEN_AREAS:
        stats.sensitive += 1
    if rp in sample:
        stats.kept += 1
        return f"{m.group('bang')}[{m.group('label')}]({new_href(rp, frag)}{m.group('title') or ''})"
    stats.plain += 1
    return m.group("label")


# --------------------------------------------------------------------------- loading


def load_docs(src: Path, paths: list[str]) -> tuple[list[Doc], list[str], list[str]]:
    """Load listed files from src. Returns (docs, missing paths, parse errors)."""
    docs: list[Doc] = []
    missing: list[str] = []
    errors: list[str] = []
    for p in paths:
        f = src / p
        if not f.is_file() or f.is_symlink():
            missing.append(p)
            continue
        try:
            docs.append(parse_doc(p, f.read_text(encoding="utf-8")))
        except (ImportError_, UnicodeDecodeError) as e:
            errors.append(str(e) if isinstance(e, ImportError_) else f"{p}: not UTF-8")
    return docs, missing, errors


def jaccard_tokens(text: str) -> set[str]:
    # Mirrors jaccardTokens() in agentmemory src/state/schema.ts for non-CJK text.
    return {t for t in unicodedata.normalize("NFC", text.lower()).split() if len(t) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


# --------------------------------------------------------------------------- fetch


def cmd_fetch(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser()
    guard_outside_this_repo(out, "--out")
    paths, refused = load_list(Path(args.list))
    for p, why in refused:
        print(f"refused  {p}: {why}", file=sys.stderr)
    if shutil.which("gh") is None:
        die("gh is not on PATH")
    st = subprocess.run(["gh", "api", f"repos/{args.repo}", "-q", ".full_name"], capture_output=True, text=True)
    if st.returncode != 0:
        die(f"gh cannot read {args.repo} (run `gh auth login` with an account that can): {st.stderr.strip()[:200]}")
    out.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, []
    for p in paths:
        endpoint = f"repos/{args.repo}/contents/{urllib.parse.quote(p)}"
        if args.ref:
            endpoint += f"?ref={urllib.parse.quote(args.ref)}"
        r = subprocess.run(
            ["gh", "api", "-H", "Accept: application/vnd.github.raw", endpoint],
            capture_output=True,
        )
        if r.returncode != 0:
            failed.append(p)
            print(f"failed   {p}: {r.stderr.decode(errors='replace').strip()[:200]}", file=sys.stderr)
            continue
        data = r.stdout
        # Some proxies ignore the raw media type and return the JSON envelope.
        if data[:1] == b"{":
            try:
                env = json.loads(data)
                if isinstance(env, dict) and env.get("encoding") == "base64":
                    data = base64.b64decode(env["content"])
            except (ValueError, KeyError):
                pass
        dest = out / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        ok += 1
    print("\n== fetch summary ==")
    print(f"source repo      {args.repo}{'@' + args.ref if args.ref else ''}")
    print(f"listed (valid)   {len(paths)}")
    print(f"refused          {len(refused)}")
    print(f"downloaded       {ok}")
    print(f"failed           {len(failed)}")
    print(f"output dir       {out}")
    return 1 if failed or refused else 0


# --------------------------------------------------------------------------- agentmemory


def agentmemory_payload(doc: Doc, sample: set[str], stats: LinkStats) -> tuple[dict, str]:
    body, stripped = split_leading_h1(doc.body, doc.title)
    body = rewrite_links(body, doc.path, sample, lambda rp, frag: f"/{rp}{frag}", stats)
    parts = [doc.title]
    if doc.description:
        parts.append(doc.description)
    if body.strip():
        parts.append(body.strip())
    resource = str(doc.fields.get("resource", "")).strip()
    if resource:
        parts.append(f"Resource: {resource}")
    concepts: list[str] = []
    for c in doc.tags + [area_of(doc.path)]:
        if c not in concepts:
            concepts.append(c)
    payload = {
        "content": "\n\n".join(parts) + "\n",
        "type": AGENTMEMORY_TYPE.get(doc.okf_type, "fact"),
        "concepts": concepts,
        "files": [f"{SOURCE_REPO}:{doc.path}"],
        "project": PROJECT,
        "agentId": AGENT_ID,
    }
    return payload, stripped


def payload_sha(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Api:
    def __init__(self, base: str, secret: str, timeout: float = 60.0):
        self.base = base.rstrip("/")
        self.secret = secret
        self.timeout = timeout

    def call(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
        url = f"{self.base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.secret}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b"null")
            except ValueError:
                payload = None
            return e.code, payload


def default_state_path(url: str) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    h = hashlib.sha256(url.rstrip("/").encode()).hexdigest()[:12]
    return base / "agent-memory-import" / f"agentmemory-{h}.json"


def load_state(path: Path, url: str) -> dict:
    if path.is_file():
        st = json.loads(path.read_text(encoding="utf-8"))
        if st.get("url") != url.rstrip("/") or st.get("project") != PROJECT:
            die(f"state file {path} belongs to another server/project ({st.get('url')}); pass --state")
        st.setdefault("entries", {})
        return st
    return {"version": 1, "url": url.rstrip("/"), "project": PROJECT, "agentId": AGENT_ID, "entries": {}}


def save_state(path: Path, st: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def server_memories(api: Api) -> dict[str, list[dict]]:
    """Latest memories of this importer, grouped by source path (from files[])."""
    code, res = api.call("GET", "/agentmemory/memories",
                         query={"agentId": AGENT_ID, "project": PROJECT, "latest": "true"})
    if code != 200 or not isinstance(res, dict):
        raise ImportError_(f"GET /agentmemory/memories returned HTTP {code}")
    by_path: dict[str, list[dict]] = {}
    prefix = f"{SOURCE_REPO}:"
    for m in res.get("memories", []):
        for f in m.get("files", []) or []:
            if isinstance(f, str) and f.startswith(prefix):
                by_path.setdefault(f[len(prefix):], []).append(m)
    return by_path


def same_memory(m: dict, payload: dict) -> bool:
    return (m.get("content") == payload["content"] and m.get("type") == payload["type"]
            and list(m.get("concepts") or []) == payload["concepts"]
            and list(m.get("files") or []) == payload["files"])


def team_shared_ids(api: Api) -> set[str] | None:
    """Memory ids already in the team feed; None when team memory is disabled."""
    code, res = api.call("GET", "/agentmemory/team/feed", query={"limit": "100000"})
    if code == 404:
        return None
    if code != 200 or not isinstance(res, dict):
        raise ImportError_(f"GET /agentmemory/team/feed returned HTTP {code}")
    ids = set()
    for item in res.get("items", []):
        c = item.get("content") if isinstance(item, dict) else None
        if item.get("type") == "memory" and isinstance(c, dict) and c.get("id"):
            ids.add(c["id"])
    return ids


def cmd_agentmemory(args: argparse.Namespace) -> int:
    src = Path(args.src).expanduser()
    paths, refused = load_list(Path(args.list))
    for p, why in refused:
        print(f"refused  {p}: {why}", file=sys.stderr)
    docs, missing, errors = load_docs(src, paths)
    for p in missing:
        print(f"missing  {p}", file=sys.stderr)
    for e in errors:
        print(f"invalid  {e}", file=sys.stderr)
    sample = {d.path for d in docs}

    lstats = LinkStats()
    planned: list[tuple[Doc, dict, str]] = []
    h1_kinds = {"exact": 0, "variant": 0, "": 0}
    type_counts: dict[str, int] = {}
    for d in docs:
        payload, stripped = agentmemory_payload(d, sample, lstats)
        h1_kinds[stripped] += 1
        key = f"{d.okf_type or '?'}->{payload['type']}"
        type_counts[key] = type_counts.get(key, 0) + 1
        planned.append((d, payload, payload_sha(payload)))

    # Two files whose contents are this similar would supersede each other on the
    # server (same agentId and project), silently hiding the first one.
    toks = [jaccard_tokens(p["content"]) for _, p, _ in planned]
    collisions, near = [], 0
    for i in range(len(planned)):
        for j in range(i + 1, len(planned)):
            s = jaccard(toks[i], toks[j])
            if s > SUPERSEDE_JACCARD:
                collisions.append((planned[i][0].path, planned[j][0].path, s))
            elif s > NEAR_JACCARD:
                near += 1

    url = args.url.rstrip("/")
    state_path = Path(args.state).expanduser() if args.state else default_state_path(url)
    guard_outside_this_repo(state_path, "--state")
    state = load_state(state_path, url)

    counts = dict(created=0, updated=0, unchanged=0, adopted=0, forgotten=0,
                  shared=0, already_shared=0, share_failed=0, failed=0)
    secret = os.environ.get(args.secret_env, "")
    api = Api(url, secret) if secret else None
    if not args.dry_run and not secret:
        die(f"environment variable {args.secret_env} is empty")

    print("== agentmemory plan ==")
    print(f"server           {url}")
    print(f"state file       {state_path}")
    print(f"files loaded     {len(docs)} (listed {len(paths)}, missing {len(missing)}, invalid {len(errors)}, refused {len(refused)})")
    print(f"type mapping     {', '.join(f'{k}: {v}' for k, v in sorted(type_counts.items()))}")
    print(f"leading H1       dropped {h1_kinds['exact'] + h1_kinds['variant']} ({h1_kinds['exact']} same as title, {h1_kinds['variant']} reworded), none in {h1_kinds['']}")
    print(f"links            kept {lstats.kept}, to plain text {lstats.plain} (customers/people {lstats.sensitive}), external {lstats.external}")
    if docs:
        sizes = sorted(len(p["content"]) for _, p, _ in planned)
        print(f"content chars    min {sizes[0]}, median {sizes[len(sizes)//2]}, max {sizes[-1]}, total {sum(sizes)}")
        print(f"concepts/memory  min {min(len(p['concepts']) for _, p, _ in planned)}, max {max(len(p['concepts']) for _, p, _ in planned)}")
    print(f"similar pairs    supersede-level (>{SUPERSEDE_JACCARD}) {len(collisions)}, near (>{NEAR_JACCARD}) {near}")
    for a, b, s in collisions:
        print(f"  collision {a} ~ {b} ({s:.2f})", file=sys.stderr)
    if collisions and not args.dry_run:
        die("some files would supersede each other on the server; fix the list or the content first")

    on_server: dict[str, list[dict]] | None = None
    shared: set[str] | None = set()
    if api:
        try:
            on_server = server_memories(api)
            shared = team_shared_ids(api) if not args.no_share else set()
        except (ImportError_, urllib.error.URLError, OSError) as e:
            if not args.dry_run:
                die(str(e))
            warn(f"server not readable, planning from the state file only: {e}")
            on_server = None
        if shared is None:
            warn("team memory is not enabled on the server (TEAM_ID/USER_ID unset): sharing will fail")

    entries = state["entries"]
    for d, payload, sha in planned:
        ent = entries.get(d.path)
        server_list = (on_server or {}).get(d.path, [])
        server_ids = {m["id"] for m in server_list}
        current_id = ent.get("memoryId") if ent else None
        if on_server is not None and current_id and current_id not in server_ids:
            current_id = None  # deleted or superseded behind our back
        adopted = False
        if on_server is not None and not current_id and server_list:
            # State file lost or new laptop: re-adopt an identical memory instead of
            # posting it again (which would create a new version of it).
            match = [m for m in server_list if same_memory(m, payload)]
            if match:
                current_id = match[0]["id"]
                entries[d.path] = ent = {"memoryId": current_id, "sha": sha, "sharedMemoryId": None}
                counts["adopted"] += 1
                adopted = True
        unchanged = bool(current_id and ent and ent.get("sha") == sha)
        if unchanged:
            if not adopted:
                counts["unchanged"] += 1
        else:
            old_ids = [i for i in ([current_id] if current_id else [])]
            old_ids += [i for i in server_ids if i not in old_ids]
            action = "update" if old_ids else "create"
            if args.dry_run:
                counts["updated" if old_ids else "created"] += 1
                counts["shared"] += 0 if args.no_share else 1
                print(f"would {action:6} {d.path}")
                continue
            code, res = api.call("POST", "/agentmemory/remember", payload)  # type: ignore[union-attr]
            mem = res.get("memory") if isinstance(res, dict) else None
            if code not in (200, 201) or not (isinstance(res, dict) and res.get("success")) or not mem:
                counts["failed"] += 1
                print(f"failed   {d.path}: remember HTTP {code} {(res or {}).get('error', '') if isinstance(res, dict) else ''}", file=sys.stderr)
                continue
            new_id = mem["id"]
            superseded = set(mem.get("supersedes") or [])
            known = {e.get("memoryId"): p for p, e in entries.items() if p != d.path}
            for sid in superseded:
                if sid in known:
                    warn(f"{d.path} superseded the memory of {known[sid]} on the server (content too similar)")
            # One latest memory per source file: drop an old version the server did
            # not supersede by itself (it only does when Jaccard > 0.7).
            for oid in old_ids:
                if oid == new_id or oid in superseded:
                    continue
                c2, r2 = api.call("POST", "/agentmemory/forget", {"memoryId": oid})  # type: ignore[union-attr]
                if c2 == 200:
                    counts["forgotten"] += 1
                else:
                    warn(f"{d.path}: could not forget old memory {oid} (HTTP {c2})")
            entries[d.path] = ent = {"memoryId": new_id, "sha": sha, "sharedMemoryId": None,
                                     "importedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            counts["updated" if old_ids else "created"] += 1
            current_id = new_id
            save_state(state_path, state)

        if args.no_share or not current_id:
            continue
        already = (ent or {}).get("sharedMemoryId") == current_id or (shared is not None and current_id in shared)
        if already:
            counts["already_shared"] += 1
            if ent is not None and ent.get("sharedMemoryId") != current_id:
                ent["sharedMemoryId"] = current_id
            continue
        if args.dry_run:
            counts["shared"] += 1
            continue
        code, res = api.call("POST", "/agentmemory/team/share",  # type: ignore[union-attr]
                             {"itemId": current_id, "itemType": "memory", "project": PROJECT})
        if code in (200, 201) and isinstance(res, dict) and res.get("success"):
            counts["shared"] += 1
            ent["sharedMemoryId"] = current_id  # type: ignore[index]
            ent["teamItemId"] = (res.get("sharedItem") or {}).get("id")  # type: ignore[index]
        else:
            counts["share_failed"] += 1
            err = res.get("error") if isinstance(res, dict) else ""
            print(f"share failed {d.path}: HTTP {code} {err}", file=sys.stderr)
        save_state(state_path, state)

    if not args.dry_run:
        save_state(state_path, state)

    print("\n== agentmemory summary ==" + (" (dry run, nothing written)" if args.dry_run else ""))
    for k in ("created", "updated", "unchanged", "adopted", "forgotten", "shared", "already_shared", "share_failed", "failed"):
        print(f"{k:16} {counts[k]}")
    stale = sorted(set(entries) - sample)
    if stale:
        print(f"in state, not in this run: {len(stale)} (left on the server; remove with POST /agentmemory/forget)")
    if api and on_server is not None and not args.dry_run:
        after = server_memories(api)
        dup = sum(1 for p in sample if len(after.get(p, [])) > 1)
        print(f"server latest    {sum(len(v) for k, v in after.items() if k in sample)} memories for {len(sample)} files ({dup} files with >1 latest)")
    bad = counts["failed"] + counts["share_failed"] + len(missing) + len(errors) + len(refused)
    return 1 if bad else 0


# --------------------------------------------------------------------------- cartographer


def yaml_scalar(s: str) -> str:
    """Serialize a string so both YAML and Cartographer's frontmatter parser read it back."""
    plain_ok = (
        s != ""
        and s == s.strip()
        and s[0] not in "[]{}&*!|>'\"%@`,#?:-~"
        and ": " not in s
        and " #" not in s
        and not s.endswith(":")
        and "\n" not in s
        and s.lower() not in ("true", "false", "null", "yes", "no", "on", "off")
        and not re.fullmatch(r"[-+.\d][\d._:eE+-]*", s)
    )
    if plain_ok:
        return s
    if '"' not in s and "\\" not in s:
        return f'"{s}"'
    return "'" + s.replace("'", "''") + "'"


def yaml_flow_item(s: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+-]*", s):
        return s
    if '"' not in s and "\\" not in s:
        return f'"{s}"'
    return "'" + s.replace("'", "''") + "'"


def cartographer_doc(doc: Doc, sample: set[str], stats: LinkStats) -> tuple[str, str]:
    """Return (staged relative path, file text) for one source file."""
    dest = kb_path_of(doc.path)
    dest_dir = posixpath.dirname(dest)

    def href(rp: str, frag: str) -> str:
        return posixpath.relpath(kb_path_of(rp), dest_dir) + frag

    # The source H1 repeats the title, sometimes reworded; Cartographer's
    # title_h1_mismatch lint wants them equal, and the title is the label it shows.
    body, h1 = split_leading_h1(doc.body, doc.title)
    if h1:
        body = f"# {doc.title}\n\n{body}"
    body = rewrite_links(body, doc.path, sample, href, stats)

    related_out: list[str] = []
    for item in doc.related:
        m = SINGLE_LINK_RE.match(item.strip())
        label, target = (m.group("label"), m.group("target")) if m else (None, item.strip())
        res = resolve_target(doc.path, target) if target.endswith(".md") or ".md#" in target else None
        if res is None:  # external link or plain text: keep as written
            stats.external += 1 if m else 0
            related_out.append(item.strip())
            continue
        rp, frag = res
        if rp.split("/")[0] in FORBIDDEN_AREAS:
            stats.sensitive += 1
        if label is None:
            label = posixpath.splitext(posixpath.basename(rp))[0]
        if rp in sample:
            stats.kept += 1
            related_out.append(f"[{label}]({href(rp, frag)})")
        else:
            stats.plain += 1
            related_out.append(label)

    fields = dict(doc.fields)
    fields["type"] = CARTOGRAPHER_TYPE.get(doc.okf_type, doc.okf_type[:1].upper() + doc.okf_type[1:])
    lines = ["---"]
    keys = [k for k in FRONTMATTER_ORDER if k in fields] + [k for k in doc.raw_keys if k not in FRONTMATTER_ORDER]
    for k in keys:
        if k == "related":
            if related_out:
                lines.append("related: [" + ", ".join(yaml_flow_item(x) for x in related_out) + "]")
            continue
        v = fields[k]
        if k == "tags":
            lines.append("tags: [" + ", ".join(yaml_flow_item(t) for t in doc.tags) + "]")
        elif isinstance(v, list):
            lines.append(f"{k}: [" + ", ".join(yaml_flow_item(str(x)) for x in v) + "]")
        else:
            lines.append(f"{k}: {yaml_scalar(str(v).strip())}")
    lines.append(f"provenance: [{yaml_flow_item(f'https://github.com/{SOURCE_REPO}/blob/main/{doc.path}')}]")
    lines.append("---")
    text = "\n".join(lines) + "\n" + body
    if not text.endswith("\n"):
        text += "\n"
    return dest, text


def git(clone: Path, *a: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(clone), *a], capture_output=True, text=True, check=check)


def github_slug(remote: str) -> str | None:
    m = re.match(r"^(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$", remote)
    return m.group(1) if m else None


def cmd_cartographer(args: argparse.Namespace) -> int:
    src = Path(args.src).expanduser()
    clone = Path(args.kb_clone).expanduser().resolve()
    guard_outside_this_repo(clone, "--kb-clone")
    binary = args.bin or os.environ.get("CARTOGRAPHER_BIN") or "cartographer"
    if shutil.which(binary) is None:
        die(f"cartographer binary {binary!r} not found on PATH")
    ver = subprocess.run([binary, "version"], capture_output=True, text=True).stdout.strip()
    if not ver.startswith("v0.28."):
        warn(f"cartographer {ver or '?'} found; this script was tested with v0.28.0")
    if not (clone / ".git").exists():
        die(f"{clone} is not a git clone")
    if not (clone / "data").is_dir():
        die(f"{clone} has no data/ dir: not an initialized Cartographer KB (see `cartographer kb create`)")

    paths, refused = load_list(Path(args.list))
    for p, why in refused:
        print(f"refused  {p}: {why}", file=sys.stderr)
    docs, missing, errors = load_docs(src, paths)
    for p in missing:
        print(f"missing  {p}", file=sys.stderr)
    for e in errors:
        print(f"invalid  {e}", file=sys.stderr)
    sample = {d.path for d in docs}

    remote = git(clone, "remote", "get-url", "origin", check=False).stdout.strip()
    slug = github_slug(remote) if remote else None
    if args.push:
        if not remote:
            die("--push: the clone has no origin remote")
        if slug:
            r = subprocess.run(["gh", "repo", "view", slug, "--json", "visibility", "-q", ".visibility"],
                               capture_output=True, text=True)
            vis = r.stdout.strip().upper()
            if r.returncode != 0:
                die(f"--push: cannot read the visibility of {slug} with gh")
            if vis == "PUBLIC" and not args.allow_public:
                die(f"--push: {slug} is PUBLIC; the KB holds company content and must be private")
            print(f"kb repo          {slug} ({vis.lower()})")
        else:
            print(f"kb remote        non-GitHub remote, visibility not checked")

    lstats = LinkStats()
    per_map: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="agent-memory-stage-") as stage_s:
        stage = Path(stage_s)
        for d in docs:
            dest, text = cartographer_doc(d, sample, lstats)
            f = stage / dest
            f.parent.mkdir(parents=True, exist_ok=True)
            if f.exists():
                die(f"two source files map to {dest}")
            f.write_text(text, encoding="utf-8")
            per_map[map_of(d.path)] = per_map.get(map_of(d.path), 0) + 1

        print("== cartographer plan ==")
        print(f"kb clone         {clone}")
        print(f"files loaded     {len(docs)} (listed {len(paths)}, missing {len(missing)}, invalid {len(errors)}, refused {len(refused)})")
        print(f"maps             {', '.join(f'{k}: {v}' for k, v in sorted(per_map.items()))}")
        print(f"links            kept {lstats.kept}, to plain text {lstats.plain} (customers/people {lstats.sensitive}), external {lstats.external}")

        cmd = [binary, "import", "--source", str(stage), "--kb", str(clone)]
        for m in sorted(per_map):
            cmd += ["--map", f"{m}={m}"]
        if args.overwrite:
            cmd.append("--overwrite")
        if args.dry_run:
            cmd.append("--dry-run")
        else:
            cmd += ["--message", args.message or f"import: {SOURCE_REPO} sample ({len(docs)} files)"]
        head_before = git(clone, "rev-parse", "HEAD", check=False).stdout.strip()
        r = subprocess.run(cmd, capture_output=True, text=True)
        out = (r.stdout + r.stderr).replace(str(stage), "<stage>")
        print("\n-- cartographer import output --")
        print(out.rstrip())
        if r.returncode != 0:
            print(f"\ncartographer import exited {r.returncode}", file=sys.stderr)
            return 1

        written = 0
        link_drift = 0
        if not args.dry_run:
            link_re = re.compile(r"\]\(([^)\s]+)")
            for d in docs:
                rel = kb_path_of(d.path)
                kbf = clone / "data" / rel
                if kbf.is_file():
                    written += 1
                    a = link_re.findall((stage / rel).read_text(encoding="utf-8"))
                    b = link_re.findall(kbf.read_text(encoding="utf-8"))
                    link_drift += a != b

    head_after = git(clone, "rev-parse", "HEAD", check=False).stdout.strip()
    pushed = False
    if args.push and not args.dry_run:
        branch = git(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        p = git(clone, "push", "origin", f"HEAD:{branch}", check=False)
        if p.returncode != 0:
            print(p.stderr.strip(), file=sys.stderr)
            die("git push failed (pull --rebase in the clone, then re-run with --push)")
        pushed = True

    print("\n== cartographer summary ==" + (" (dry run, nothing written)" if args.dry_run else ""))
    print(f"staged files     {len(docs)}")
    if not args.dry_run:
        print(f"present in KB    {written} of {len(docs)} under data/<map>/")
        print(f"link drift       {link_drift} files whose links import changed (expected 0)")
        print(f"new commit       {'yes ' + head_after[:12] if head_after != head_before else 'no (nothing new)'}")
        print(f"pushed           {'yes' if pushed else 'no'}")
    bad = len(missing) + len(errors) + len(refused)
    return 1 if bad else 0


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", default=str(DEFAULT_LIST), help="file list (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download the listed files with gh")
    f.add_argument("--out", required=True, help="output dir (outside this repo)")
    f.add_argument("--repo", default=SOURCE_REPO)
    f.add_argument("--ref", default="", help="branch, tag or commit (default: the repo's default branch)")
    f.set_defaults(func=cmd_fetch)

    a = sub.add_parser("agentmemory", help="import into an agentmemory server")
    a.add_argument("--src", required=True, help="dir written by fetch")
    a.add_argument("--url", required=True, help="server base URL, e.g. http://localhost:3111")
    a.add_argument("--secret-env", default="AGENTMEMORY_SECRET", help="env var holding the bearer secret")
    a.add_argument("--state", default="", help="state file (default: $XDG_STATE_HOME/agent-memory-import/agentmemory-<url hash>.json)")
    a.add_argument("--no-share", action="store_true", help="do not team-share the memories")
    a.add_argument("--dry-run", action="store_true", help="plan only; reads the server when the secret is set")
    a.set_defaults(func=cmd_agentmemory)

    c = sub.add_parser("cartographer", help="import into a local clone of the Cartographer KB")
    c.add_argument("--src", required=True, help="dir written by fetch")
    c.add_argument("--kb-clone", required=True, help="local clone of the KB repo (initialized KB)")
    c.add_argument("--bin", default="", help="cartographer binary (default: $CARTOGRAPHER_BIN or cartographer on PATH)")
    c.add_argument("--overwrite", action="store_true", help="replace concepts that already exist (re-import changed files)")
    c.add_argument("--message", default="", help="commit message")
    c.add_argument("--push", action="store_true", help="git push the import commit")
    c.add_argument("--allow-public", action="store_true", help="allow pushing to a public GitHub repo (never for company content)")
    c.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    c.set_defaults(func=cmd_cartographer)

    args = ap.parse_args(argv)
    if sys.version_info < (3, 10):
        die("Python 3.10+ is required")
    try:
        return args.func(args)
    except ImportError_ as e:
        die(str(e))
    return 2


if __name__ == "__main__":
    sys.exit(main())
