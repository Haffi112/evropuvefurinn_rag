#!/usr/bin/env python3
"""Migrate articles from all_eu_content.json to the Evrópuvefur API via bulk endpoint."""

import argparse
import json
import re
import sys

import httpx


def strip_svar_prefix(answer: str) -> str:
    """Remove leading 'Svar\\n\\n' prefix from answer text."""
    return re.sub(r"^Svar\n\n", "", answer)


def load_articles(path: str) -> list[dict]:
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
    parser = argparse.ArgumentParser(description="Migrate articles to Evrópuvefur API")
    parser.add_argument("--input", required=True, help="Path to all_eu_content.json")
    parser.add_argument("--api-url", required=True, help="Base URL of the API (e.g. http://localhost:8000)")
    parser.add_argument("--api-key", required=True, help="CMS API key")
    parser.add_argument("--batch-size", type=int, default=100, help="Articles per batch (max 100)")
    args = parser.parse_args()

    articles = load_articles(args.input)
    print(f"Loaded {len(articles)} articles from {args.input}")

    total_created = 0
    total_updated = 0
    total_failed = 0
    all_errors = []

    with httpx.Client(timeout=120) as client:
        for i in range(0, len(articles), args.batch_size):
            batch = articles[i : i + args.batch_size]
            batch_num = i // args.batch_size + 1
            print(f"\nBatch {batch_num}: sending {len(batch)} articles...")

            resp = client.post(
                f"{args.api_url}/api/v1/articles/bulk",
                json={"articles": batch},
                headers={"X-API-Key": args.api_key},
            )

            if resp.status_code != 200:
                print(f"  ERROR: HTTP {resp.status_code} — {resp.text[:200]}")
                total_failed += len(batch)
                continue

            data = resp.json()
            total_created += data["created"]
            total_updated += data["updated"]
            total_failed += data["failed"]
            all_errors.extend(data.get("errors", []))

            print(f"  Created: {data['created']}, Updated: {data['updated']}, Failed: {data['failed']}")

    print(f"\n{'='*50}")
    print(f"Migration complete:")
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
