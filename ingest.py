#!/usr/bin/env python3
"""
Phase 2: Build the wiki from classified conversations.

Reads classified.jsonl (post-review), groups conversations by topic,
and calls Claude (or OpenRouter) to write/update wiki pages.

Resume-safe: tracks ingested conversation IDs in wiki/log.md.

Usage:
    # Anthropic (Haiku)
    python3 ingest.py [--dry-run] [--category tech] [--min-value 3]

    # OpenRouter (DeepSeek V3 or other)
    python3 ingest.py --openrouter [--model deepseek/deepseek-v3]

    # Personal summary
    python3 ingest.py --openrouter --personal
"""

import json
import argparse
import datetime
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import anthropic
from tqdm import tqdm

_write_lock = Lock()

CONVERSATIONS_FILE = "conversations.json"
CLASSIFIED_FILE = "classified.jsonl"
WIKI_DIR = Path("wiki")
PERSONAL_DIR = Path("personal")

# Anthropic fallback models
WIKI_MODEL = "claude-haiku-4-5-20251001"
SYNTH_MODEL = "claude-sonnet-4-6"

# OpenRouter defaults
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "deepseek/deepseek-chat-v3.1"

INGEST_PROMPT = """You are maintaining a personal knowledge base wiki.

The user is a person who has been using ChatGPT since 2023. You are building wiki pages
that capture their durable knowledge, projects, and interests from their conversations.

## Current wiki page content (may be empty if new):
{current_content}

## New conversations to integrate (category: {category}, topic: {topic}):
{conversations}

## Instructions:
Update (or create) the wiki page for "{topic}" by integrating knowledge from these conversations.
- Synthesize, don't just list. Find patterns, note what the user tried, what worked.
- Keep it concise but information-dense. This is a reference, not an essay.
- Preserve existing content that's still relevant; update or remove stale claims.
- Do NOT include sensitive personal details (health, relationships, specific names of people).
- Use the format from the schema (Summary, Key Points, Tools & Resources, Projects & Experiments, Open Questions, Related).
- In "Tools & Resources": use markdown links `[Name](url)` for any external tools, papers, or frameworks. Use `[[Page Name]]` for internal cross-references to other wiki topics. Never list a resource as plain unlinked text.
- In "Related": always use `[[Page Name]]` syntax for internal wiki links.

## Voice and provenance — read carefully:
Default to neutral framing that records what was explored, not what the user believes:
- Prefer: "discussion explored X", "the topic of X was covered", "this area includes X"
- Use "the user built/used/tried X" only for concrete actions evidenced in the conversation
- Use "the user argues/believes X" only when the user stated it explicitly in first person
- Never attribute an idea to the user if it came from an AI response or an external source being discussed
- When uncertain who originated an idea, use "X was discussed" or "X emerged in discussion"
- Open Questions should reflect genuine unresolved tensions from the conversations — not inferred curiosity

Output ONLY the updated markdown content for the page. No explanation, no preamble."""

PERSONAL_PROMPT = """You are creating a private personal summary from a person's ChatGPT conversations.

This is a high-level, abstract summary for the person's own private reference.
Focus on themes, patterns, and growth areas — NOT specific personal details.

## Conversations marked as personal/sensitive:
{conversations}

## Instructions:
Write a brief private summary covering:
- Major life themes or recurring concerns (abstractly — no details)
- Areas of personal growth or exploration
- Recurring questions or goals
- Tone and patterns in how they approached personal challenges

Keep it abstract, empathetic, and useful as a private self-reflection tool.
No specific names, dates, medical details, or financial figures.

Output only the markdown content."""


def load_classified(min_value: int = 3, categories: list = None, personal: bool = False) -> list[dict]:
    """Load classified.jsonl with filters applied."""
    if not Path(CLASSIFIED_FILE).exists():
        print(f"Error: {CLASSIFIED_FILE} not found. Run classify.py first.")
        sys.exit(1)

    items = []
    with open(CLASSIFIED_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                items.append(obj)
            except json.JSONDecodeError:
                pass

    if personal:
        return [i for i in items if i.get("sensitive") or i.get("category") == "personal"]

    # For wiki: exclude personal/sensitive, apply value threshold
    result = []
    for i in items:
        if i.get("sensitive") or i.get("category") in ("personal", "trivial"):
            continue
        if i.get("value", 0) < min_value:
            continue
        if categories and i.get("category") not in categories:
            continue
        result.append(i)

    return result


def load_conversations_by_id(ids: set) -> dict:
    """Load full conversation text for a set of IDs."""
    print(f"Loading full conversation text for {len(ids)} conversations...")
    with open(CONVERSATIONS_FILE) as f:
        all_convos = json.load(f)

    result = {}
    for c in all_convos:
        if c.get("id") in ids:
            result[c["id"]] = c
    return result


def extract_full_text(convo: dict, max_chars: int = 3000) -> str:
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
                ts = msg.get("create_time", 0)
                msgs.append((ts, role, text))

    msgs.sort(key=lambda x: x[0])

    result = []
    chars = 0
    for _, role, text in msgs:
        if chars >= max_chars:
            result.append("[...truncated]")
            break
        prefix = "User: " if role == "user" else "AI: "
        snippet = text[:max_chars - chars]
        result.append(f"{prefix}{snippet}")
        chars += len(snippet)

    return "\n".join(result)


def group_by_topic(classified: list[dict]) -> dict:
    """Group classified items by topic (using category + topics tags)."""
    groups = defaultdict(list)
    for item in classified:
        category = item.get("category", "research")
        topics = item.get("topics", [])
        # Primary grouping: category/first-topic
        if topics:
            key = f"{category}/{topics[0].lower().replace(' ', '_')}"
        else:
            key = category
        groups[key].append(item)
    return dict(groups)


def get_wiki_page_path(topic_key: str) -> Path:
    """Get the filesystem path for a wiki topic."""
    return WIKI_DIR / f"{topic_key}.md"


def read_wiki_page(path: Path) -> str:
    """Read existing wiki page content, or empty string if new."""
    if path.exists():
        return path.read_text()
    return ""


def write_wiki_page(path: Path, content: str):
    """Write wiki page, creating directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def call_llm(client, model: str, prompt: str, use_openrouter: bool) -> str:
    """Unified LLM call supporting both Anthropic and OpenRouter clients."""
    if use_openrouter:
        response = client.chat.completions.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
    else:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()


def update_wiki_page(
    client,
    topic_key: str,
    category: str,
    items: list[dict],
    conv_texts: dict,
    model: str,
    use_openrouter: bool,
    dry_run: bool = False,
) -> str:
    """Call LLM to update a single wiki page. Returns new content."""
    page_path = get_wiki_page_path(topic_key)
    current_content = read_wiki_page(page_path)

    # Build conversation snippets
    conv_snippets = []
    for item in items[:15]:  # cap at 15 conversations per page update
        cid = item["id"]
        text = conv_texts.get(cid, "")
        if not text:
            continue
        date = ""
        ts = item.get("create_time", 0)
        if ts:
            date = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        summary = item.get("summary", "")
        conv_snippets.append(f"[{date}] {summary}\n{text}\n---")

    if not conv_snippets:
        return current_content

    topic_name = topic_key.replace("/", " / ").replace("_", " ").title()
    conversations_text = "\n".join(conv_snippets)

    if dry_run:
        return f"# {topic_name}\n\n[dry-run: would process {len(items)} conversations]\n"

    prompt = INGEST_PROMPT.format(
        current_content=current_content or "(empty — new page)",
        category=category,
        topic=topic_name,
        conversations=conversations_text,
    )

    for attempt in range(3):
        try:
            return call_llm(client, model, prompt, use_openrouter)
        except Exception as e:
            if attempt == 2:
                print(f"\nWarning: failed to update {topic_key}: {e}")
                return current_content
            time.sleep(2 ** attempt)

    return current_content


def build_personal_summary(client, items: list[dict], conv_texts: dict, model: str, use_openrouter: bool, dry_run: bool = False):
    """Build personal/summary.md from personal/sensitive conversations."""
    PERSONAL_DIR.mkdir(exist_ok=True)
    out_path = PERSONAL_DIR / "summary.md"

    conv_snippets = []
    for item in items[:50]:  # cap
        cid = item["id"]
        text = conv_texts.get(cid, "")
        if not text:
            continue
        summary = item.get("summary", "")
        conv_snippets.append(f"Summary: {summary}\n{text[:500]}\n---")

    if not conv_snippets:
        print("No personal conversations found.")
        return

    if dry_run:
        out_path.write_text("# Personal Summary\n\n[dry-run]\n")
        return

    prompt = PERSONAL_PROMPT.format(conversations="\n".join(conv_snippets))
    content = call_llm(client, model, prompt, use_openrouter)
    out_path.write_text(content)
    print(f"Written: {out_path}")


def update_index(all_pages: list[Path]):
    """Rebuild wiki/index.md from all existing wiki pages."""
    WIKI_DIR.mkdir(exist_ok=True)
    index_path = WIKI_DIR / "index.md"

    # Group by top-level category
    by_category = defaultdict(list)
    for p in sorted(all_pages):
        rel = p.relative_to(WIKI_DIR)
        parts = rel.parts
        category = parts[0] if len(parts) > 1 else "other"
        by_category[category].append(p)

    lines = [
        "# Knowledge Base Index",
        f"\n_Last updated: {datetime.date.today()}_\n",
    ]
    for cat in sorted(by_category):
        lines.append(f"\n## {cat.title()}\n")
        for p in by_category[cat]:
            rel = p.relative_to(WIKI_DIR)
            name = p.stem.replace("_", " ").title()
            lines.append(f"- [{name}]({rel})")

    index_path.write_text("\n".join(lines) + "\n")
    print(f"Updated: {index_path}")


def append_log(message: str):
    """Append to wiki/log.md."""
    WIKI_DIR.mkdir(exist_ok=True)
    log_path = WIKI_DIR / "log.md"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(log_path, "a") as f:
        f.write(f"\n[{ts}] {message}\n")


def main():
    parser = argparse.ArgumentParser(description="Build wiki from classified conversations")
    parser.add_argument("--dry-run", action="store_true", help="Don't call API")
    parser.add_argument("--personal", action="store_true", help="Build personal/summary.md instead of wiki")
    parser.add_argument(
        "--category",
        nargs="+",
        choices=["tech", "ai", "research", "projects"],
        help="Only process these categories",
    )
    parser.add_argument("--min-value", type=int, default=3, help="Minimum value score (default: 3)")
    parser.add_argument("--topic", type=str, help="Only process this specific topic key")
    parser.add_argument("--openrouter", action="store_true", help="Use OpenRouter instead of Anthropic")
    parser.add_argument("--model", type=str, default=None, help="Model ID override")
    parser.add_argument("--workers", type=int, default=15, help="Parallel workers (default: 15)")
    args = parser.parse_args()

    use_openrouter = args.openrouter

    if args.dry_run:
        client = None
        model = "dry-run"
    elif use_openrouter:
        import openai as openai_lib
        or_key = os.environ.get("OPENROUTER_API_KEY")
        if not or_key:
            # Fallback: read from Demando .env
            env_path = Path.home() / "code/Demando/.env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("OPENROUTER_API_KEY="):
                        or_key = line.split("=", 1)[1].strip().strip('"')
                        break
        if not or_key:
            print("Error: OPENROUTER_API_KEY not found.")
            sys.exit(1)
        client = openai_lib.OpenAI(base_url=OPENROUTER_BASE_URL, api_key=or_key)
        model = args.model or OPENROUTER_DEFAULT_MODEL
        print(f"Using OpenRouter: {model}")
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("Error: ANTHROPIC_API_KEY environment variable not set.")
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)
        model = args.model or WIKI_MODEL
        print(f"Using Anthropic: {model}")

    # Load classified conversations
    classified = load_classified(
        min_value=args.min_value,
        categories=args.category,
        personal=args.personal,
    )
    print(f"Loaded {len(classified)} qualifying conversations")

    if not classified:
        print("No conversations match filters. Check classified.jsonl and adjust --min-value.")
        return

    # Load full conversation text
    ids = {item["id"] for item in classified}
    conv_map = load_conversations_by_id(ids)

    # Build text lookup: id -> readable text
    conv_texts = {}
    for cid, convo in conv_map.items():
        conv_texts[cid] = extract_full_text(convo)

    # Handle personal mode
    if args.personal:
        print("Building personal/summary.md...")
        build_personal_summary(client, classified, conv_texts, model, use_openrouter, dry_run=args.dry_run)
        return

    # Group by topic
    groups = group_by_topic(classified)

    if args.topic:
        groups = {k: v for k, v in groups.items() if k == args.topic}
        if not groups:
            print(f"Topic '{args.topic}' not found. Available: {sorted(groups.keys())}")
            return

    print(f"\nTopics to process: {len(groups)}")
    for key, items in sorted(groups.items(), key=lambda x: -len(x[1]))[:20]:
        print(f"  {key}: {len(items)} conversations")

    workers = args.workers
    print(f"\nRunning with {workers} parallel workers\n")

    updated_pages = []
    failed = []

    def process_topic(topic_key_items):
        topic_key, items = topic_key_items
        category = items[0].get("category", "research")
        try:
            new_content = update_wiki_page(
                client, topic_key, category, items, conv_texts, model, use_openrouter, dry_run=args.dry_run
            )
            page_path = get_wiki_page_path(topic_key)
            with _write_lock:
                write_wiki_page(page_path, new_content)
            return page_path, None
        except Exception as e:
            return None, f"{topic_key}: {e}"

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_topic, item): item[0] for item in sorted(groups.items())}
        with tqdm(total=len(futures), desc="Building wiki") as pbar:
            for future in as_completed(futures):
                page_path, err = future.result()
                if page_path:
                    updated_pages.append(page_path)
                else:
                    failed.append(err)
                pbar.update(1)

    if failed:
        print(f"\nWarning: {len(failed)} pages failed:")
        for f in failed[:10]:
            print(f"  {f}")

    # Update index
    all_pages = list(WIKI_DIR.rglob("*.md"))
    all_pages = [p for p in all_pages if p.name not in ("index.md", "log.md")]
    update_index(all_pages)

    # Log the ingest
    append_log(
        f"Ingested {len(classified)} conversations → {len(updated_pages)} pages updated "
        f"(min_value={args.min_value}, categories={args.category or 'all'}, workers={workers})"
    )

    print(f"\nDone! Updated {len(updated_pages)} wiki pages.")
    print(f"Wiki directory: {WIKI_DIR}/")
    print(f"Index: {WIKI_DIR}/index.md")


if __name__ == "__main__":
    main()
