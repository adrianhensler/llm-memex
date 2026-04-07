# Knowledge Base Schema

## Purpose
A personal wiki synthesized from ChatGPT conversations (2023–2026).
Captures durable knowledge: programming patterns, research findings, tools explored, recurring projects.
Does NOT contain personal health, relationship, or sensitive life data (handled separately).

## Categories

### tech
Programming, software development, APIs, languages, frameworks, debugging.
Pages live in: `wiki/tech/`

### ai
AI/ML tools, models, prompting, APIs (OpenAI, Anthropic, Replicate, etc.), agents, workflows.
Pages live in: `wiki/ai/`

### research
Factual research, how-tos, domain knowledge (networking, hardware, home improvement, etc.).
Pages live in: `wiki/research/`

### projects
Specific projects or builds the user worked on across multiple conversations.
Pages live in: `wiki/projects/`

### personal
Health, relationships, career struggles, life decisions, finances.
Output goes to: `personal/` (separate, private stream — lighter-touch summary).

### trivial
One-off lookups, very short exchanges with no durable knowledge.
Skip entirely.

## Wiki Page Format

```markdown
# [Topic Title]

**Last updated:** YYYY-MM-DD
**Conversation count:** N

## Summary
2-3 sentence overview of what the user knows/explored in this area.

## Key Points
- Bullet points of durable knowledge, findings, patterns

## Tools & Resources
- Tools, libraries, services used or evaluated

## Projects & Experiments
- Specific things built or tried

## Open Questions / Next Steps
- Things left unresolved or worth revisiting

## Related
- Links to related wiki pages
```

## Special Files

- `wiki/index.md` — master index of all pages, organized by category
- `wiki/log.md` — append-only ingest log (date, batch, pages updated)
- `personal/summary.md` — high-level personal themes (no details)
- `classified.jsonl` — intermediate classification output (user-reviewable)
