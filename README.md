# ChatGPT → Personal Knowledge Wiki

**Turn years of ChatGPT conversations into a searchable, self-improving personal knowledge base.**

Built by applying [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) to a personal ChatGPT export — 4,103 conversations, ~293MB of raw JSON — producing ~1,200 structured wiki pages, 3,500+ open questions, and a browsable web interface with AI-powered improvement tools.

> *"I thought 3 years of conversations with an AI tool would have value — and it does, just not in the way I expected. The value isn't the raw conversations. It's what you can synthesize from them."*

---

## What You End Up With

- **~1,200 wiki pages** organized by category (ai, projects, research, tech), each with Summary, Key Points, Tools & Resources, Open Questions, and Related sections
- **3,500+ open questions** aggregated from across the wiki, browsable with a feed of recently updated ones — and a place to add your own notes
- **A web app** with collapsible sidebar navigation, full-text search, per-page AI improvement, and automatic cross-referencing between related pages
- **[[Wiki links]]** that resolve to actual pages, plus external resource links
- **A living document** — pages improve over time as you use the Improve button, which pulls in source conversations and related wiki pages as context

---

## Inspiration: Karpathy's LLM Wiki Pattern

[Andrej Karpathy's gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) describes a pattern for AI-assisted knowledge management built around a key insight:

> The tedious part of maintaining a knowledge base is the bookkeeping — updating references, maintaining consistency — which causes humans to abandon wikis. LLMs don't get bored and can touch 15 files simultaneously, making maintenance nearly cost-free.

His pattern uses three layers:
1. **Raw Sources** — immutable documents as source of truth
2. **The Wiki** — LLM-generated markdown organized by concept, with cross-references
3. **The Schema** — a configuration file documenting wiki structure and conventions

This project applies that pattern to a source most people already have: their own ChatGPT conversation history.

---

## Architecture

```
conversations.json (ChatGPT export)
        │
        ▼
   classify.py          ← Claude Haiku: categorize, score, summarize each conversation
        │
        ▼
  classified.jsonl       ← ~4,100 items with topic, value score, category, summary
        │
        ▼
    ingest.py            ← Claude Haiku/Sonnet: synthesize wiki pages per topic
        │
        ▼
    wiki/                ← ~1,200 markdown pages organized by category
        │
        ▼
    web/app.py           ← Flask web app: browse, search, improve, explore questions
```

---

## Setup

### Prerequisites

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or pip
- An [Anthropic API key](https://console.anthropic.com/)
- A [ChatGPT data export](https://help.openai.com/en/articles/7260999-how-do-i-export-my-chatgpt-history-and-data) (`conversations.json`)

### Install

```bash
git clone https://github.com/adrianhensler/llm-memex
cd llm-memex
uv sync   # or: pip install -r requirements.txt
```

Place your `conversations.json` in the project root.

### API Key

```bash
export ANTHROPIC_API_KEY=your_key_here
```

---

## Step 1: Classify Conversations

`classify.py` reads every conversation and uses Claude Haiku to assign:
- **Category**: `tech`, `ai`, `research`, `projects`, `personal`, `trivial`
- **Topics**: 1–3 specific topic tags (e.g. `["docker", "networking"]`)
- **Value score**: 1–5 (how much durable knowledge this conversation contains)
- **Summary**: one sentence
- **Sensitive flag**: whether the conversation contains personal/health/financial data

```bash
uv run python3 classify.py
```

This processes all conversations in parallel (15 workers) and writes `classified.jsonl`. On 4,100 conversations: ~45 minutes, ~$0.65 using Claude Haiku.

#### Classification Prompt

The core prompt used for each conversation:

```
You are classifying a ChatGPT conversation for a personal knowledge base.

Classify this conversation and output JSON:
{
  "category": "tech|ai|research|projects|personal|trivial",
  "topics": ["primary_topic", "optional_secondary"],
  "value": 1-5,
  "summary": "one sentence",
  "sensitive": true/false
}

Value scale:
1 = trivial/throwaway (chitchat, simple lookups)
2 = minor reference value
3 = useful knowledge or technique
4 = significant insight, solution, or project work
5 = deep expertise, novel insight, or important decision

Sensitive = contains health, relationships, finances, or personal identifying info.

Conversation:
{text}
```

**Key design decisions:**
- Conversations truncated to 2,000 characters for cost control — enough to classify accurately
- `sensitive` flag routes conversations to a separate `personal/` output rather than the main wiki
- `trivial` category is excluded entirely from wiki generation
- Value threshold: only conversations scoring ≥ 3 make it into wiki pages

---

## Step 2: Generate Wiki Pages

`ingest.py` groups classified conversations by their primary topic key (`category/topic`), then calls Claude to synthesize a wiki page for each group.

```bash
uv run python3 ingest.py
```

Options:
```bash
uv run python3 ingest.py --dry-run          # preview without writing
uv run python3 ingest.py --category tech    # only one category
uv run python3 ingest.py --min-value 4      # only high-value conversations
uv run python3 ingest.py --personal         # generate personal summary
```

#### Wiki Page Schema (`schema.md`)

Every wiki page follows this structure:

```markdown
# Category / Topic Name

## Summary
2-3 sentence synthesis of the user's knowledge and perspective on this topic.

## Key Points
- Specific insights, techniques, decisions, and lessons learned
- What worked, what didn't
- Concrete tools, versions, configurations used

## Tools & Resources
- [Tool Name](https://url) — description
- [[Related Wiki Page]] — internal cross-reference

## Projects & Experiments
Notable things the user built, tried, or explored in this topic area.

## Open Questions
- Unresolved questions, tensions, things worth investigating further

## Related
- [[Topic A]]
- [[Topic B]]
```

#### Ingest Prompt

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
- Do NOT include sensitive personal details (health, relationships, specific names of people).
- Write in third person about "the user" or use neutral phrasing.
- In "Tools & Resources": use [Name](url) for external resources, [[Page Name]] for internal
  wiki cross-references. Never list a resource as plain unlinked text.
- In "Related": always use [[Page Name]] syntax for internal wiki links.

Output ONLY the updated markdown content for the page. No explanation, no preamble.
```

**Key design decisions:**
- Up to 15 conversations per page update, capped to prevent prompt bloat
- Each conversation truncated to 3,000 characters
- Resume-safe: tracks ingested conversation IDs in `wiki/log.md` — can be interrupted and restarted
- Parallel execution with `ThreadPoolExecutor` (15 workers)

**On models:** The initial full run used Claude Haiku for cost. Pages generated with Haiku are good but not deep. The Improve feature (Step 5) uses Sonnet with source conversations for pages that matter.

---

## Step 3: Handle Duplicates

The classifier sometimes routes the same topic to multiple categories, or creates hyphen vs. underscore variants of the same page name. `merge_dupes.py` handles this.

```bash
uv run python3 merge_dupes.py --dry-run   # review what would be merged
uv run python3 merge_dupes.py             # actually merge
```

It finds pairs like `tech/docker_networking` and `research/docker-networking`, calls Haiku to intelligently merge the content, keeps the larger file as canonical, and deletes the duplicate.

**Important:** Always run with `--dry-run` first and review the list before committing.

---

## Step 4: Run the Web App

```bash
uv run python3 web/app.py
# Open http://localhost:5000
```

The web app provides:
- **Sidebar navigation** with collapsible categories and sub-groups by filename prefix (e.g., all `docker_*` pages grouped as "docker (8)")
- **Full-text search** with multi-word AND matching and results grouped by category
- **See Also bar** on each page — automatic cross-links to same-named pages in other categories
- **`[[Wiki links]]`** — automatically resolved to internal pages, with n-gram fuzzy matching for verbose link titles
- **Statistics dashboard** — 8 stat cards and 6 charts about your conversation corpus
- **Questions page** (`/questions`) — all Open Questions aggregated across the wiki, with a feed of recently updated ones and inline personal notes
- **Page improvement** — AI-powered improvement with access to source conversations and wiki cross-references

---

## Step 5: Improve Pages

The Improve button is the highest-leverage feature. Each wiki page was initially generated from a narrow slice of your conversations. The Improve flow goes deeper:

**What it does:**
1. Loads the top-10 highest-value source conversations that contributed to the page
2. Identifies related pages across the wiki (`read_wiki_page` tool — Claude reads them before rewriting)
3. Calls Claude Sonnet to rewrite the page with full context

**Three options on the improve form:**
- **Reference related wiki pages** — checked by default when related pages exist; Claude can request any of them via tool use
- **Include source conversations** — checked by default; loads the original ChatGPT conversations that fed this page
- **Enable web search** — for verifying tool versions, project status, factual claims

**Improve prompt:**

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

**Cost per improvement:** ~$0.07 (Haiku pre-summarization + Sonnet synthesis with source conversations)

---

## Step 6: Open Questions

The `/questions` page aggregates all Open Questions sections from every wiki page:

- **Feed** — 40 most recently updated questions (sorted by page modification time)
- **By category** — all questions grouped, collapsible
- **Live search** — filters across all sections as you type
- **Personal notes** — click ✏️ on any question to add your own thoughts; saved to `questions_notes.json`, never touched by AI

This is the most intellectually honest part of the wiki. Questions represent genuine uncertainty — what you haven't resolved yet, what you're actively thinking about.

---

## Batch Improvement

For bulk merging and improving multiple pages at once, use the pattern from `merge_dupes.py` extended with the improve logic:

```python
# Merge with Haiku, then improve with Sonnet + source conversations
for page_group in duplicate_groups:
    merged = haiku_merge(page_group)
    improved = sonnet_improve(merged, source_conversations=get_source_convos(page))
    write(improved)
```

The project merged and improved ~40 pages this way: 24 cross-category technology duplicates (docker, python, ssh, linux, networking, etc.) and 9 same-category plural/spelling variants.

---

## File Reference

| File | Purpose |
|------|---------|
| `classify.py` | Classify conversations → `classified.jsonl` |
| `ingest.py` | Generate wiki pages from `classified.jsonl` |
| `merge_dupes.py` | Find and merge duplicate wiki pages |
| `recover_topics.py` | Single-pass recovery for specific topic keys |
| `stats.py` | Generate statistics from corpus and wiki |
| `schema.md` | Wiki page schema and conventions |
| `web/app.py` | Flask web application (~780 lines) |
| `web/templates/` | Jinja2 templates (wiki, search, questions, improve) |
| `web/static/style.css` | Custom styles |

---

## Costs (Actual)

For 4,103 conversations:

| Step | Model | Cost |
|------|-------|------|
| Classification | Claude Haiku | ~$0.65 |
| Wiki generation (~1,200 pages) | Claude Haiku | ~$4.00 |
| Deduplication merges | Claude Haiku | ~$0.20 |
| Batch improve (40 pages) | Sonnet + Haiku | ~$3.00 |
| Web improvements during dev | Claude Sonnet | ~$13.00 |
| **Total** | | **~$20** |

The ~$13 in web improvements reflects the interactive development process (this README was written with that project context). A clean run of just the pipeline would cost ~$5–7 for 4,000 conversations.

---

## What the Wiki Reveals

Running this pipeline on 3+ years of ChatGPT conversations surfaces patterns that are invisible in the raw conversations:

- **Recurring questions** — the same unresolved tensions appear across dozens of conversations under different guises. The Open Questions page makes this visible.
- **Expertise depth** — some topics have 40+ conversations, revealing genuine deep engagement. Others have 2–3, revealing surface curiosity.
- **Project archaeology** — half-started projects, abandoned experiments, and decisions made and forgotten resurface with full context.
- **Knowledge gaps** — topics you've discussed but never synthesized, where the wiki page is thin despite many conversations.

---

## Future Directions

- **Ingest other sources** — email, bookmarks, notes apps, Anthropic exports
- **Better conversation selection** — use embeddings to find conversations most relevant to a thin page
- **Question resolution tracking** — mark questions as answered/in-progress without AI involvement
- **Related questions** — surface questions from other pages that relate to the one you're viewing
- **Timeline view** — see how your thinking on a topic evolved over time

---

## Credits

- [Andrej Karpathy's LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — the pattern this is built on
- [Anthropic Claude](https://anthropic.com) — classification, synthesis, improvement
- [Flask](https://flask.palletsprojects.com/), [Bootstrap 5](https://getbootstrap.com/), [markdown2](https://github.com/trentm/python-markdown2)
