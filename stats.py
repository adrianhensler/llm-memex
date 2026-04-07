#!/usr/bin/env python3
"""
Repeatable statistics generator for the karpathy_kb pipeline.

Reads conversations.json + classified.jsonl + wiki/ directory.
Outputs stats/stats.json and stats/report.md.

Usage:
    python3 stats.py
"""

import json
import datetime
import statistics
from collections import Counter, defaultdict
from pathlib import Path

CONVERSATIONS_FILE = "conversations.json"
CLASSIFIED_FILE = "classified.jsonl"
WIKI_DIR = Path("wiki")
STATS_DIR = Path("stats")


def get_messages(convo):
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
    return msgs


def load_conversations():
    print("Loading conversations.json...")
    with open(CONVERSATIONS_FILE) as f:
        return json.load(f)


def load_classified():
    items = []
    if not Path(CLASSIFIED_FILE).exists():
        return items
    with open(CLASSIFIED_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return items


def conversation_stats(conversations):
    stats = {}
    stats["total"] = len(conversations)

    timestamps = [c.get("create_time", 0) for c in conversations if c.get("create_time")]
    if timestamps:
        stats["date_range"] = {
            "start": datetime.datetime.fromtimestamp(min(timestamps)).strftime("%Y-%m-%d"),
            "end": datetime.datetime.fromtimestamp(max(timestamps)).strftime("%Y-%m-%d"),
        }

    # By year
    by_year = Counter()
    by_month = Counter()
    by_weekday = Counter()
    by_hour = Counter()
    for c in conversations:
        ts = c.get("create_time")
        if ts:
            dt = datetime.datetime.fromtimestamp(ts)
            by_year[str(dt.year)] += 1
            by_month[f"{dt.year}-{dt.month:02d}"] += 1
            by_weekday[dt.strftime("%A")] += 1
            by_hour[dt.hour] += 1

    stats["by_year"] = dict(sorted(by_year.items()))
    stats["by_month"] = dict(sorted(by_month.items()))
    stats["by_weekday"] = dict(by_weekday)
    stats["by_hour"] = {str(k): v for k, v in sorted(by_hour.items())}

    # Models used
    models = Counter(c.get("default_model_slug", "unknown") for c in conversations if c.get("default_model_slug"))
    stats["models_used"] = dict(models.most_common(20))

    # Conversation lengths
    lengths = []
    message_counts = []
    for c in conversations:
        msgs = get_messages(c)
        total_chars = sum(len(t) for _, _, t in msgs)
        lengths.append(total_chars)
        message_counts.append(len(msgs))

    lengths_nonzero = [l for l in lengths if l > 0]
    if lengths_nonzero:
        lengths_sorted = sorted(lengths_nonzero)
        n = len(lengths_sorted)
        stats["length_chars"] = {
            "min": min(lengths_nonzero),
            "median": int(statistics.median(lengths_nonzero)),
            "mean": int(statistics.mean(lengths_nonzero)),
            "p90": lengths_sorted[int(0.9 * n)],
            "p99": lengths_sorted[int(0.99 * n)],
            "max": max(lengths_nonzero),
            "total": sum(lengths_nonzero),
            "total_estimated_tokens": sum(lengths_nonzero) // 4,
        }

    if message_counts:
        stats["message_counts"] = {
            "min": min(message_counts),
            "median": int(statistics.median(message_counts)),
            "mean": round(statistics.mean(message_counts), 1),
            "max": max(message_counts),
            "total": sum(message_counts),
        }

    # Archived / starred
    stats["archived"] = sum(1 for c in conversations if c.get("is_archived"))
    stats["starred"] = sum(1 for c in conversations if c.get("is_starred"))

    return stats


def classification_stats(classified):
    if not classified:
        return {}

    stats = {}
    stats["total_classified"] = len(classified)

    # By category
    stats["by_category"] = dict(Counter(i.get("category") for i in classified).most_common())

    # By value
    stats["by_value"] = dict(sorted(Counter(i.get("value") for i in classified).items()))

    # Sensitive
    sensitive = [i for i in classified if i.get("sensitive")]
    stats["sensitive_count"] = len(sensitive)
    stats["sensitive_pct"] = round(100 * len(sensitive) / len(classified), 1)

    # Eligible for wiki
    eligible = [
        i for i in classified
        if not i.get("sensitive")
        and i.get("category") not in ("personal", "trivial")
        and i.get("value", 0) >= 3
    ]
    stats["eligible_for_wiki"] = len(eligible)

    # Top topics overall
    topic_counts = Counter()
    for i in classified:
        for t in i.get("topics", []):
            topic_counts[t.lower()] += 1
    stats["top_topics"] = dict(topic_counts.most_common(50))

    # Top topics by category
    by_cat_topics = defaultdict(Counter)
    for i in classified:
        cat = i.get("category", "unknown")
        for t in i.get("topics", []):
            by_cat_topics[cat][t.lower()] += 1
    stats["top_topics_by_category"] = {
        cat: dict(counter.most_common(10))
        for cat, counter in by_cat_topics.items()
    }

    # Category by year (trend)
    cat_by_year = defaultdict(Counter)
    for i in classified:
        # classified items don't have timestamps directly, skip for now
        pass

    return stats


def wiki_stats():
    stats = {}
    if not WIKI_DIR.exists():
        stats["status"] = "not built yet"
        return stats

    pages = [p for p in WIKI_DIR.rglob("*.md") if p.name not in ("index.md", "log.md")]
    stats["total_pages"] = len(pages)

    if not pages:
        return stats

    by_category = Counter()
    page_sizes = []
    total_words = 0

    for p in pages:
        rel = p.relative_to(WIKI_DIR)
        cat = rel.parts[0] if len(rel.parts) > 1 else "root"
        by_category[cat] += 1
        content = p.read_text()
        words = len(content.split())
        total_words += words
        page_sizes.append(words)

    stats["by_category"] = dict(by_category.most_common())
    stats["total_words"] = total_words
    stats["avg_words_per_page"] = int(total_words / len(pages)) if pages else 0
    stats["page_size_words"] = {
        "min": min(page_sizes),
        "median": int(statistics.median(page_sizes)),
        "max": max(page_sizes),
    }

    return stats


def write_markdown_report(all_stats):
    lines = [
        "# Knowledge Base Statistics",
        f"\n_Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}_\n",
    ]

    cs = all_stats.get("conversations", {})
    lines += [
        "## Conversations",
        f"- **Total:** {cs.get('total', 0):,}",
        f"- **Date range:** {cs.get('date_range', {}).get('start', '?')} → {cs.get('date_range', {}).get('end', '?')}",
        f"- **Total messages:** {cs.get('message_counts', {}).get('total', 0):,}",
        f"- **Estimated total tokens:** {cs.get('length_chars', {}).get('total_estimated_tokens', 0):,}",
        f"- **Avg messages/conversation:** {cs.get('message_counts', {}).get('mean', 0)}",
        f"- **Archived:** {cs.get('archived', 0)} | **Starred:** {cs.get('starred', 0)}",
        "",
        "### By Year",
    ]
    for year, count in cs.get("by_year", {}).items():
        lines.append(f"- {year}: {count:,} conversations")

    models = cs.get("models_used", {})
    if models:
        lines += ["", "### Models Used"]
        for model, count in list(models.items())[:10]:
            lines.append(f"- `{model}`: {count:,}")

    cls = all_stats.get("classification", {})
    if cls:
        lines += [
            "",
            "## Classification",
            f"- **Total classified:** {cls.get('total_classified', 0):,}",
            f"- **Eligible for wiki:** {cls.get('eligible_for_wiki', 0):,}",
            f"- **Flagged sensitive:** {cls.get('sensitive_count', 0):,} ({cls.get('sensitive_pct', 0)}%)",
            "",
            "### By Category",
        ]
        for cat, count in cls.get("by_category", {}).items():
            lines.append(f"- {cat}: {count:,}")

        lines += ["", "### By Value Score"]
        for v, count in sorted(cls.get("by_value", {}).items()):
            lines.append(f"- Score {v}: {count:,}")

        topics = cls.get("top_topics", {})
        if topics:
            lines += ["", "### Top 30 Topics"]
            for topic, count in list(topics.items())[:30]:
                lines.append(f"- {topic}: {count}")

    ws = all_stats.get("wiki", {})
    if ws.get("total_pages", 0) > 0:
        lines += [
            "",
            "## Wiki",
            f"- **Total pages:** {ws.get('total_pages', 0)}",
            f"- **Total words:** {ws.get('total_words', 0):,}",
            f"- **Avg words/page:** {ws.get('avg_words_per_page', 0)}",
            "",
            "### Pages by Category",
        ]
        for cat, count in ws.get("by_category", {}).items():
            lines.append(f"- {cat}: {count} pages")

    return "\n".join(lines) + "\n"


def main():
    STATS_DIR.mkdir(exist_ok=True)

    conversations = load_conversations()
    classified = load_classified()

    print("Computing conversation stats...")
    all_stats = {
        "generated_at": datetime.datetime.now().isoformat(),
        "conversations": conversation_stats(conversations),
        "classification": classification_stats(classified),
        "wiki": wiki_stats(),
    }

    # Write JSON
    json_path = STATS_DIR / "stats.json"
    with open(json_path, "w") as f:
        json.dump(all_stats, f, indent=2)
    print(f"Written: {json_path}")

    # Write markdown report
    report = write_markdown_report(all_stats)
    report_path = STATS_DIR / "report.md"
    report_path.write_text(report)
    print(f"Written: {report_path}")

    # Print summary
    cs = all_stats["conversations"]
    cls = all_stats["classification"]
    print(f"\n{'='*40}")
    print(f"Conversations: {cs.get('total',0):,}")
    print(f"Date range:    {cs.get('date_range',{}).get('start','?')} → {cs.get('date_range',{}).get('end','?')}")
    print(f"Total tokens:  ~{cs.get('length_chars',{}).get('total_estimated_tokens',0):,}")
    print(f"Classified:    {cls.get('total_classified',0):,}")
    print(f"Wiki eligible: {cls.get('eligible_for_wiki',0):,}")
    print(f"Wiki pages:    {all_stats['wiki'].get('total_pages',0)}")


if __name__ == "__main__":
    main()
