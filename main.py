from __future__ import annotations

import asyncio
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from google import (
    LOCATIONS as GOOGLE_LOCATIONS,
    MAX_YEARS_EXCLUSIVE,
    QUERY as GOOGLE_QUERY,
    TOP_N as GOOGLE_TOP_N,
    Job as GoogleJob,
    collect_google_jobs,
)
from greenhouse import (
    LOCATIONS as GREENHOUSE_LOCATIONS,
    LOOKBACK_HOURS as GREENHOUSE_LOOKBACK_HOURS,
    MAX_YEARS as GREENHOUSE_MAX_YEARS,
    MIN_EMPLOYEES,
    Job as GreenhouseJob,
    collect_greenhouse_jobs,
    date_label as greenhouse_date_label,
)

OUTPUT_DIR = Path("output")


def normalize_google_job(job: GoogleJob) -> dict[str, Any]:
    return {
        "source": "google",
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "published_at": job.date_posted,
        "date_source": "google_rank",
        "url": job.url,
        "estimated_employees": None,
    }


def normalize_greenhouse_job(job: GreenhouseJob) -> dict[str, Any]:
    return {
        "source": "greenhouse",
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "published_at": job.published_at,
        "date_source": job.date_source,
        "url": job.url,
        "estimated_employees": job.estimated_employees,
    }


def save_results(jobs: list[dict[str, Any]], generated_at: datetime) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    payload = {
        "generated_at": generated_at.isoformat(),
        "sources": {
            "google": {
                "query": GOOGLE_QUERY,
                "locations": list(GOOGLE_LOCATIONS),
                "max_years_exclusive": MAX_YEARS_EXCLUSIVE,
                "limit": GOOGLE_TOP_N,
            },
            "greenhouse": {
                "min_employees": MIN_EMPLOYEES,
                "lookback_hours": GREENHOUSE_LOOKBACK_HOURS,
                "max_years": GREENHOUSE_MAX_YEARS,
                "locations": list(GREENHOUSE_LOCATIONS),
            },
        },
        "counts": {
            "google": sum(1 for job in jobs if job["source"] == "google"),
            "greenhouse": sum(1 for job in jobs if job["source"] == "greenhouse"),
            "total": len(jobs),
        },
        "jobs": jobs,
    }

    (OUTPUT_DIR / "jobs.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    cards = "\n".join(
        f"""
        <article>
          <p class="source">{html.escape(job["source"])}</p>
          <h2>{html.escape(job["title"])}</h2>
          <p><strong>{html.escape(job["company"])}</strong>
            {f' · ~{job["estimated_employees"]:,} employees (est.)' if job.get("estimated_employees") else ""}
          </p>
          <p>{html.escape(job["location"])}</p>
          <p>{html.escape(_display_date_label(job))}:
            {html.escape(job["published_at"])}</p>
          <a href="{html.escape(job["url"])}" target="_blank" rel="noopener noreferrer">View job</a>
        </article>
        """
        for job in jobs
    )

    if not cards:
        cards = "<p>No matching jobs were found.</p>"

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Career Radar</title>
  <style>
    body {{ max-width: 900px; margin: 40px auto; padding: 0 20px; font-family: Arial, sans-serif; line-height: 1.5; background: #f7f7f7; }}
    article {{ background: white; border: 1px solid #ddd; border-radius: 10px; padding: 20px; margin: 16px 0; }}
    h1, h2 {{ margin-top: 0; }}
    .source {{ text-transform: uppercase; letter-spacing: 0.04em; font-size: 12px; color: #666; margin: 0 0 8px; }}
    a {{ display: inline-block; margin-top: 8px; }}
  </style>
</head>
<body>
  <h1>Career Radar</h1>
  <p>Generated at {html.escape(generated_at.isoformat())}.</p>
  <p>
    Google: top {GOOGLE_TOP_N} {html.escape(GOOGLE_QUERY)} roles.
    Greenhouse: Software Engineer / Senior Software Engineer in
    {html.escape(" / ".join(GREENHOUSE_LOCATIONS))} from the past
    {GREENHOUSE_LOOKBACK_HOURS} hours
    (years of experience &lt;= {GREENHOUSE_MAX_YEARS} when mentioned).
  </p>
  {cards}
</body>
</html>
"""

    (OUTPUT_DIR / "jobs.html").write_text(document, encoding="utf-8")


def _display_date_label(job: dict[str, Any]) -> str:
    if job["source"] == "greenhouse":
        return greenhouse_date_label(str(job.get("date_source") or ""))
    return "Recency"


def print_jobs(jobs: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 72)
    print(f"Found {len(jobs)} jobs total.")

    for job in jobs:
        employees = (
            f" · ~{job['estimated_employees']:,} est."
            if job.get("estimated_employees")
            else ""
        )
        print(f"\n[{job['source']}] [{job['company']}{employees}] {job['title']}")
        print(f"Location: {job['location']}")
        print(f"{_display_date_label(job)}: {job['published_at']}")
        print(f"URL:      {job['url']}")

    print("\nSaved:")
    print(f"  {OUTPUT_DIR / 'jobs.json'}")
    print(f"  {OUTPUT_DIR / 'jobs.html'}")


async def run_google() -> list[GoogleJob]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            return await collect_google_jobs(
                page=page,
                top_n=GOOGLE_TOP_N,
                query=GOOGLE_QUERY,
                locations=GOOGLE_LOCATIONS,
            )
        finally:
            await browser.close()


async def run() -> None:
    generated_at = datetime.now(timezone.utc)

    google_jobs = await run_google()
    greenhouse_jobs = collect_greenhouse_jobs(now=generated_at)

    combined = [
        *[normalize_google_job(job) for job in google_jobs],
        *[normalize_greenhouse_job(job) for job in greenhouse_jobs],
    ]

    save_results(combined, generated_at=generated_at)
    print_jobs(combined)

    print("\n" + "=" * 72)
    print(
        f"Done. Google: {len(google_jobs)} · "
        f"Greenhouse: {len(greenhouse_jobs)} · "
        f"Total: {len(combined)}"
    )


if __name__ == "__main__":
    asyncio.run(run())
