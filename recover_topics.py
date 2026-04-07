#!/usr/bin/env python3
"""
Single-pass recovery: load corpus once, regenerate a list of topic keys in parallel.
Usage: python3 recover_topics.py topics.txt [--workers N]
"""
import argparse
import sys
from pathlib import Path

# Re-use ingest helpers directly
sys.path.insert(0, str(Path(__file__).parent))
from ingest import (
    load_classified, load_conversations_by_id, extract_full_text,
    group_by_topic, update_wiki_page, write_wiki_page, get_wiki_page_path,
    append_log, WIKI_MODEL,
)

import anthropic
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from tqdm import tqdm

_write_lock = Lock()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("topics_file", help="File with one topic key per line")
    parser.add_argument("--workers", type=int, default=15)
    args = parser.parse_args()

    target_keys = set()
    with open(args.topics_file) as f:
        for line in f:
            line = line.strip()
            if line:
                target_keys.add(line)

    print(f"Target topics: {len(target_keys)}")

    # Load data once
    print("Loading classified conversations...")
    classified = load_classified(min_value=3)
    print(f"  {len(classified)} qualifying conversations")

    print("Loading full conversation texts...")
    ids = {item["id"] for item in classified}
    conv_map = load_conversations_by_id(ids)
    conv_texts = {cid: extract_full_text(convo) for cid, convo in conv_map.items()}

    # Group and filter to target keys
    all_groups = group_by_topic(classified)
    groups = {k: v for k, v in all_groups.items() if k in target_keys}

    missing = target_keys - set(groups.keys())
    if missing:
        print(f"Warning: {len(missing)} topic keys not found in classified data: {sorted(missing)[:5]}")

    print(f"Processing {len(groups)} topics with {args.workers} workers...\n")

    client = anthropic.Anthropic()
    failed = []

    def process(item):
        topic_key, items = item
        category = items[0].get("category", "research")
        try:
            content = update_wiki_page(
                client, topic_key, category, items, conv_texts,
                WIKI_MODEL, use_openrouter=False, dry_run=False,
            )
            page_path = get_wiki_page_path(topic_key)
            with _write_lock:
                write_wiki_page(page_path, content)
            return topic_key, None
        except Exception as e:
            return topic_key, str(e)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process, item): item[0] for item in groups.items()}
        with tqdm(total=len(futures), desc="Recovering") as pbar:
            for future in as_completed(futures):
                key, err = future.result()
                if err:
                    failed.append(f"{key}: {err}")
                pbar.update(1)

    if failed:
        print(f"\n{len(failed)} failures:")
        for f in failed:
            print(f"  {f}")
    else:
        print(f"\nDone. {len(groups)} topics recovered.")

    append_log(f"recover_topics: regenerated {len(groups)} topics")


if __name__ == "__main__":
    main()
