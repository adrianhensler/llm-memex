# llm-memex

**Turn years of LLM conversations into a searchable, self-improving personal knowledge wiki.**

Point an agent at this repo with your `conversations.json` export and it will build everything described here. All prompts, data formats, and implementation details are included.

> *"I thought 3 years of conversations with an AI tool would have value — and it does, just not in the way I expected. The value isn't the raw conversations. It's what you can synthesize from them."*

---

## What You End Up With

- **~1,200 wiki pages** organized by category (`ai`, `projects`, `research`, `tech`), each with Summary, Key Points, Tools & Resources, Open Questions, and Related sections
- **3,500+ open questions** aggregated across the wiki, with a feed of recently updated ones and a place to add your own notes
- **A Flask web app** with collapsible sidebar navigation, full-text search, per-page AI improvement, and automatic cross-referencing
- **`[[Wiki links]]`** that resolve to actual pages with n-gram fuzzy matching
- **A living document** — pages improve over time via an Improve button that pulls in original source conversations and related wiki pages as context

---

## Foundation: Karpathy's LLM Wiki Pattern

This project applies [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) to a source most people already have: their own LLM conversation history.

His pattern has three layers, mapped here as:

| Karpathy Layer | This Project | Description |
|---|---|---|
| **Raw Sources** | `conversations.json` | Immutable export — never modified |
| **The Schema** | `schema.md` | Defines categories, page structure, conventions |
| **The Wiki** | `wiki/` | LLM-generated markdown, lives alongside sources |

The key insight from the pattern: LLMs remove the bookkeeping friction that causes humans to abandon wikis. Updates are resume-safe and incremental — classify once, ingest in batches, improve selectively. The wiki compounds over time.

---

## Architecture

```
conversations.json  (ChatGPT/Claude export — immutable raw source)
        │
        ▼
   classify.py       ← Claude Haiku: batch-classify each conversation
        │
        ▼
  classified.jsonl   ← one JSON object per conversation (id, category, topics, value, summary, sensitive)
        │
        ▼
    ingest.py         ← Claude Haiku: group by topic, synthesize wiki pages
        │
        ▼
    wiki/             ← markdown pages organized as wiki/{category}/{topic}.md
        │
        ▼
    web/app.py        ← Flask app: browse, search, improve, explore questions
```

---

## Prerequisites

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or pip
- An [Anthropic API key](https://console.anthropic.com/)
- A ChatGPT data export (`conversations.json`) — Settings → Data Controls → Export

### Install

```bash
git clone https://github.com/adrianhensler/llm-memex
cd llm-memex
uv sync   # or: pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here
```

Place your `conversations.json` in the project root.

### Dependencies (`requirements.txt`)

```
anthropic>=0.89.0
flask>=3.0.0
markdown2>=2.5.0
tqdm>=4.66.0
```

---

## Input Format: `conversations.json`

The ChatGPT export is a JSON array. Each element is a conversation object:

```json
[
  {
    "id": "abc123",
    "title": "Docker networking question",
    "create_time": 1704067200.0,
    "mapping": {
      "node-uuid-1": {
        "message": {
          "author": {"role": "user"},
          "content": {
            "content_type": "text",
            "parts": ["How do I expose a port in Docker?"]
          },
          "create_time": 1704067200.0
        }
      },
      "node-uuid-2": {
        "message": {
          "author": {"role": "assistant"},
          "content": {
            "content_type": "text",
            "parts": ["Use the -p flag: docker run -p 8080:80 ..."]
          },
          "create_time": 1704067215.0
        }
      }
    }
  }
]
```

Key fields:
- `id` — unique conversation ID (used as key throughout the pipeline)
- `title` — conversation title assigned by ChatGPT
- `create_time` — Unix timestamp
- `mapping` — dict of node IDs to message objects; messages have `author.role` (`user`/`assistant`/`system`/`tool`) and `content.parts` (list of strings)

To extract readable text from a conversation, iterate `mapping.values()`, filter to `role in ("user", "assistant")`, join `content.parts`, sort by `create_time`, and truncate to a character limit.

---

## The Schema (`schema.md`)

The schema file defines the wiki's structure and is passed as context to the ingest prompt. This is the Karpathy "schema layer" — it constrains what the LLM produces and keeps all pages consistent.

```markdown
# Knowledge Base Schema

## Categories

### tech
Programming, software development, APIs, languages, frameworks, debugging.
Pages live in: wiki/tech/

### ai
AI/ML tools, models, prompting, APIs, agents, workflows.
Pages live in: wiki/ai/

### research
Factual research, how-tos, domain knowledge (networking, hardware, etc.).
Pages live in: wiki/research/

### projects
Specific projects or builds the user worked on across multiple conversations.
Pages live in: wiki/projects/

### personal
Health, relationships, career, life decisions, finances.
Output goes to: personal/ (separate, lighter-touch summary).

### trivial
One-off lookups, very short exchanges with no durable knowledge.
Skip entirely.

## Wiki Page Format

# [Topic Title]

## Summary
2-3 sentence overview of what the user knows/explored in this area.

## Key Points
- Bullet points of durable knowledge, findings, patterns

## Tools & Resources
- [Tool Name](https://url) — description
- [[Related Wiki Page]] — internal cross-reference

## Projects & Experiments
- Specific things built or tried

## Open Questions
- Things left unresolved or worth revisiting

## Related
- [[Topic A]]
- [[Topic B]]
```

---

## Step 1: Classify Conversations (`classify.py`)

Reads `conversations.json`, sends batches to Claude Haiku, writes `classified.jsonl`.

```bash
uv run python3 classify.py
uv run python3 classify.py --dry-run          # test without API calls
uv run python3 classify.py --limit 100        # process first 100 only
uv run python3 classify.py --batch-size 25    # conversations per API call
```

**Resume-safe**: already-classified IDs are read from `classified.jsonl` on startup and skipped.

### Classification Prompt

Conversations are batched (default 25 per call). Each conversation is formatted as:

```
ID: {id}
Date: {YYYY-MM-DD}
Title: {title}
User: {first N chars of first user message}
AI: {first N chars of first assistant message}
---
```

Truncated to 800 characters per conversation (enough to classify, minimal cost).

The prompt:

```
You are classifying ChatGPT conversations for a personal knowledge base.

For each conversation below, output a JSON array where each element has:
- "id": the conversation id (copy exactly)
- "category": one of: "tech", "ai", "research", "projects", "personal", "trivial"
- "value": integer 1-5 (5=highly valuable durable knowledge, 1=trivial/transient)
- "sensitive": true if contains health, medical, relationships, finances, named individuals
- "summary": one sentence describing what it's about (be specific)
- "topics": list of 1-3 keyword tags

Category guidance:
- tech: programming, software, debugging, APIs, frameworks, languages
- ai: AI/ML models, tools, prompting, agents, LLMs
- research: factual lookups, how-tos, domain knowledge
- projects: a specific build being worked on
- personal: health, relationships, career anxiety, life decisions, finances
- trivial: one-off simple lookups, very short exchanges, nothing durable

Value guidance:
- 5: Deep exploration, something worth referencing again
- 4: Solid knowledge, useful patterns or findings
- 3: Some value, maybe a useful reference
- 2: Mostly transient, minimal durable knowledge
- 1: Skip-worthy

Conversations:
{conversations}

Respond with ONLY a valid JSON array. No markdown, no explanation.
```

### Output: `classified.jsonl`

One JSON object per line:

```json
{"id": "abc123", "category": "tech", "value": 4, "sensitive": false, "summary": "Debugging Docker port binding on macOS", "topics": ["docker", "networking"]}
```

**Cost**: ~$0.65 for 4,100 conversations using Claude Haiku (~45 minutes at 15 parallel workers).

---

## Step 2: Generate Wiki Pages (`ingest.py`)

Groups classified conversations by `category/topic[0]`, synthesizes a wiki page per group.

```bash
uv run python3 ingest.py
uv run python3 ingest.py --dry-run          # preview without writing
uv run python3 ingest.py --category tech    # one category only
uv run python3 ingest.py --min-value 4      # high-value conversations only
uv run python3 ingest.py --personal         # generate personal/ summary
```

**Resume-safe**: ingested conversation IDs are tracked in `wiki/log.md`. Interrupted runs restart where they left off.

### Grouping Logic

```python
# Group by primary topic key: "category/topic"
groups = defaultdict(list)
for item in classified:
    if item["category"] in ("trivial", "personal"):
        continue
    if item["value"] < 3:
        continue
    key = f"{item['category']}/{item['topics'][0].lower().replace(' ', '_')}"
    groups[key].append(item)
```

### Ingest Prompt

For each group, up to 15 conversations are included, each truncated to 3,000 characters:

```
You are maintaining a personal knowledge base wiki.

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
- Do NOT include sensitive personal details (health, relationships, specific names).
- Write in third person about "the user" or use neutral phrasing.
- In "Tools & Resources": use [Name](url) for external resources, [[Page Name]] for internal
  wiki cross-references. Never list a resource as plain unlinked text.
- In "Related": always use [[Page Name]] syntax for internal wiki links.

Output ONLY the updated markdown content for the page. No explanation, no preamble.
```

Output is written to `wiki/{category}/{topic}.md`. Parent directories are created as needed.

**Cost**: ~$4.00 for ~1,200 pages using Claude Haiku (~2 hours at 15 parallel workers).

---

## Step 3: Handle Duplicates (`merge_dupes.py`)

The classifier sometimes routes the same topic to multiple categories, or creates `docker_networking` and `docker-networking` as separate keys. This script finds and merges them.

```bash
uv run python3 merge_dupes.py --dry-run   # review proposed merges
uv run python3 merge_dupes.py             # execute merges
```

**Detection**: normalize all page stems (lowercase, collapse `-` and `_`). Pages with identical normalized stems are candidates. Calls Haiku to intelligently merge content, keeps the larger file as canonical, deletes the duplicate.

Always run `--dry-run` first and review before committing.

**Typical yield**: ~33 merges on a 1,200-page corpus (9 same-category plural/spelling variants, 24 cross-category duplicates for common tools like docker, python, linux, etc.).

---

## Step 4: Run the Web App

```bash
uv run python3 web/app.py
# Open http://localhost:5000
```

### App Structure

```
web/
  app.py              # Flask application (~780 lines)
  templates/
    base.html         # navbar + collapsible sidebar
    wiki.html         # page view with see-also bar and improve button
    wiki_category.html # category grid with prefix filter chips
    search.html       # results grouped by category
    improve.html      # improve form with options
    improve_result.html # side-by-side diff with accept/discard
    questions.html    # aggregated open questions with notes
    index.html        # stats dashboard
  static/
    style.css         # custom styles (sidebar, cards, questions)
```

### Key Implementation Details

#### Sidebar: Prefix-Based Grouping

Pages within each category are auto-grouped by the first word of their filename stem (before `_` or `-`). Groups with only 1 page are bucketed as "other".

```python
def group_by_prefix(pages):
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
```

The sidebar uses Bootstrap collapse. The active category (containing the current page) starts expanded; others collapsed.

#### Wiki Link Resolution

`[[Page Name]]` syntax is resolved before passing to `markdown2`. Exact match first, then progressive n-gram fallback:

```python
def _resolve_wiki_links(text, pages):
    lookup = _build_page_lookup(pages)  # normalized name → path

    def find_path(title):
        # 1. Exact normalized match
        path = lookup.get(_normalize_link(title))
        if path:
            return path
        # 2. Progressively shorter n-grams (longest first)
        words = [w for w in re.split(r'[\s_&+,/]+', title.lower()) if len(w) > 2]
        for n in range(len(words), 1, -1):
            for i in range(len(words) - n + 1):
                candidate = '_'.join(words[i:i + n])
                if candidate in lookup:
                    return lookup[candidate]
        # 3. Single-word only if that's the whole title
        if len(words) == 1 and words[0] in lookup:
            return lookup[words[0]]
        return None

    def replace(m):
        title = m.group(1).strip()
        path = find_path(title)
        return f'[{title}](/wiki/{path})' if path else title

    return re.sub(r'\[\[([^\]]+)\]\]', replace, text)
```

Normalization: lowercase, `&`/`+` → `_and_`, strip punctuation, collapse whitespace/dashes/underscores to `_`.

#### See Also Bar

On each wiki page, pages with the same normalized stem in *other* categories are shown as badges:

```python
stem = re.sub(r'[-_]+', '_', path.stem.lower())
see_also = []
for cat, cat_pages in pages.items():
    if cat == active_cat:
        continue
    for p in cat_pages:
        p_stem = re.sub(r'[-_]+', '_', p['path'].split('/')[-1].lower())
        if p_stem == stem:
            see_also.append(p)
```

#### Search: Multi-Word AND Match

Split query into words. A page matches if ALL words appear in title OR content. Pages where all words appear in the title rank first.

```python
words = q.lower().split()
for p in wiki_files:
    lower = text.lower()
    title = display_name(p.stem).lower()
    if not all(w in lower or w in title for w in words):
        continue
    title_match = all(w in title for w in words)
    rank = 0 if title_match else 1
    # snippet: 80 chars before + 220 chars after first word match
```

Results are grouped by category in the template.

#### Open Questions Extraction

```python
def extract_questions():
    for p in wiki_files:
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
                    qid = hashlib.md5(f"{page_path}::{text}".encode()).hexdigest()[:12]
                    questions.append({...})
```

Question IDs are stable MD5 hashes of `page_path::question_text`. Human notes are stored in `questions_notes.json` keyed by this ID — never touched by AI.

---

## Step 5: Improve Pages

The Improve button is the highest-leverage feature. Each page was initially generated from a narrow slice of conversations. The Improve flow gives Claude full context:

1. **Loads the top-10 highest-value source conversations** that contributed to the page (matched via normalized `category/topic` key against `classified.jsonl`, then fetched from `conversations.json`)
2. **Exposes a `read_wiki_page` tool** — Claude can request related pages before rewriting
3. **Calls Claude Sonnet** to rewrite with full context

### Source Conversation Loading

`conversations.json` (293MB) is loaded once into a module-level dict `{id: convo}` on first use and cached. `classified.jsonl` is similarly cached. This avoids re-reading on every improve request.

```python
_classified_cache = None
_conversations_cache = None

def _load_conversations():
    global _conversations_cache
    if _conversations_cache is None:
        with open(CONVERSATIONS_FILE) as f:
            convos = json.load(f)
        _conversations_cache = {c["id"]: c for c in convos}
    return _conversations_cache
```

### Agentic Improve Loop

The improve endpoint runs a multi-turn agentic loop to handle tool use:

```python
messages = [{"role": "user", "content": full_prompt}]
for _ in range(15):  # max rounds
    response = client.messages.create(messages=messages, **kwargs)

    # Collect any text output
    text_blocks = [b.text for b in response.content if hasattr(b, "text")]
    if text_blocks:
        improved_text = "\n".join(text_blocks).strip()

    if response.stop_reason != "tool_use":
        break

    messages.append({"role": "assistant", "content": response.content})

    # Handle local read_wiki_page tool calls
    local_tool_uses = [
        b for b in response.content
        if getattr(b, "type", "") == "tool_use"
        and getattr(b, "name", "") == "read_wiki_page"
    ]
    if local_tool_uses:
        tool_results = []
        for tu in local_tool_uses:
            req = tu.input.get("path", "").strip("/")
            req_path = WIKI_DIR / (req + ".md")
            if req_path.exists() and req_path.resolve().is_relative_to(WIKI_DIR.resolve()):
                content = req_path.read_text()
                if len(content) > 5000:
                    content = content[:5000] + "\n\n[...truncated]"
                result_text = content
            else:
                result_text = f"Page not found: {req}"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result_text,
            })
        messages.append({"role": "user", "content": tool_results})
    # web_search is server-side — Anthropic handles tool_result automatically, just loop
```

The `read_wiki_page` tool is defined as a local tool with this schema:

```json
{
  "name": "read_wiki_page",
  "description": "Read the full content of another wiki page for context.",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "e.g. 'tech/kubernetes'"}
    },
    "required": ["path"]
  }
}
```

Path traversal is blocked: `req_path.resolve().is_relative_to(WIKI_DIR.resolve())`.

### Improve Prompt

```
You are editing a personal knowledge base wiki page. Your job is to make it more
accurate, specific, and useful — not longer for its own sake.

## Page to improve:
{current_content}

## Rules:
- Output ONLY the complete updated markdown page. No preamble, no explanation.
- Fix vague or incomplete claims by making them specific (versions, dates, outcomes)
- Sharpen "Open Questions" — replace generic questions with ones that reflect real
  decisions or unresolved tensions evident in the page
- If there are clear patterns of what worked or failed, add or improve a "Lessons Learned" section
- Remove filler content: obvious statements, redundant bullet points, vague aspirations
- In "Tools & Resources": use [Name](url) for external links, [[Page Name]] for internal wiki refs
- Do not invent projects, tools, or outcomes not already evidenced in the page
- Do not announce or describe your edits anywhere in the output
```

When source conversations are included, they are appended as:

```
## Source conversations (10 highest-value conversations that contributed to this page)
Use these to add specific details, correct vague claims, and recover insights lost
in the original synthesis:

[1] 2024-03-15 (value: 5)
Summary: Debugging Kubernetes ingress controller on bare metal
User: ...
AI: ...
---
```

The improve result is saved to `{page_path}.__pending__.md`. The user reviews a side-by-side diff and clicks Accept or Discard. Accept overwrites the original (with a `.bak` backup); Discard deletes the pending file.

**Cost per improvement**: ~$0.07 (Haiku pre-summarization + Sonnet synthesis with source conversations).

---

## Step 6: Open Questions (`/questions`)

The questions page aggregates all `## Open Questions` sections from every wiki page:

- **Feed** — 40 most recently modified questions (by page mtime)
- **By category** — all questions grouped, collapsible
- **Live search** — JavaScript filters as you type
- **Personal notes** — ✏️ button opens an inline textarea; saves to `questions_notes.json` on blur; never AI-touched

Notes are keyed by stable MD5 hash of `"{page_path}::{question_text}"`. If a question text changes in the wiki, the old note becomes orphaned (harmless — just invisible).

---

## Batch Improvement

To merge and improve many pages at once:

```python
# Pattern: Haiku merge → Sonnet improve with source conversations
for page_group in duplicate_groups:
    merged = haiku_merge(page_group)          # combine content intelligently
    improved = sonnet_improve(               # deepen with source context
        merged,
        source_conversations=get_source_convos(page)
    )
    write(improved)
```

Typical run: ~40 pages, ~$3.00 (Sonnet + Haiku).

---

## File Reference

| File | Purpose |
|------|---------|
| `classify.py` | Batch-classify conversations → `classified.jsonl` |
| `ingest.py` | Synthesize wiki pages from classified conversations |
| `merge_dupes.py` | Detect and merge duplicate wiki pages |
| `recover_topics.py` | Single-pass recovery for specific topic keys |
| `stats.py` | Generate corpus statistics → `stats/stats.json` |
| `schema.md` | Wiki schema: categories, page format, conventions |
| `web/app.py` | Flask web application |
| `web/templates/` | Jinja2 templates |
| `web/static/style.css` | Custom styles |

---

## Costs (Actual, 4,103 conversations)

| Step | Model | Cost |
|------|-------|------|
| Classification | Claude Haiku | ~$0.65 |
| Wiki generation (~1,200 pages) | Claude Haiku | ~$4.00 |
| Deduplication merges | Claude Haiku | ~$0.20 |
| Batch improve (40 pages) | Sonnet + Haiku | ~$3.00 |
| Interactive web improvements | Claude Sonnet | ~$13.00 |
| **Total** | | **~$20** |

A clean pipeline run (classify + ingest + dedup, no interactive improvements) costs **~$5–7** for 4,000 conversations.

---

## What the Wiki Reveals

- **Recurring questions** — the same unresolved tensions appear across dozens of conversations under different guises
- **Expertise depth** — topics with 40+ conversations reveal genuine deep engagement; 2–3 conversations reveals surface curiosity
- **Project archaeology** — half-started experiments and forgotten decisions resurface with context
- **Knowledge gaps** — thin pages despite many conversations signal topics worth improving first

---

## Future Directions

- **Ingest other sources** — email, bookmarks, notes apps, Anthropic/Claude exports
- **Embedding-based conversation selection** — find conversations most semantically relevant to a thin page rather than top-N by value score
- **Question resolution tracking** — mark questions as answered/in-progress without AI involvement
- **Timeline view** — how thinking on a topic evolved over time

---

## Credits

- [Andrej Karpathy's LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — the pattern this is built on
- [Anthropic Claude](https://anthropic.com) — classification, synthesis, improvement
- [Flask](https://flask.palletsprojects.com/), [Bootstrap 5](https://getbootstrap.com/), [markdown2](https://github.com/trentm/python-markdown2)
