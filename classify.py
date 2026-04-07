#!/usr/bin/env python3
"""
Phase 1: Classify all conversations.

Reads conversations.json, sends batches to Claude Haiku for classification.
Outputs classified.jsonl — one JSON object per conversation.

Resume-safe: skips already-classified conversation IDs.

Usage:
    python3 classify.py [--dry-run] [--batch-size 30]
"""

import json
import argparse
import datetime
import os
import sys
import time
from pathlib import Path

import anthropic
from tqdm import tqdm

CONVERSATIONS_FILE = "conversations.json"
OUTPUT_FILE = "classified.jsonl"
MODEL = "claude-haiku-4-5-20251001"

CLASSIFY_PROMPT = """You are classifying ChatGPT conversations for a personal knowledge base.

For each conversation below, output a JSON array where each element has:
- "id": the conversation id (copy exactly)
- "category": one of: "tech", "ai", "research", "projects", "personal", "trivial"
- "value": integer 1-5 (5=highly valuable durable knowledge, 1=trivial/transient)
- "sensitive": true if contains health, medical, relationships, finances, named individuals, or personal struggles
- "summary": one sentence describing what it's about (be specific)
- "topics": list of 1-3 keyword tags

Category guidance:
- tech: programming, software, debugging, APIs, frameworks, languages
- ai: AI/ML models, tools, prompting, agents, Replicate, OpenAI, Anthropic, LLMs
- research: factual lookups, how-tos, domain knowledge (networking, hardware, food, etc.)
- projects: a specific build/project being worked on (game, app, tool, home project)
- personal: health, relationships, career anxiety, life decisions, finances, emotions
- trivial: one-off simple lookups, very short exchanges, jokes, nothing durable

Value guidance:
- 5: Deep exploration of a topic, something worth referencing again
- 4: Solid knowledge, useful patterns or findings
- 3: Some value, maybe a useful reference or project note
- 2: Mostly transient, minimal durable knowledge
- 1: Skip-worthy (trivial question, simple lookup, or too short to matter)

Conversations:
{conversations}

Respond with ONLY a valid JSON array. No markdown, no explanation."""


def extract_conversation_text(convo: dict, max_chars: int = 800) -> str:
    """Extract a brief readable summary of a conversation for classification."""
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

    # Build a truncated representation
    result = []
    chars = 0
    for _, role, text in msgs:
        if chars >= max_chars:
            break
        prefix = "User: " if role == "user" else "AI: "
        snippet = text[:max_chars - chars]
        result.append(f"{prefix}{snippet}")
        chars += len(snippet)

    return "\n".join(result)


def load_classified_ids(output_file: str) -> set:
    """Load already-classified conversation IDs for resume support."""
    classified = set()
    if Path(output_file).exists():
        with open(output_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        classified.add(obj["id"])
                    except (json.JSONDecodeError, KeyError):
                        pass
    return classified


def classify_batch(client: anthropic.Anthropic, batch: list[dict], dry_run: bool = False) -> list[dict]:
    """Classify a batch of conversations. Returns list of classification objects."""
    if dry_run:
        return [
            {
                "id": c["id"],
                "category": "tech",
                "value": 3,
                "sensitive": False,
                "summary": "[dry-run]",
                "topics": ["dry-run"],
            }
            for c in batch
        ]

    # Build conversation snippets for the prompt
    conv_texts = []
    for c in batch:
        date = ""
        if c.get("create_time"):
            date = datetime.datetime.fromtimestamp(c["create_time"]).strftime("%Y-%m-%d")
        title = c.get("title", "Untitled")
        text = extract_conversation_text(c)
        conv_texts.append(
            f'ID: {c["id"]}\nDate: {date}\nTitle: {title}\n{text}\n---'
        )

    prompt = CLASSIFY_PROMPT.format(conversations="\n".join(conv_texts))

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            results = json.loads(raw)

            # Validate and fill in missing IDs
            result_map = {r["id"]: r for r in results if "id" in r}
            output = []
            for c in batch:
                if c["id"] in result_map:
                    output.append(result_map[c["id"]])
                else:
                    # Fallback if LLM missed this one
                    output.append({
                        "id": c["id"],
                        "category": "trivial",
                        "value": 1,
                        "sensitive": False,
                        "summary": "Classification failed",
                        "topics": [],
                    })
            return output

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            if attempt == 2:
                print(f"\nWarning: batch classification failed after 3 attempts: {e}")
                # Return fallback for entire batch
                return [
                    {
                        "id": c["id"],
                        "category": "trivial",
                        "value": 1,
                        "sensitive": False,
                        "summary": "Classification error",
                        "topics": [],
                    }
                    for c in batch
                ]
            time.sleep(2 ** attempt)

    return []


def main():
    parser = argparse.ArgumentParser(description="Classify ChatGPT conversations")
    parser.add_argument("--dry-run", action="store_true", help="Don't call API, just test pipeline")
    parser.add_argument("--batch-size", type=int, default=25, help="Conversations per API call (default: 25)")
    parser.add_argument("--limit", type=int, default=0, help="Process only N conversations (0=all)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        print("Error: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key) if not args.dry_run else None

    print(f"Loading {CONVERSATIONS_FILE}...")
    with open(CONVERSATIONS_FILE) as f:
        conversations = json.load(f)

    print(f"Total conversations: {len(conversations)}")

    # Filter out conversations with no meaningful content
    conversations = [c for c in conversations if c.get("id") and c.get("title")]
    print(f"After filtering empty: {len(conversations)}")

    if args.limit:
        conversations = conversations[:args.limit]
        print(f"Limited to: {len(conversations)}")

    # Sort by date for consistent ordering
    conversations.sort(key=lambda c: c.get("create_time", 0))

    # Load already-classified IDs
    already_done = load_classified_ids(OUTPUT_FILE)
    remaining = [c for c in conversations if c["id"] not in already_done]
    print(f"Already classified: {len(already_done)}, remaining: {len(remaining)}")

    if not remaining:
        print("All conversations already classified!")
        return

    # Process in batches
    batches = [remaining[i:i+args.batch_size] for i in range(0, len(remaining), args.batch_size)]
    print(f"Processing {len(remaining)} conversations in {len(batches)} batches of {args.batch_size}")
    print(f"Model: {MODEL}\n")

    total_classified = 0
    with open(OUTPUT_FILE, "a") as out_f:
        for batch in tqdm(batches, desc="Classifying"):
            results = classify_batch(client, batch, dry_run=args.dry_run)
            for r in results:
                out_f.write(json.dumps(r) + "\n")
            out_f.flush()
            total_classified += len(results)

    print(f"\nDone! Classified {total_classified} conversations.")
    print(f"Output: {OUTPUT_FILE}")
    print(f"\nNext steps:")
    print(f"  1. Review classified.jsonl (check sensitive=true items, adjust categories)")
    print(f"  2. Run: python3 ingest.py")


if __name__ == "__main__":
    main()
