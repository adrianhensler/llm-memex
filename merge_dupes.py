#!/usr/bin/env python3
"""
Merge duplicate wiki pages (hyphen vs underscore naming variants).

Finds pairs like ai/ai_agents.md + ai/ai-agents.md, merges them intelligently
using Claude Haiku, writes the result to the underscore-named file, and removes
the hyphen-named file.

Usage:
    python3 merge_dupes.py [--dry-run] [--workers N]
"""

import argparse
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import anthropic
from tqdm import tqdm

WIKI_DIR = Path("wiki")
MODEL = "claude-haiku-4-5-20251001"
_write_lock = Lock()

MERGE_PROMPT = """\
You are merging two versions of the same wiki page from a personal knowledge base.
Both pages cover the same topic but were created separately and may have different \
or complementary content. Produce a single, unified page that preserves ALL unique \
information from both versions.

Rules:
- Keep every distinct fact, example, project note, open question, and lesson
- Eliminate exact duplicates and near-duplicates (same point said twice)
- Maintain the standard wiki schema sections (Summary, Key Points, Tools & Resources, \
Projects & Experiments, Open Questions, Related)
- If one version has a section the other lacks, include it
- Be concise but don't drop content — prefer a longer result over a shorter one that loses information
- Output ONLY the merged markdown, no preamble

## Version A ({name_a}):
{content_a}

## Version B ({name_b}):
{content_b}
"""


def find_duplicate_groups():
    def norm_key(cat, stem):
        return cat + "/" + re.sub(r"[-_]+", "_", stem.lower())

    groups = defaultdict(list)
    for p in sorted(WIKI_DIR.rglob("*.md")):
        if p.name in ("index.md", "log.md"):
            continue
        if ".__" in p.name:
            continue
        rel = p.relative_to(WIKI_DIR)
        parts = rel.parts
        cat = parts[0] if len(parts) > 1 else "other"
        groups[norm_key(cat, p.stem)].append(p)

    return {k: v for k, v in groups.items() if len(v) > 1}


def canonical_path(files):
    """Pick the underscore version as canonical; if none, the alphabetically first."""
    underscore = [f for f in files if "_" in f.stem]
    return underscore[0] if underscore else sorted(files)[0]


def merge_pair(client, key, files, dry_run):
    canon = canonical_path(files)
    others = [f for f in files if f != canon]

    content_a = canon.read_text().strip()
    # Merge all others into canon sequentially (usually just one other)
    merged = content_a
    for other in others:
        content_b = other.read_text().strip()
        if dry_run:
            merged = f"[dry-run merge of {canon.name} + {other.name}]"
            continue
        prompt = MERGE_PROMPT.format(
            name_a=canon.name,
            content_a=merged,
            name_b=other.name,
            content_b=content_b,
        )
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                )
                merged = response.content[0].text.strip()
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    if not dry_run:
        with _write_lock:
            canon.write_text(merged)
            for other in others:
                other.unlink()

    return canon, others


def main():
    parser = argparse.ArgumentParser(description="Merge duplicate wiki pages")
    parser.add_argument("--dry-run", action="store_true", help="Don't call API or delete files")
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    groups = find_duplicate_groups()
    print(f"Found {len(groups)} duplicate groups to merge")
    for k, files in sorted(groups.items())[:10]:
        print(f"  {k}: {[f.name for f in files]}")
    if len(groups) > 10:
        print(f"  ... and {len(groups) - 10} more")

    if not groups:
        print("Nothing to merge.")
        return

    if not args.dry_run:
        client = anthropic.Anthropic()
    else:
        client = None
        print("\n[dry-run mode — no API calls, no file changes]\n")

    merged_count = 0
    failed = []

    def task(item):
        key, files = item
        try:
            canon, removed = merge_pair(client, key, files, args.dry_run)
            return canon, removed, None
        except Exception as e:
            return None, [], str(e)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(task, item): item[0] for item in groups.items()}
        with tqdm(total=len(futures), desc="Merging") as pbar:
            for future in as_completed(futures):
                canon, removed, err = future.result()
                if err:
                    failed.append(f"{futures[future]}: {err}")
                else:
                    merged_count += 1
                pbar.update(1)

    print(f"\nMerged {merged_count} groups.")
    if failed:
        print(f"{len(failed)} failures:")
        for f in failed:
            print(f"  {f}")


if __name__ == "__main__":
    main()
