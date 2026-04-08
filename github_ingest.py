#!/usr/bin/env python3
"""
GitHub Repo → Wiki Page ingestion.

Fetches README, recent commits, and file structure from GitHub repos
and synthesizes wiki pages using Claude Haiku.

Usage:
    python3 github_ingest.py
    python3 github_ingest.py --force        # overwrite existing pages
    python3 github_ingest.py --dry-run      # preview without writing
    python3 github_ingest.py --repos podcast-generator botterverse
"""

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import anthropic
from tqdm import tqdm

WIKI_DIR = Path("wiki")
OWNER = "adrianhensler"
MODEL = "claude-haiku-4-5-20251001"

REPOS = [
    "podcast-generator",
    "hensler-photography",
    "test.hensler.work",
    "botterverse",
    "Demando",
    "audio_seg_gui",
    "canadian-tax-chatbot",
    "family_KCS",
    "chatgpt_export_rag",
    "ouroboros",
    "model_council_cli",
]

INGEST_PROMPT = """You are maintaining a personal knowledge base wiki for a developer/entrepreneur.

Generate a wiki page for the GitHub project "{repo}" based on the information below.
This person built this project — write about what it does, how it works, and what's interesting about it.

## Repository information:
{repo_info}

## Instructions:
- Follow this structure exactly: Summary, Key Points, Tools & Resources, Open Questions, Related
- Summary: 2-3 sentences on what it is and what problem it solves
- Key Points: architecture decisions, approach, what's technically interesting, current status
- Tools & Resources: use [Name](url) for external tools/frameworks. Use [[Page Name]] for related wiki pages.
- Open Questions: genuine unresolved questions about the project — where it could go, what's uncertain
- Related: [[Page Name]] links to related wiki pages (other projects, tech topics)

## Voice rules:
- "This project does X" not "the user believes X"
- Record what was built and how, not opinions
- If the project is experimental or incomplete, say so

Output ONLY the markdown wiki page. No preamble, no explanation."""


def gh_api(endpoint: str) -> dict | list | None:
    """Call gh api and return parsed JSON."""
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def fetch_repo_info(repo: str) -> str:
    """Fetch README, commits, file listing and return as a context string."""
    parts = []

    # Description
    meta = gh_api(f"repos/{OWNER}/{repo}")
    if meta and isinstance(meta, dict):
        desc = meta.get("description", "")
        lang = meta.get("language", "")
        pushed = meta.get("pushed_at", "")[:10]
        parts.append(f"Description: {desc}")
        parts.append(f"Primary language: {lang}")
        parts.append(f"Last updated: {pushed}")

    # README
    readme_data = gh_api(f"repos/{OWNER}/{repo}/readme")
    if readme_data and isinstance(readme_data, dict):
        content = readme_data.get("content", "")
        try:
            readme_text = base64.b64decode(content).decode("utf-8", errors="replace")
            # Truncate to avoid huge prompts
            if len(readme_text) > 4000:
                readme_text = readme_text[:4000] + "\n\n[...README truncated]"
            parts.append(f"\n## README:\n{readme_text}")
        except Exception:
            pass

    # Recent commits
    commits = gh_api(f"repos/{OWNER}/{repo}/commits?per_page=15")
    if commits and isinstance(commits, list):
        commit_lines = []
        for c in commits:
            msg = c.get("commit", {}).get("message", "").split("\n")[0][:100]
            date = c.get("commit", {}).get("author", {}).get("date", "")[:10]
            commit_lines.append(f"  {date}: {msg}")
        parts.append("\n## Recent commits:\n" + "\n".join(commit_lines))

    # Top-level file listing
    files = gh_api(f"repos/{OWNER}/{repo}/contents")
    if files and isinstance(files, list):
        names = [f["name"] for f in files if isinstance(f, dict)]
        parts.append("\n## Top-level files:\n" + ", ".join(names))

    return "\n".join(parts)


def repo_to_slug(repo: str) -> str:
    """Convert repo name to wiki filename slug."""
    return repo.lower().replace(".", "_").replace("-", "_")


def generate_wiki_page(client: anthropic.Anthropic, repo: str, repo_info: str) -> str:
    """Call Claude Haiku to generate a wiki page."""
    prompt = INGEST_PROMPT.format(repo=repo, repo_info=repo_info)
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


def main():
    parser = argparse.ArgumentParser(description="Ingest GitHub repos into wiki")
    parser.add_argument("--force", action="store_true", help="Overwrite existing pages")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write")
    parser.add_argument("--repos", nargs="+", help="Specific repos to process")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key) if not args.dry_run else None

    repos = args.repos if args.repos else REPOS
    WIKI_DIR.mkdir(exist_ok=True)
    (WIKI_DIR / "projects").mkdir(exist_ok=True)

    skipped, written, failed = [], [], []

    for repo in tqdm(repos, desc="Ingesting repos"):
        slug = repo_to_slug(repo)
        out_path = WIKI_DIR / "projects" / f"{slug}.md"

        if out_path.exists() and not args.force:
            skipped.append(repo)
            continue

        repo_info = fetch_repo_info(repo)
        if not repo_info.strip():
            print(f"\nWarning: no data fetched for {repo}")
            failed.append(repo)
            continue

        if args.dry_run:
            print(f"\n--- {repo} ---")
            print(repo_info[:500])
            written.append(repo)
            continue

        try:
            page = generate_wiki_page(client, repo, repo_info)
            out_path.write_text(page)
            written.append(repo)
        except Exception as e:
            print(f"\nError generating page for {repo}: {e}")
            failed.append(repo)

    print(f"\nDone.")
    print(f"  Written: {len(written)} — {written}")
    if skipped:
        print(f"  Skipped (exist): {len(skipped)} — {skipped}")
    if failed:
        print(f"  Failed: {len(failed)} — {failed}")
    print(f"\nPages in: wiki/projects/")


if __name__ == "__main__":
    main()
