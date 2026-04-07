# I Turned 3 Years of ChatGPT Conversations Into a Personal Knowledge Wiki

*And it cost about $20.*

---

I've been using ChatGPT almost every day since early 2023. Over time I accumulated 4,103 conversations — 293MB of raw JSON — covering everything from Docker networking and LLM architecture to half-finished side projects and personal decisions I'd talked through with the model.

The conversations were there, but they weren't *useful*. Finding anything required knowing roughly when you discussed it and scrolling through chat history. More importantly, the knowledge was trapped in conversational form — buried in back-and-forth exchanges, never synthesized into anything I could actually reference.

I spent a couple of days turning that into a structured personal knowledge wiki. Here's how it works and what I learned.

---

## The Core Idea: Karpathy's LLM Wiki Pattern

The whole thing is built around [a pattern described by Andrej Karpathy](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). His insight is simple but powerful:

> The tedious part of maintaining a knowledge base is the bookkeeping — updating references, maintaining consistency — which causes humans to abandon wikis. LLMs don't get bored and can touch 15 files simultaneously, making maintenance nearly cost-free.

His pattern has three layers:
1. **Raw Sources** — immutable documents as the source of truth
2. **The Wiki** — LLM-generated markdown organized by concept, with cross-references
3. **The Schema** — a configuration file documenting wiki structure and conventions

I applied this to a source most people already have: their own ChatGPT conversation history.

---

## What You End Up With

After running the pipeline:

- **~1,200 wiki pages** organized by category (ai, projects, research, tech)
- **3,500+ open questions** aggregated from across the wiki — the things I haven't resolved yet
- **A web app** with sidebar navigation, full-text search, and per-page AI improvement
- **`[[Wiki links]]`** that actually resolve to internal pages
- **A living document** — pages improve over time as you use an Improve button that pulls in the original source conversations

---

## Step 1: Get Your Data

ChatGPT lets you export your full conversation history. Go to Settings → Data Controls → Export data. You'll get a zip file with a `conversations.json` that contains everything.

Mine was 293MB — 4,103 conversations spanning three years.

---

## Step 2: Classify Every Conversation

The first pass uses Claude Haiku to classify each conversation. For each one, I extract the first ~2,000 characters (enough to classify accurately, cheap enough to do at scale) and ask the model:

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
```

The `sensitive` flag routes personal conversations to a separate output. The `trivial` category gets dropped entirely. Only conversations scoring ≥ 3 make it into wiki pages.

This runs at 15 workers in parallel. For 4,100 conversations: **~45 minutes, ~$0.65 using Claude Haiku**.

---

## Step 3: Synthesize Wiki Pages

The classified conversations get grouped by topic key (`category/topic`). For each group, Claude synthesizes a wiki page following a consistent schema:

```markdown
# Category / Topic Name

## Summary
2-3 sentence synthesis of the user's knowledge and perspective on this topic.

## Key Points
- Specific insights, techniques, decisions, and lessons learned

## Tools & Resources
- [Tool Name](https://url) — description
- [[Related Wiki Page]] — internal cross-reference

## Open Questions
- Unresolved questions, things worth investigating further

## Related
- [[Topic A]]
```

The synthesis prompt explicitly asks Claude to *synthesize, not list* — find patterns, note what worked, keep it information-dense. The output is a reference document, not a transcript summary.

This also runs at 15 workers in parallel. For ~1,200 pages: **~2 hours, ~$4.00 using Claude Haiku**.

The initial Haiku-generated pages are good but not deep. The Improve feature (below) is where they get really useful.

---

## Step 4: Build the Web App

A Flask app provides:

- **Sidebar navigation** with auto-grouped prefixes — all `docker_*` pages grouped as "docker (8)"
- **Full-text search** with multi-word AND matching and results grouped by category
- **See Also bar** on each page — automatic cross-links to same-named pages in other categories
- **`[[Wiki links]]`** resolved to internal pages with n-gram fuzzy matching
- **Statistics dashboard** — charts about your conversation corpus
- **Open Questions page** — all questions aggregated, with a feed of recently updated ones

The sidebar grouping was a necessity. With 1,273 pages across 4 categories, showing a flat list was useless. The prefix grouping is automatic — split on `_` or `-`, group anything with 2+ pages under that prefix, singletons become "other".

---

## Step 5: Improve Individual Pages

This is the highest-leverage feature. Each wiki page was initially generated from a narrow slice of conversations. The Improve flow goes deeper:

1. **Loads the top-10 highest-value source conversations** that contributed to the page
2. **Lets Claude request related wiki pages** — it can call a `read_wiki_page` tool to pull in cross-referenced pages before rewriting
3. **Calls Claude Sonnet** to rewrite the page with full context

The improve prompt is deliberately constrained:

```
Fix vague or incomplete claims by making them specific (versions, dates, outcomes)
Sharpen "Open Questions" — replace generic questions with ones that reflect real
decisions or unresolved tensions evident in the page
Remove filler content: obvious statements, redundant bullet points, vague aspirations
Do not invent projects, tools, or outcomes not already evidenced in the page
```

That last rule matters. Without it, the model adds plausible-sounding details. With it, the page gets sharper but stays accurate.

**Cost per improvement: ~$0.07** (Haiku pre-summarization + Sonnet synthesis with source conversations).

---

## Step 6: Handle Duplicates

The classifier sometimes routes the same topic to multiple categories, or creates `docker_networking` and `docker-networking` as separate keys. A deduplication script finds these, calls Haiku to merge the content, and deletes the duplicate.

I ran two passes:
- **9 same-category merges** — plural/spelling variants (`emergent_behavior` + `emergent_behaviors`, etc.)
- **24 cross-category merges** — same topic in multiple categories (`docker` in projects/ + tech/, `python` in projects/ + research/ + tech/, etc.)

Each merge was followed by an improve pass with Sonnet + source conversations, since merged pages often have the best material.

---

## The Open Questions Page

This turned out to be the most intellectually honest part of the whole project.

Every wiki page has an "Open Questions" section — unresolved tensions, things worth investigating further, genuine uncertainty. Extracted and aggregated across all pages, these become a window into patterns of thinking you didn't know you had.

Some questions appear in slightly different forms across a dozen different pages — the same unresolved tension showing up in different domains. Others are deeply specific but clearly important. The feed of recently-updated questions (sorted by when the underlying page was last modified or improved) gives you a rolling view of what's been touched lately.

I added inline human notes — a ✏️ button on each question lets you add your own thoughts. These are saved to a local JSON file and never touched by AI. The distinction matters: the wiki is AI-synthesized, the notes are yours.

---

## What the Wiki Actually Reveals

Running this on three years of conversations surfaces things that are invisible in the raw history:

**Recurring questions**: The same unresolved tensions appear across dozens of conversations under different guises. The Open Questions page makes this visible in a way that's genuinely surprising.

**Expertise depth**: Some topics have 40+ conversations, revealing genuine deep engagement. Others have 2–3, revealing surface curiosity that felt deeper in the moment.

**Project archaeology**: Half-started experiments and decisions made-and-forgotten resurface with full context. It's like reading an archaeological dig of your own thinking.

**Knowledge gaps**: Topics you've discussed repeatedly but never synthesized, where the wiki page is thin despite many conversations — these are worth improving first.

---

## Costs

For 4,103 conversations, total cost was about **$20**:

| Step | Model | Cost |
|------|-------|------|
| Classification | Claude Haiku | ~$0.65 |
| Wiki generation (~1,200 pages) | Claude Haiku | ~$4.00 |
| Deduplication merges | Claude Haiku | ~$0.20 |
| Batch improve (40 pages) | Sonnet + Haiku | ~$3.00 |
| Web improvements during dev | Claude Sonnet | ~$13.00 |

The $13 in web improvements reflects interactive development — every time I hit the Improve button while building the feature, that was Sonnet with full source context. A clean run of just the pipeline would cost **$5–7** for 4,000 conversations.

---

## What I'd Do Next

The pipeline is built around ChatGPT exports, but the pattern works on anything:

- **Email** — years of written communication, searchable by topic
- **Bookmarks** — synthesize what you've actually read and why you saved it
- **Notes apps** — Obsidian, Notion, Bear exports
- **Anthropic exports** — Claude conversations work the same way

The other thing I want is better conversation selection for improvement. Right now it picks top-10 by value score from the classified file. Using embeddings to find the conversations *most semantically relevant to a thin page* would be sharper.

---

## The Deeper Point

I thought three years of conversations with an AI tool would have value — and it does, just not in the way I expected. The value isn't the raw conversations. It's what you can synthesize from them.

A conversation is ephemeral by nature — it has context, it has back-and-forth, it was useful *when you had it*. But the knowledge in it doesn't have to be. The wiki extracts what's durable: the insights, the techniques, the decisions, the questions you're still working through.

Karpathy's framing is right: the hard part of maintaining a knowledge base has always been the bookkeeping. LLMs remove that friction almost entirely. The result is a compounding artifact that gets better the more you use it.

---

*Full source code and detailed setup instructions: [github.com/adrianhensler/chatgpt-wiki-builder](https://github.com/adrianhensler/chatgpt-wiki-builder)*

*Built on [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).*
