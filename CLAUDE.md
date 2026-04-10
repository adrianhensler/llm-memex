# Claude Instructions — llm-memex

## Wiki as context

This project contains a personal knowledge wiki in `wiki/` (~1,200 pages) synthesized from the owner's LLM conversation history.

When working on any topic where prior knowledge is likely relevant:
1. Read `wiki/index.md` to find related pages
2. Read the relevant wiki page(s) before responding
3. Reference what's already known rather than starting from scratch — note gaps, contradictions, or updates rather than repeating what's already captured

This applies especially to: technical decisions, project work, AI/ML topics, research questions, and anything where "what have I already tried/learned?" is a useful frame.

## Voice

When discussing wiki content, use neutral framing ("this topic was explored", "discussion covered X") rather than asserting beliefs ("the user believes X"). See provenance notes in `ingest.py`.
