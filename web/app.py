#!/usr/bin/env python3
"""
Simple Flask wiki browser for karpathy_kb.

Usage:
    cd /home/adrian/code/karpathy_kb
    python3 web/app.py
    # Open http://localhost:5000
"""

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from collections import defaultdict

import anthropic
import markdown2
from flask import Flask, render_template, request, abort, redirect, url_for, session, send_from_directory
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).parent.parent
WIKI_DIR = BASE_DIR / "wiki"
PERSONAL_DIR = BASE_DIR / "personal"
STATS_FILE = BASE_DIR / "stats" / "stats.json"
CLASSIFIED_FILE = BASE_DIR / "classified.jsonl"
CONVERSATIONS_FILE = BASE_DIR / "conversations.json"
QUESTIONS_NOTES_FILE = BASE_DIR / "questions_notes.json"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {".json", ".jsonl", ".md", ".txt"}

# Module-level caches for large source files (loaded once on first use)
_classified_cache = None
_conversations_cache = None

app = Flask(__name__)
app.secret_key = os.urandom(24)

IMPROVE_MODEL = "claude-sonnet-4-6"

BASE_IMPROVE_PROMPT = """\
You are editing a personal knowledge base wiki page. Your job is to make it more \
accurate, specific, and useful — not longer for its own sake.

Context: this page was synthesized from the owner's ChatGPT conversations. They are a \
developer/entrepreneur/photographer with projects including mQail (modular AI agents), \
Ouroboros (self-improving agent), family-it-helpdesk (KCS-based help desk), and interests \
in photography, home networking, and AI systems.

## Page to improve:
{current_content}

## Rules — read carefully:

**Output:** Return ONLY the complete updated markdown page. No preamble, no explanation, \
no "I've improved...", no meta-commentary. The output should look exactly like a wiki page, \
nothing else.

**Voice:** Preserve the existing tone and structure. Do not rewrite sections that are \
already clear. Do not convert prose to bullet lists or vice versa without good reason.

**Edits to make:**
- Fix vague or incomplete claims by making them specific (versions, dates, names, outcomes)
- Sharpen "Open Questions" — replace generic questions with ones that reflect real \
  decisions or unresolved tensions evident in the page
- If there are clear patterns of what worked or failed, add or improve a "Lessons Learned" section
- Add a "Connections" section only if there are genuine links to the owner's other projects \
  or interests — not generic topic associations
- Remove filler content: obvious statements, redundant bullet points, vague aspirations
- In "Tools & Resources": use markdown links `[Name](url)` for external resources. \
  Use `[[Page Name]]` for internal wiki cross-references (e.g. `[[AI Alignment]]`). \
  Never list a resource as plain unlinked text.

**What NOT to do:**
- Do not invent projects, tools, or outcomes not already evidenced in the page
- Do not add sections just to add sections
- Do not announce or describe your edits anywhere in the output
- Do not use phrases like "this page explores", "it is worth noting", or "in conclusion"

**If web search is enabled:** Use it to verify specific facts (tool versions, project status, \
dates). Integrate findings directly into the content — do not write "according to my search" \
or reference the search in any way.\
"""


def load_stats():
    if STATS_FILE.exists():
        return json.loads(STATS_FILE.read_text())
    return {}


def _page_display_name(stem):
    """Normalize a file stem to a human-readable title."""
    return stem.replace("_", " ").replace("-", " ").title()


def _norm_key(cat, stem):
    """Dedup key: category + stem with hyphens/underscores collapsed."""
    return cat + "/" + re.sub(r"[-_]+", "_", stem.lower())


def get_wiki_pages():
    """Return all wiki pages grouped by category, deduplicated by normalized name."""
    if not WIKI_DIR.exists():
        return {}
    # First pass: collect all candidates, keep largest file per normalized key
    best = {}  # norm_key -> (path, rel, cat, size)
    for p in sorted(WIKI_DIR.rglob("*.md")):
        if p.name in ("index.md", "log.md"):
            continue
        if ".__" in p.name:
            continue
        rel = p.relative_to(WIKI_DIR)
        parts = rel.parts
        cat = parts[0] if len(parts) > 1 else "other"
        size = p.stat().st_size
        key = _norm_key(cat, p.stem)
        if key not in best or size > best[key][3]:
            best[key] = (p, rel, cat, size)

    pages = defaultdict(list)
    for p, rel, cat, size in sorted(best.values(), key=lambda x: str(x[1])):
        pages[cat].append({
            "name": _page_display_name(p.stem),
            "path": str(rel.with_suffix("")),
            "size": len(p.read_text().split()),
        })
    return dict(sorted(pages.items()))


def group_by_prefix(pages):
    """Group a category's pages by first word of stem (before first _ or -).
    Groups with only 1 page are bucketed into 'other'."""
    groups = defaultdict(list)
    for p in pages:
        stem = p['path'].split('/')[-1]
        prefix = re.split(r'[_\-]', stem)[0].lower()
        groups[prefix].append(p)
    result = {}
    other = []
    for prefix, grp in sorted(groups.items()):
        if len(grp) >= 2:
            result[prefix] = grp
        else:
            other.extend(grp)
    if other:
        result['other'] = sorted(other, key=lambda p: p['name'])
    return result


def get_grouped_pages():
    """Return {cat: {prefix: [pages]}} for all categories."""
    pages = get_wiki_pages()
    return {cat: group_by_prefix(cat_pages) for cat, cat_pages in pages.items()}


def get_related_pages(page_path: str, all_pages: dict) -> list[dict]:
    """Return related pages: [[wiki links]] in page + exact-stem matches in other categories + same-prefix siblings."""
    parts = page_path.split("/")
    if len(parts) < 2:
        return []
    current_cat = parts[0]
    stem = re.sub(r'[-_]+', '_', parts[-1].lower())
    prefix = re.split(r'[_\-]', parts[-1])[0].lower()

    # Build lookup for resolving [[links]]
    lookup = _build_page_lookup(all_pages)

    related = []
    seen = {page_path}

    # 1. Explicit [[wiki links]] in the page content
    page_file = WIKI_DIR / (page_path + ".md")
    if page_file.exists():
        text = page_file.read_text()
        for link_title in re.findall(r'\[\[([^\]]+)\]\]', text):
            resolved = lookup.get(_normalize_link(link_title))
            if resolved and resolved not in seen:
                # Find the full page object
                for cat_pages in all_pages.values():
                    for p in cat_pages:
                        if p['path'] == resolved:
                            related.append({**p, 'relevance': 'linked'})
                            seen.add(resolved)
                            break

    # 2. Exact stem match in other categories
    for cat, cat_pages in all_pages.items():
        for p in cat_pages:
            if p['path'] in seen:
                continue
            p_last = p['path'].split('/')[-1]
            p_stem = re.sub(r'[-_]+', '_', p_last.lower())
            p_prefix = re.split(r'[_\-]', p_last)[0].lower()
            if cat != current_cat and p_stem == stem:
                related.append({**p, 'relevance': 'exact'})
                seen.add(p['path'])
            elif cat == current_cat and p_prefix == prefix and p_last != parts[-1]:
                related.append({**p, 'relevance': 'sibling'})
                seen.add(p['path'])

    related.sort(key=lambda p: (0 if p['relevance'] == 'linked' else 1 if p['relevance'] == 'exact' else 2, p['name']))
    return related[:20]


def _load_classified() -> list:
    global _classified_cache
    if _classified_cache is None:
        items = []
        if CLASSIFIED_FILE.exists():
            with open(CLASSIFIED_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            items.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        _classified_cache = items
    return _classified_cache


def _load_conversations() -> dict:
    global _conversations_cache
    if _conversations_cache is None:
        if CONVERSATIONS_FILE.exists():
            with open(CONVERSATIONS_FILE) as f:
                convos = json.load(f)
            _conversations_cache = {c["id"]: c for c in convos}
        else:
            _conversations_cache = {}
    return _conversations_cache


def _extract_convo_text(convo: dict, max_chars: int = 3000) -> str:
    """Extract readable conversation text, truncated."""
    msgs = []
    for node in convo.get("mapping", {}).values():
        msg = node.get("message")
        if not msg:
            continue
        role = msg.get("author", {}).get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", {})
        if content.get("content_type") == "text":
            parts = content.get("parts", [])
            text = " ".join(p for p in parts if isinstance(p, str)).strip()
            if text:
                msgs.append((msg.get("create_time", 0), role, text))
    msgs.sort(key=lambda x: x[0])
    result, chars = [], 0
    for _, role, text in msgs:
        if chars >= max_chars:
            result.append("[...truncated]")
            break
        prefix = "User: " if role == "user" else "AI: "
        snippet = text[:max_chars - chars]
        result.append(f"{prefix}{snippet}")
        chars += len(snippet)
    return "\n".join(result)


def extract_questions() -> list[dict]:
    """Extract all Open Questions from every wiki page."""
    questions = []
    for p in sorted(WIKI_DIR.rglob("*.md")):
        if p.name in ("index.md", "log.md") or ".__" in p.name or ".bak" in p.name:
            continue
        rel = p.relative_to(WIKI_DIR)
        cat = rel.parts[0] if len(rel.parts) > 1 else "other"
        mtime = p.stat().st_mtime
        page_path = str(rel.with_suffix(""))
        page_name = _page_display_name(p.stem)

        in_questions = False
        for line in p.read_text().splitlines():
            if re.match(r'##\s+open\s+questions', line, re.IGNORECASE):
                in_questions = True
                continue
            if in_questions:
                if line.startswith('##'):
                    break
                m = re.match(r'^[-*]\s+(.+)', line.strip())
                if m:
                    text = m.group(1).strip()
                    if text:
                        qid = hashlib.md5(
                            f"{page_path}::{text}".encode()
                        ).hexdigest()[:12]
                        questions.append({
                            "id": qid,
                            "text": text,
                            "page_path": page_path,
                            "page_name": page_name,
                            "category": cat,
                            "modified": mtime,
                        })
    return questions


def load_question_notes() -> dict:
    if QUESTIONS_NOTES_FILE.exists():
        return json.loads(QUESTIONS_NOTES_FILE.read_text())
    return {}


def save_question_note(qid: str, note: str):
    notes = load_question_notes()
    if note.strip():
        notes[qid] = {
            "note": note.strip(),
            "updated": datetime.datetime.now().isoformat(),
        }
    else:
        notes.pop(qid, None)
    QUESTIONS_NOTES_FILE.write_text(json.dumps(notes, indent=2))


def get_source_conversations(page_path: str, max_convos: int = 10) -> list[dict]:
    """Load highest-value source conversations for a wiki page from classified.jsonl."""
    norm_path = re.sub(r'[-_]+', '_', page_path.lower())
    matches = []
    for item in _load_classified():
        category = item.get("category", "")
        topics = item.get("topics", [])
        key = f"{category}/{topics[0].lower().replace(' ', '_')}" if topics else category
        if re.sub(r'[-_]+', '_', key.lower()) == norm_path:
            matches.append(item)
    if not matches:
        return []
    matches.sort(key=lambda x: x.get("value", 0), reverse=True)
    convos = _load_conversations()
    result = []
    for item in matches[:max_convos]:
        convo = convos.get(item.get("id", ""))
        if convo:
            ts = item.get("create_time", 0)
            date = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "unknown"
            result.append({
                "summary": item.get("summary", ""),
                "value": item.get("value", 0),
                "date": date,
                "text": _extract_convo_text(convo, max_chars=3000),
            })
    return result


def _normalize_link(s: str) -> str:
    """Normalize a wiki link title to a lookup key."""
    s = s.lower()
    s = re.sub(r'[&+]', '_and_', s)
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'[\s\-_]+', '_', s)
    return s.strip('_')


def _build_page_lookup(pages: dict) -> dict:
    """Build a normalized-name → path lookup from all wiki pages."""
    lookup = {}
    for cat_pages in pages.values():
        for p in cat_pages:
            # Index by display name and by stem
            for key in (_normalize_link(p['name']), _normalize_link(p['path'].split('/')[-1])):
                if key and key not in lookup:
                    lookup[key] = p['path']
    return lookup


def _resolve_wiki_links(text: str, pages: dict) -> str:
    """Convert [[Page Name]] wiki links to markdown links.
    Falls back to n-gram matching when exact title doesn't match a page."""
    lookup = _build_page_lookup(pages)

    def find_path(title: str) -> str | None:
        # 1. Exact normalized match
        path = lookup.get(_normalize_link(title))
        if path:
            return path
        # 2. Try progressively shorter word n-grams (longest first)
        words = [w for w in re.split(r'[\s_&+,/]+', title.lower()) if len(w) > 2]
        for n in range(len(words), 1, -1):
            for i in range(len(words) - n + 1):
                candidate = '_'.join(words[i:i + n])
                if candidate in lookup:
                    return lookup[candidate]
        # 3. Single significant word only when it's the whole title
        if len(words) == 1 and words[0] in lookup:
            return lookup[words[0]]
        return None

    def replace(m):
        title = m.group(1).strip()
        path = find_path(title)
        if path:
            return f'[{title}](/wiki/{path})'
        return title  # plain text, no broken brackets

    return re.sub(r'\[\[([^\]]+)\]\]', replace, text)


def render_md(text, with_toc=False):
    extras = ["fenced-code-blocks", "tables", "header-ids", "strike", "task_list"]
    if with_toc:
        extras.append("toc")
    result = markdown2.markdown(text, extras=extras)
    if with_toc:
        return str(result), result.toc_html or ""
    return str(result)


@app.route("/")
def index():
    stats = load_stats()
    pages = get_wiki_pages()
    grouped_pages = get_grouped_pages()
    total_pages = sum(len(v) for v in pages.values())
    return render_template("index.html", stats=stats, pages=pages,
                           grouped_pages=grouped_pages, total_pages=total_pages,
                           active_cat=None, active_prefix=None)


@app.route("/wiki")
def wiki_index():
    pages = get_wiki_pages()
    grouped_pages = get_grouped_pages()
    return render_template("wiki.html", pages=pages, grouped_pages=grouped_pages,
                           page=None, content=None, title="Wiki",
                           active_cat=None, active_prefix=None, see_also=[])


@app.route("/wiki/category/<cat>")
def wiki_category(cat):
    pages = get_wiki_pages()
    grouped_pages = get_grouped_pages()
    all_cat_pages = pages.get(cat, [])
    grouped = grouped_pages.get(cat, {})
    prefix = request.args.get("prefix", "").strip().lower()
    cat_pages = grouped.get(prefix, all_cat_pages) if prefix else all_cat_pages
    return render_template("wiki_category.html", pages=pages, grouped_pages=grouped_pages,
                           cat=cat, cat_pages=cat_pages, grouped=grouped,
                           total_count=len(all_cat_pages), active_prefix=prefix,
                           active_cat=cat, title=cat.title())


@app.route("/wiki/<path:page_path>")
def wiki_page(page_path):
    path = WIKI_DIR / (page_path + ".md")
    if not path.exists() or not path.resolve().is_relative_to(WIKI_DIR.resolve()):
        abort(404)
    text = path.read_text()
    pages = get_wiki_pages()
    text = _resolve_wiki_links(text, pages)
    content, toc = render_md(text, with_toc=True)
    title = _page_display_name(path.stem)
    grouped_pages = get_grouped_pages()
    active_cat = page_path.split("/")[0]
    # Find see-also: same normalized stem in other categories
    stem = re.sub(r'[-_]+', '_', path.stem.lower())
    see_also = []
    for cat, cat_pages in pages.items():
        if cat == active_cat:
            continue
        for p in cat_pages:
            p_stem = re.sub(r'[-_]+', '_', p['path'].split('/')[-1].lower())
            if p_stem == stem:
                see_also.append(p)
    return render_template("wiki.html", pages=pages, grouped_pages=grouped_pages,
                           page=page_path, content=content, toc=toc, title=title,
                           active_cat=active_cat, active_prefix=None, see_also=see_also)


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    results = []
    if q and len(q) >= 2:
        words = q.lower().split()
        for p in sorted(WIKI_DIR.rglob("*.md")):
            if p.name in ("index.md", "log.md") or ".__" in p.name:
                continue
            text = p.read_text()
            lower = text.lower()
            title = _page_display_name(p.stem).lower()
            # All words must appear in title or content
            if not all(w in lower or w in title for w in words):
                continue
            rel = p.relative_to(WIKI_DIR)
            title_match = all(w in title for w in words)
            rank = 0 if title_match else 1
            # Snippet around first word
            idx = lower.find(words[0])
            start = max(0, idx - 80)
            end = min(len(text), idx + 220)
            snippet = text[start:end].replace("\n", " ")
            for w in words:
                pattern = re.compile(re.escape(w), re.IGNORECASE)
                snippet = pattern.sub(f"<mark>{w}</mark>", snippet)
            results.append({
                "path": str(rel.with_suffix("")),
                "name": _page_display_name(p.stem),
                "category": rel.parts[0] if len(rel.parts) > 1 else "other",
                "snippet": snippet,
                "rank": rank,
                "title_match": title_match,
            })
        results.sort(key=lambda r: (r["rank"], r["name"]))
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    pages = get_wiki_pages()
    grouped_pages = get_grouped_pages()
    return render_template("search.html", q=q, results=results,
                           by_cat=dict(by_cat), pages=pages,
                           grouped_pages=grouped_pages,
                           active_cat=None, active_prefix=None)


@app.route("/personal")
def personal():
    summary_path = PERSONAL_DIR / "summary.md"
    content = None
    if summary_path.exists():
        content = render_md(summary_path.read_text())
    pages = get_wiki_pages()
    grouped_pages = get_grouped_pages()
    return render_template("wiki.html", pages=pages, grouped_pages=grouped_pages,
                           page="personal/summary", content=content,
                           title="Personal Summary (Private)",
                           active_cat=None, active_prefix=None, see_also=[])


@app.route("/log")
def log():
    log_path = WIKI_DIR / "log.md"
    content = None
    if log_path.exists():
        content = render_md(log_path.read_text())
    pages = get_wiki_pages()
    grouped_pages = get_grouped_pages()
    return render_template("wiki.html", pages=pages, grouped_pages=grouped_pages,
                           page=None, content=content, title="Ingest Log",
                           active_cat=None, active_prefix=None, see_also=[])


@app.route("/wiki/<path:page_path>/improve", methods=["GET"])
def improve_form(page_path):
    path = WIKI_DIR / (page_path + ".md")
    if not path.exists() or not path.resolve().is_relative_to(WIKI_DIR.resolve()):
        abort(404)
    current = path.read_text()
    title = path.stem.replace("_", " ").title()
    prompt = BASE_IMPROVE_PROMPT.format(current_content=current)
    pages = get_wiki_pages()
    grouped_pages = get_grouped_pages()
    related_pages = get_related_pages(page_path, pages)
    has_source_data = CLASSIFIED_FILE.exists() and CONVERSATIONS_FILE.exists()
    return render_template("improve.html", page=page_path, title=title,
                           prompt=prompt, pages=pages, grouped_pages=grouped_pages,
                           active_cat=page_path.split("/")[0], active_prefix=None,
                           related_pages=related_pages, has_source_data=has_source_data)


@app.route("/wiki/<path:page_path>/improve/run", methods=["POST"])
def improve_run(page_path):
    path = WIKI_DIR / (page_path + ".md")
    if not path.exists() or not path.resolve().is_relative_to(WIKI_DIR.resolve()):
        abort(404)

    user_prompt = request.form.get("prompt", "").strip()
    custom = request.form.get("custom", "").strip()
    use_search = request.form.get("web_search") == "on"
    use_ref_pages = request.form.get("ref_pages") == "on"
    use_src_convos = request.form.get("src_convos") == "on"

    if custom:
        user_prompt += f"\n\n## Additional instructions:\n{custom}"

    original = path.read_text()
    title = path.stem.replace("_", " ").title()

    # Build augmented prompt
    full_prompt = user_prompt

    if use_ref_pages:
        all_pages = get_wiki_pages()
        related = get_related_pages(page_path, all_pages)
        if related:
            lines = [f"- {p['path']} ({p['name']})" for p in related]
            full_prompt += (
                "\n\n## Related wiki pages\n"
                "Use the `read_wiki_page` tool to read any before improving — "
                "to add cross-references, avoid duplication, or understand context:\n"
                + "\n".join(lines)
            )

    if use_src_convos:
        src = get_source_conversations(page_path, max_convos=10)
        if src:
            blocks = []
            for i, c in enumerate(src, 1):
                blocks.append(
                    f"[{i}] {c['date']} (value: {c['value']})\n"
                    f"Summary: {c['summary']}\n{c['text']}\n---"
                )
            full_prompt += (
                f"\n\n## Source conversations ({len(src)} highest-value conversations "
                "that contributed to this page)\n"
                "Use these to add specific details, correct vague claims, and recover "
                "insights lost in the original synthesis:\n\n"
                + "\n\n".join(blocks)
            )

    try:
        client = anthropic.Anthropic()

        tools = []
        if use_search:
            tools.append({"type": "web_search_20250305", "name": "web_search"})
        if use_ref_pages:
            tools.append({
                "name": "read_wiki_page",
                "description": (
                    "Read the full content of another wiki page for context. "
                    "Use this to understand related topics, avoid duplication, "
                    "and add meaningful cross-references."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Wiki page path, e.g. 'tech/kubernetes'",
                        }
                    },
                    "required": ["path"],
                },
            })

        kwargs = dict(model=IMPROVE_MODEL, max_tokens=4096)
        if tools:
            kwargs["tools"] = tools

        messages = [{"role": "user", "content": full_prompt}]
        improved_text = ""
        max_rounds = 15

        for _ in range(max_rounds):
            response = client.messages.create(messages=messages, **kwargs)
            text_blocks = [b.text for b in response.content if hasattr(b, "text")]
            if text_blocks:
                improved_text = "\n".join(text_blocks).strip()

            if response.stop_reason != "tool_use":
                break

            has_web_search = any(
                getattr(b, "type", "") == "web_search_20250305"
                for b in response.content
            )
            local_tool_uses = [
                b for b in response.content
                if getattr(b, "type", "") == "tool_use"
                and getattr(b, "name", "") == "read_wiki_page"
            ]

            if not has_web_search and not local_tool_uses:
                break

            messages.append({"role": "assistant", "content": response.content})

            if local_tool_uses:
                tool_results = []
                for tu in local_tool_uses:
                    req = tu.input.get("path", "").strip("/")
                    req_path = WIKI_DIR / (req + ".md")
                    try:
                        if req_path.exists() and req_path.resolve().is_relative_to(WIKI_DIR.resolve()):
                            content = req_path.read_text()
                            if len(content) > 5000:
                                content = content[:5000] + "\n\n[...truncated]"
                            result_text = content
                        else:
                            result_text = f"Page not found: {req}"
                    except Exception:
                        result_text = f"Error reading: {req}"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": result_text,
                    })
                messages.append({"role": "user", "content": tool_results})
            # web_search is server-side: just loop, no tool_result needed

    except Exception as e:
        pages = get_wiki_pages()
        grouped_pages = get_grouped_pages()
        related_pages = get_related_pages(page_path, get_wiki_pages())
        return render_template("improve.html", page=page_path, title=title,
                               prompt=user_prompt, pages=pages, grouped_pages=grouped_pages,
                               active_cat=page_path.split("/")[0], active_prefix=None,
                               related_pages=related_pages,
                               has_source_data=CLASSIFIED_FILE.exists() and CONVERSATIONS_FILE.exists(),
                               error=f"API error: {e}")

    # Save pending result to a temp file
    pending_path = WIKI_DIR / (page_path + ".__pending__.md")
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(improved_text)

    pages = get_wiki_pages()
    grouped_pages = get_grouped_pages()
    return render_template("improve_result.html",
                           page=page_path, title=title,
                           original=render_md(original),
                           improved=render_md(improved_text),
                           improved_raw=improved_text,
                           pages=pages, grouped_pages=grouped_pages,
                           active_cat=page_path.split("/")[0], active_prefix=None)


@app.route("/wiki/<path:page_path>/improve/accept", methods=["POST"])
def improve_accept(page_path):
    path = WIKI_DIR / (page_path + ".md")
    pending_path = WIKI_DIR / (page_path + ".__pending__.md")

    if not pending_path.exists():
        abort(404)

    # Backup original
    backup_path = WIKI_DIR / (page_path + ".__backup__.md")
    if path.exists():
        backup_path.write_text(path.read_text())

    path.write_text(pending_path.read_text())
    pending_path.unlink()
    return redirect(f"/wiki/{page_path}")


@app.route("/wiki/<path:page_path>/improve/discard", methods=["POST"])
def improve_discard(page_path):
    pending_path = WIKI_DIR / (page_path + ".__pending__.md")
    if pending_path.exists():
        pending_path.unlink()
    return redirect(f"/wiki/{page_path}")


@app.route("/questions")
def questions_view():
    all_q = extract_questions()
    notes = load_question_notes()

    # Attach notes and format dates
    for q in all_q:
        note_data = notes.get(q["id"], {})
        q["note"] = note_data.get("note", "")
        q["note_updated"] = note_data.get("updated", "")
        q["modified_fmt"] = datetime.datetime.fromtimestamp(
            q["modified"]).strftime("%Y-%m-%d")

    # Feed: most recently touched pages (deduplicated by page, top 40 questions)
    seen_pages = set()
    feed = []
    for q in sorted(all_q, key=lambda x: x["modified"], reverse=True):
        feed.append(q)
        seen_pages.add(q["page_path"])
        if len(feed) >= 40:
            break

    # By category
    by_cat = defaultdict(list)
    for q in all_q:
        by_cat[q["category"]].append(q)

    pages = get_wiki_pages()
    grouped_pages = get_grouped_pages()
    return render_template(
        "questions.html",
        feed=feed,
        by_cat=dict(sorted(by_cat.items())),
        total=len(all_q),
        pages=pages,
        grouped_pages=grouped_pages,
        active_cat=None,
        active_prefix=None,
    )


@app.route("/questions/note", methods=["POST"])
def questions_note():
    qid = request.form.get("qid", "").strip()
    note = request.form.get("note", "")
    if qid:
        save_question_note(qid, note)
    return {"ok": True}


INGEST_STATUS_FILE = UPLOAD_DIR / "status.json"
_status_lock = threading.Lock()


def load_ingest_status() -> dict:
    if INGEST_STATUS_FILE.exists():
        try:
            return json.loads(INGEST_STATUS_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_ingest_status(status: dict):
    with _status_lock:
        INGEST_STATUS_FILE.write_text(json.dumps(status, indent=2))


def set_file_status(filename: str, status: str, log: str = ""):
    all_status = load_ingest_status()
    all_status[filename] = {
        "status": status,
        "updated_at": datetime.datetime.now().isoformat(),
        "log": log,
    }
    save_ingest_status(all_status)


def run_ingest_background(filename: str, filepath: Path, suffix: str):
    """Run classify + ingest pipeline in background thread."""
    log_lines = []

    def run(cmd, **kwargs):
        result = subprocess.run(cmd, capture_output=True, text=True,
                                cwd=str(BASE_DIR), **kwargs)
        log_lines.append(f"$ {' '.join(str(c) for c in cmd)}")
        log_lines.append(result.stdout[-3000:] if result.stdout else "")
        if result.stderr:
            log_lines.append("STDERR: " + result.stderr[-1000:])
        return result.returncode

    set_file_status(filename, "running")
    try:
        python = sys.executable

        if suffix == ".json":
            # classify (appends new IDs, skips existing — safe to re-run)
            rc = run([python, "classify.py", "--input", str(filepath)])
            if rc != 0:
                set_file_status(filename, "error", "\n".join(log_lines))
                return
            # ingest
            rc = run([python, "ingest.py"])
            if rc != 0:
                set_file_status(filename, "error", "\n".join(log_lines))
                return

        elif suffix == ".jsonl":
            # Treat as a pre-classified file — run ingest directly
            rc = run([python, "ingest.py", "--classified-file", str(filepath)])
            if rc != 0:
                set_file_status(filename, "error", "\n".join(log_lines))
                return

        elif suffix in (".md", ".txt"):
            # Run through Claude to clean up, pick category/slug, format as wiki page
            raw_content = filepath.read_text(encoding="utf-8", errors="replace")
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                set_file_status(filename, "error", "ANTHROPIC_API_KEY not set")
                return
            client = anthropic.Anthropic(api_key=api_key)
            prompt = f"""\
You are converting an uploaded document into a clean wiki page for a personal knowledge base.

The owner is a developer/entrepreneur interested in AI systems, multi-LLM orchestration, photography, and home tech.

Wiki categories: ai, projects, research, tech, personal

## Rules:
- Choose the most appropriate category from the list above
- Choose a short snake_case slug (e.g. llm_ensemble_deliberation)
- Remove citation artifacts like citeturn0search0, turn0search2, turn15view0 etc — strip them entirely
- Reformat as a clean wiki page with proper headings, summary section, key points
- First line of output MUST be: CATEGORY: <category>
- Second line MUST be: SLUG: <slug>
- Third line blank
- Then the full markdown page starting with # <Category Title> / <Page Title>
- No preamble, no explanation outside the page itself

## Document to convert:
{raw_content[:12000]}
"""
            msg = client.messages.create(
                model=IMPROVE_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            result_text = msg.content[0].text.strip()

            # Parse CATEGORY and SLUG from first two lines
            lines = result_text.splitlines()
            category, slug = "research", filepath.stem.replace("-", "_").replace(" ", "_")
            content_start = 0
            for i, line in enumerate(lines[:5]):
                if line.startswith("CATEGORY:"):
                    category = line.split(":", 1)[1].strip().lower()
                    content_start = i + 1
                elif line.startswith("SLUG:"):
                    slug = line.split(":", 1)[1].strip().lower()
                    content_start = i + 1

            # Skip blank line after headers
            while content_start < len(lines) and not lines[content_start].strip():
                content_start += 1

            page_content = "\n".join(lines[content_start:]).strip()

            # Save to wiki
            cat_dir = WIKI_DIR / category
            cat_dir.mkdir(exist_ok=True)
            dest = cat_dir / f"{slug}.md"
            dest.write_text(page_content + "\n")
            log_lines.append(f"Saved to {dest.relative_to(BASE_DIR)}")

        set_file_status(filename, "done", "\n".join(log_lines))
    except Exception as e:
        set_file_status(filename, "error", str(e))


@app.route("/files")
def files_library():
    # Core pipeline files always shown
    core_files = []
    for path in [CONVERSATIONS_FILE, CLASSIFIED_FILE]:
        if path.exists():
            stat = path.stat()
            core_files.append({
                "name": path.name,
                "size": stat.st_size,
                "mtime": datetime.datetime.fromtimestamp(stat.st_mtime),
                "path": str(path.relative_to(BASE_DIR)),
                "core": True,
            })

    # Uploaded files
    all_status = load_ingest_status()
    uploaded = []
    for path in sorted(UPLOAD_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_file() and path.suffix in ALLOWED_EXTENSIONS:
            stat = path.stat()
            file_status = all_status.get(path.name, {})
            uploaded.append({
                "name": path.name,
                "size": stat.st_size,
                "mtime": datetime.datetime.fromtimestamp(stat.st_mtime),
                "suffix": path.suffix,
                "status": file_status.get("status", ""),
                "status_log": file_status.get("log", ""),
                "core": False,
            })

    return render_template("files.html", core_files=core_files, uploaded=uploaded,
                           title="Files", active_cat=None, active_prefix=None,
                           pages=get_wiki_pages(), grouped_pages=get_grouped_pages())


@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return redirect(url_for("files_library"))
    f = request.files["file"]
    if not f.filename:
        return redirect(url_for("files_library"))

    suffix = Path(f.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return render_template("files.html",
                               error=f"File type '{suffix}' not allowed. Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
                               core_files=[], uploaded=[],
                               title="Files", active_cat=None, active_prefix=None,
                               pages=get_wiki_pages(), grouped_pages=get_grouped_pages())

    filename = secure_filename(f.filename)
    # Avoid silent overwrites — append timestamp if name already exists
    dest = UPLOAD_DIR / filename
    if dest.exists():
        stem = Path(filename).stem
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{stem}_{ts}{suffix}"
        dest = UPLOAD_DIR / filename

    f.save(dest)
    return redirect(url_for("files_library"))


@app.route("/uploads/<filename>")
def download_file(filename):
    safe = secure_filename(filename)
    return send_from_directory(UPLOAD_DIR, safe, as_attachment=True)


@app.route("/ingest/<filename>", methods=["POST"])
def ingest_file(filename):
    safe = secure_filename(filename)
    filepath = UPLOAD_DIR / safe
    if not filepath.exists():
        abort(404)
    suffix = filepath.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        abort(400)

    # Don't re-run if already running
    status = load_ingest_status().get(safe, {}).get("status", "")
    if status == "running":
        return redirect(url_for("files_library"))

    set_file_status(safe, "queued")
    t = threading.Thread(target=run_ingest_background, args=(safe, filepath, suffix), daemon=True)
    t.start()
    return redirect(url_for("files_library"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5000)
