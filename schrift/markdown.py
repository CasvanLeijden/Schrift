"""Parsing and rewriting of the Markdown that notes are stored in.

Task lines follow the Obsidian Tasks convention:

    - [ ] Offerte sturen 📅 2026-10-20 14:00 🆔 k3f9x
    - [x] Agenda delen ✅ 2026-10-14 🆔 p2m7q
"""

import re
import secrets
import time
from dataclasses import dataclass

import yaml

DUE = "\U0001f4c5"  # 📅
DONE = "✅"  # ✅
ID = "\U0001f194"  # 🆔

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
TASK_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<bullet>[-*+]) \[(?P<mark>[ xX])\](?: (?P<rest>.*))?$")
DUE_RE = re.compile(DUE + r"️?\s*(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?")
DONE_RE = re.compile(DONE + r"️?\s*(\d{4}-\d{2}-\d{2})")
ID_RE = re.compile(ID + r"️?\s*([A-Za-z0-9_-]+)")
WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]+)(?:[#|][^\[\]\n]*)?\]\]")
HEADING_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*(```|~~~)")
ATTACHMENT_RE = re.compile(r"/attachments/([0-9A-Z]{26})")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SHORT_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def new_id() -> str:
    """A ULID: 48 bits of milliseconds plus 80 random bits, Crockford base32."""
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def new_short_id(length: int = 5) -> str:
    return "".join(secrets.choice(_SHORT_ALPHABET) for _ in range(length))


# --- frontmatter ---------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, text[match.end() :]


def join_frontmatter(meta: dict, body: str) -> str:
    if not meta:
        return body
    dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=None)
    return f"---\n{dumped}---\n{body}"


def normalize_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    if not isinstance(tags, list):
        return []
    seen: list[str] = []
    for tag in tags:
        name = str(tag).strip().lstrip("#").strip()
        if name and name.lower() not in (t.lower() for t in seen):
            seen.append(name)
    return seen


# --- title ------------------------------------------------------------------------


def extract_title(body: str) -> str | None:
    in_fence = False
    for line in body.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            match = HEADING_RE.match(line)
            if match:
                return match.group(1).strip()
    return None


def set_title(body: str, title: str) -> str:
    """Replace the first H1, or add one at the top."""
    lines = body.splitlines(keepends=True)
    in_fence = False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and HEADING_RE.match(line.rstrip("\r\n")):
            ending = "\n" if line.endswith("\n") else ""
            lines[i] = f"# {title}{ending}"
            return "".join(lines)
    return f"# {title}\n\n{body.lstrip()}" if body.strip() else f"# {title}\n"


# --- tasks ------------------------------------------------------------------------


@dataclass
class ParsedTask:
    line: int
    indent: str
    bullet: str
    done: bool
    title: str
    due: str | None  # "YYYY-MM-DD" or "YYYY-MM-DD HH:MM"
    done_on: str | None
    short_id: str | None


def _code_lines(lines: list[str]) -> set[int]:
    inside: set[int] = set()
    in_fence = False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            inside.add(i)
        elif in_fence:
            inside.add(i)
    return inside


def parse_task_line(line: str, index: int = 0) -> ParsedTask | None:
    match = TASK_RE.match(line.rstrip("\r\n"))
    if not match:
        return None
    rest = match.group("rest") or ""
    due = None
    if m := DUE_RE.search(rest):
        due = m.group(1) + (
            f" {int(m.group(2).split(':')[0]):02d}:{m.group(2)[-2:]}" if m.group(2) else ""
        )
    done_on = m.group(1) if (m := DONE_RE.search(rest)) else None
    short_id = m.group(1) if (m := ID_RE.search(rest)) else None
    title = ID_RE.sub("", DONE_RE.sub("", DUE_RE.sub("", rest)))
    title = re.sub(r"\s{2,}", " ", title).strip()
    return ParsedTask(
        line=index,
        indent=match.group("indent"),
        bullet=match.group("bullet"),
        done=match.group("mark") != " ",
        title=title,
        due=due,
        done_on=done_on,
        short_id=short_id,
    )


def format_task_line(task: ParsedTask) -> str:
    parts = [f"{task.indent}{task.bullet} [{'x' if task.done else ' '}] {task.title}".rstrip()]
    if task.due:
        parts.append(f"{DUE} {task.due}")
    if task.done and task.done_on:
        parts.append(f"{DONE} {task.done_on}")
    if task.short_id:
        parts.append(f"{ID} {task.short_id}")
    return " ".join(parts)


def parse_tasks(body: str) -> list[ParsedTask]:
    lines = body.split("\n")
    skip = _code_lines(lines)
    tasks = []
    for i, line in enumerate(lines):
        if i in skip:
            continue
        task = parse_task_line(line, i)
        if task and task.title:
            tasks.append(task)
    return tasks


def assign_task_ids(body: str, taken: set[str]) -> str:
    """Give every task line without a 🆔 a fresh one, and fix duplicates within the note."""
    lines = body.split("\n")
    skip = _code_lines(lines)
    seen: set[str] = set()
    changed = False
    for i, line in enumerate(lines):
        if i in skip:
            continue
        task = parse_task_line(line, i)
        if not task or not task.title:
            continue
        if task.short_id and task.short_id not in seen:
            seen.add(task.short_id)
            continue
        short_id = new_short_id()
        while short_id in taken or short_id in seen:
            short_id = new_short_id()
        task.short_id = short_id
        seen.add(short_id)
        lines[i] = format_task_line(task)
        changed = True
    return "\n".join(lines) if changed else body


def find_task(body: str, short_id: str) -> ParsedTask | None:
    for task in parse_tasks(body):
        if task.short_id == short_id:
            return task
    return None


def replace_task(body: str, task: ParsedTask) -> str:
    lines = body.split("\n")
    lines[task.line] = format_task_line(task)
    return "\n".join(lines)


def remove_task(body: str, task: ParsedTask) -> str:
    lines = body.split("\n")
    del lines[task.line]
    return "\n".join(lines)


def append_task(body: str, task: ParsedTask, under_heading: str | None = None) -> str:
    """Append a task after the last task of the given section (or the note)."""
    line = format_task_line(task)
    lines = body.rstrip("\n").split("\n") if body.strip() else []
    if under_heading:
        start = None
        for i, text in enumerate(lines):
            if re.match(r"^#{1,6}\s+" + re.escape(under_heading) + r"\s*$", text, re.IGNORECASE):
                start = i
                break
        if start is None:
            lines += ["", f"## {under_heading}", "", line]
            return "\n".join(lines) + "\n"
        end = start + 1
        while end < len(lines) and not re.match(r"^#{1,6}\s", lines[end]):
            end += 1
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, line)
        return "\n".join(lines) + "\n"
    last_task = max((t.line for t in parse_tasks("\n".join(lines))), default=None)
    if last_task is None:
        if lines:
            lines.append("")
        lines.append(line)
    else:
        lines.insert(last_task + 1, line)
    return "\n".join(lines) + "\n"


# --- links and text -----------------------------------------------------------------


def parse_wikilinks(body: str) -> list[str]:
    seen: list[str] = []
    for match in WIKILINK_RE.finditer(body):
        target = match.group(1).strip()
        if target and target.lower() not in (s.lower() for s in seen):
            seen.append(target)
    return seen


def attachment_ids(body: str) -> list[str]:
    return list(dict.fromkeys(ATTACHMENT_RE.findall(body)))


def plain_text(body: str) -> str:
    """Rough Markdown-to-text for search indexing and snippets."""
    text = re.sub(r"<[^>]+>", " ", body)
    text = DUE_RE.sub(lambda m: m.group(1), text)
    text = ID_RE.sub("", DONE_RE.sub("", text))
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = WIKILINK_RE.sub(lambda m: m.group(1), text)
    text = re.sub(r"^[ \t]*([-*+]|\d+\.) (\[[ xX]\] )?", "", text, flags=re.MULTILINE)
    text = re.sub(r"[#*_`>~|]", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", title).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return (name or "Naamloos")[:120]


def split_title(body: str) -> tuple[str | None, str]:
    """Split a leading H1 off the body: ("Title", "rest"). Used by the editor page."""
    stripped = body.lstrip("\n")
    first, _, rest = stripped.partition("\n")
    match = HEADING_RE.match(first)
    if not match:
        return None, body
    return match.group(1).strip(), rest.lstrip("\n")


def join_title(title: str, content: str) -> str:
    content = content.strip("\n")
    return f"# {title.strip()}\n\n{content}\n" if content else f"# {title.strip()}\n"


def normalize_editor_markdown(text_: str) -> str:
    """Undo escaping that some editors add to our own syntax."""
    text_ = re.sub(r"\\\[\\\[([^\[\]\n]+?)\\\]\\\]", r"[[\1]]", text_)
    return text_
