#!/usr/bin/env python3
"""Seed articles from all_eu_content.json into a running Evrópuvefur API.

Designed for first-time local setup. Reads CMS_API_KEY from .env automatically.

Usage:
    python scripts/seed_articles.py                        # local defaults
    python scripts/seed_articles.py --api-url https://...  # remote API
    python scripts/seed_articles.py --api-key my-key       # explicit key
"""

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

# Relative to this script's location inside evropuvefur_api/scripts/
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent  # evropuvefur_api/
DEFAULT_JSON = PROJECT_DIR.parent / "2025_evropuvefur_rag" / "eu_content_json" / "all_eu_content.json"
DEFAULT_ENV = PROJECT_DIR / ".env"


def read_env_value(env_path: Path, key: str) -> str | None:
    """Read a single value from a .env file (simple KEY=VALUE parser)."""
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip()
    return None


def strip_svar_prefix(answer: str) -> str:
    """Remove leading 'Svar\\n\\n' prefix from answer text."""
    return re.sub(r"^Svar\n\n", "", answer)


def load_articles(path: Path) -> list[dict]:
    with open(path) as f:
        raw = json.load(f)

    articles = []
    for item in raw:
        articles.append({
            "id": item["id"],
            "title": item["title"],
            "question": item["question"],
            "answer": strip_svar_prefix(item["answer"]),
            "source_url": item["source_url"],
            "date": item["date"],
            "author": item["author"],
            "categories": item.get("categories", []),
            "tags": [],
        })
    return articles


def main():
    parser = argparse.ArgumentParser(
        description="Seed articles from all_eu_content.json into the Evrópuvefur API"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_JSON,
        help=f"Path to all_eu_content.json (default: {DEFAULT_JSON})",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="Base URL of the API (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="CMS API key (default: read from .env)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Articles per batch, max 100 (default: 100)",
    )
    args = parser.parse_args()

    # Resolve API key
    api_key = args.api_key or read_env_value(DEFAULT_ENV, "CMS_API_KEY")
    if not api_key:
        print("Error: No API key provided. Either pass --api-key or set CMS_API_KEY in .env")
        sys.exit(1)
    if api_key == "change-me-to-a-secret":
        print("Warning: Using the default CMS_API_KEY. Change it in .env for production.")

    # Load articles
    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    articles = load_articles(args.input)
    print(f"Loaded {len(articles)} articles from {args.input}")
    print(f"Target: {args.api_url}")
    print()

    total_created = 0
    total_updated = 0
    total_failed = 0
    all_errors = []
    num_batches = (len(articles) + args.batch_size - 1) // args.batch_size

    with httpx.Client(timeout=120) as client:
        for i in range(0, len(articles), args.batch_size):
            batch = articles[i : i + args.batch_size]
            batch_num = i // args.batch_size + 1
            print(f"[{batch_num}/{num_batches}] Sending {len(batch)} articles...", end=" ", flush=True)

            try:
                resp = client.post(
                    f"{args.api_url}/api/v1/articles/bulk",
                    json={"articles": batch},
                    headers={"X-API-Key": api_key},
                )
            except httpx.ConnectError:
                print(f"\nError: Cannot connect to {args.api_url}. Is the API running?")
                sys.exit(1)

            if resp.status_code != 200:
                print(f"ERROR (HTTP {resp.status_code})")
                total_failed += len(batch)
                continue

            data = resp.json()
            total_created += data["created"]
            total_updated += data["updated"]
            total_failed += data["failed"]
            all_errors.extend(data.get("errors", []))

            print(f"created={data['created']} updated={data['updated']} failed={data['failed']}")

    print(f"\n{'='*50}")
    print(f"Seed complete!")
    print(f"  Total articles: {len(articles)}")
    print(f"  Created: {total_created}")
    print(f"  Updated: {total_updated}")
    print(f"  Failed:  {total_failed}")
    if all_errors:
        print(f"\nErrors ({len(all_errors)}):")
        for err in all_errors[:10]:
            print(f"  - {err}")
        if len(all_errors) > 10:
            print(f"  ... and {len(all_errors) - 10} more")

    sys.exit(1 if total_failed > 0 else 0)


if __name__ == "__main__":
    main()
