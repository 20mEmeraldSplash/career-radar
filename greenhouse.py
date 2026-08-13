from __future__ import annotations

import html
import json
import re
import concurrent.futures
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


OUTPUT_DIR = Path("output")
API_BASE = "https://boards-api.greenhouse.io/v1/boards"
MIN_EMPLOYEES = 200
LOOKBACK_HOURS = 48
# Reject roles that mention more than this many years of experience.
MAX_YEARS = 5
REQUEST_TIMEOUT_SECONDS = 30
MAX_WORKERS = 12

LOCATIONS = (
    "Remote (US)",
    "San Diego",
)

# Match Software Engineer / Senior Software Engineer (allow trailing specialty text).
SOFTWARE_TITLE_RE = re.compile(
    r"\b(?:senior\s+)?software\s+engineer\b",
    re.IGNORECASE,
)
# Keep titles focused on SWE / Senior SWE only.
EXCLUDED_TITLE_RE = re.compile(
    r"\b("
    r"staff|principal|lead|distinguished|fellow|"
    r"manager|director|intern|architect|head|vp|"
    r"engineering\s+manager"
    r")\b",
    re.IGNORECASE,
)
YEARS_OF_EXPERIENCE_RE = re.compile(
    r"(\d+)\s*\+?\s*years?\s+(?:of\s+)?(?:experience|exp\.?)",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Company:
    name: str
    board_token: str
    estimated_employees: int


@dataclass
class Job:
    company: str
    title: str
    location: str
    published_at: str
    date_source: str
    url: str
    estimated_employees: int
    board_token: str


# Curated Greenhouse boards with working public tokens.
# estimated_employees are approximate public estimates used only for
# MIN_EMPLOYEES; Greenhouse itself does not expose headcount.
COMPANIES: tuple[Company, ...] = (
    Company("Affirm", "affirm", 2_500),
    Company("Airbnb", "airbnb", 6_800),
    Company("Airtable", "airtable", 800),
    Company("Anthropic", "anthropic", 1_000),
    Company("Asana", "asana", 1_800),
    Company("Block", "block", 10_000),
    Company("Brex", "brex", 1_200),
    Company("Calendly", "calendly", 700),
    Company("Chime", "chime", 1_400),
    Company("Cloudflare", "cloudflare", 4_000),
    Company("Coinbase", "coinbase", 3_500),
    Company("Coursera", "coursera", 1_300),
    Company("Databricks", "databricks", 6_000),
    Company("Datadog", "datadog", 6_000),
    Company("Discord", "discord", 600),
    Company("Dropbox", "dropbox", 2_500),
    Company("Duolingo", "duolingo", 800),
    Company("Figma", "figma", 1_500),
    Company("GitLab", "gitlab", 2_000),
    Company("Gusto", "gusto", 2_400),
    Company("HubSpot", "hubspot", 7_500),
    Company("Instacart", "instacart", 3_000),
    Company("Intercom", "intercom", 1_200),
    Company("Khan Academy", "khanacademy", 500),
    Company("MongoDB", "mongodb", 5_000),
    Company("Okta", "okta", 6_000),
    Company("Pinterest", "pinterest", 4_000),
    Company("Reddit", "reddit", 2_000),
    Company("Robinhood", "robinhood", 2_400),
    Company("Stripe", "stripe", 8_000),
    Company("Twilio", "twilio", 5_500),
)


def eligible_companies(
    companies: tuple[Company, ...] = COMPANIES,
    min_employees: int = MIN_EMPLOYEES,
) -> list[Company]:
    return [
        company
        for company in companies
        if company.estimated_employees > min_employees
    ]


def parse_greenhouse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_within_lookback(posted_at: datetime, now: datetime) -> bool:
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    return cutoff <= posted_at <= now + timedelta(minutes=10)


def is_software_role(title: str) -> bool:
    if EXCLUDED_TITLE_RE.search(title):
        return False
    return bool(SOFTWARE_TITLE_RE.search(title))


SAN_DIEGO_LOCATION_RE = re.compile(r"\bSan\s+Diego\b", re.IGNORECASE)
US_REMOTE_MARKER_RE = re.compile(
    r"("
    r"\busa\b|\bu\.s\.?\b|\bunited states\b|"
    r"us-?remote|remote-?us|"
    r"remote\s*[-,]?\s*(usa|u\.s\.?|united states|\bus\b)"
    r")",
    re.IGNORECASE,
)


def is_allowed_location(location: str) -> bool:
    """US remote, or San Diego."""
    text = (location or "").strip()
    if not text:
        return False

    if SAN_DIEGO_LOCATION_RE.search(text):
        return True

    # Require an explicit US marker for remote roles.
    if "remote" not in text.lower():
        return False

    return bool(US_REMOTE_MARKER_RE.search(text))


def strip_html(text: str) -> str:
    return HTML_TAG_RE.sub(" ", html.unescape(text))


def experience_years_mentioned(text: str) -> list[int]:
    return [int(value) for value in YEARS_OF_EXPERIENCE_RE.findall(text)]


def meets_experience_cap(content: str, max_years: int = MAX_YEARS) -> bool:
    """Keep jobs with no years mentioned, or all mentioned years <= max_years."""
    years = experience_years_mentioned(strip_html(content))
    if not years:
        return True
    return all(year <= max_years for year in years)


def fetch_board_jobs(board_token: str) -> list[dict[str, Any]]:
    url = f"{API_BASE}/{board_token}/jobs?content=true"
    request = Request(
        url,
        headers={
            "User-Agent": "career-radar-mvp/1.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    jobs = payload.get("jobs", [])
    return jobs if isinstance(jobs, list) else []


def normalize_job(raw: dict[str, Any], company: Company) -> Job | None:
    title = str(raw.get("title") or "").strip()
    if not is_software_role(title):
        return None

    location = ""
    location_obj = raw.get("location")
    if isinstance(location_obj, dict):
        location = str(location_obj.get("name") or "").strip()
    if not is_allowed_location(location):
        return None

    content = str(raw.get("content") or "")
    if not meets_experience_cap(content):
        return None

    first_published = raw.get("first_published")
    updated_at = raw.get("updated_at")
    if first_published:
        date_value = str(first_published)
        date_source = "first_published"
    elif updated_at:
        date_value = str(updated_at)
        date_source = "updated_at"
    else:
        return None

    posted_at = parse_greenhouse_datetime(date_value)
    if posted_at is None:
        return None

    url = str(raw.get("absolute_url") or "").strip()
    if not url:
        return None

    return Job(
        company=company.name,
        title=html.unescape(title),
        location=location,
        published_at=posted_at.isoformat(),
        date_source=date_source,
        url=url,
        estimated_employees=company.estimated_employees,
        board_token=company.board_token,
    )


def date_label(date_source: str) -> str:
    if date_source == "first_published":
        return "First published"
    if date_source == "updated_at":
        return "Last updated"
    return "Published / updated"


def collect_company_jobs(
    company: Company,
    now: datetime,
) -> tuple[Company, list[Job], str | None]:
    try:
        raw_jobs = fetch_board_jobs(company.board_token)
    except HTTPError as exc:
        return company, [], f"HTTP {exc.code}"
    except URLError as exc:
        return company, [], f"network error: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 - board failures should not stop the run
        return company, [], str(exc)

    matched: list[Job] = []
    for raw in raw_jobs:
        if not isinstance(raw, dict):
            continue
        job = normalize_job(raw, company)
        if job is None:
            continue
        posted_at = parse_greenhouse_datetime(job.published_at)
        if posted_at is None or not is_within_lookback(posted_at, now):
            continue
        matched.append(job)

    return company, matched, None


def collect_greenhouse_jobs(
    companies: list[Company] | None = None,
    now: datetime | None = None,
) -> list[Job]:
    now = now or datetime.now(timezone.utc)
    companies = companies or eligible_companies()

    print("\nSearching Greenhouse...")
    print(
        f"  Companies (estimated_employees > {MIN_EMPLOYEES}): {len(companies)}"
    )
    print(
        "  Filters: Software Engineer / Senior Software Engineer, past "
        f"{LOOKBACK_HOURS} hours, location in {' / '.join(LOCATIONS)}, "
        f"years of experience <= {MAX_YEARS} when mentioned"
    )

    jobs: list[Job] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(collect_company_jobs, company, now)
            for company in companies
        ]
        for future in concurrent.futures.as_completed(futures):
            company, matched, error = future.result()
            if error:
                print(f"  [{company.name}] skipped: {error}")
                continue
            print(f"  [{company.name}] matching jobs: {len(matched)}")
            jobs.extend(matched)

    unique = {job.url: job for job in jobs}
    sorted_jobs = sorted(
        unique.values(),
        key=lambda job: job.published_at,
        reverse=True,
    )
    print(f"  Total matching Greenhouse roles: {len(sorted_jobs)}")
    return sorted_jobs


def save_results(jobs: list[Job], generated_at: datetime) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    payload = {
        "generated_at": generated_at.isoformat(),
        "source": "greenhouse",
        "min_employees": MIN_EMPLOYEES,
        "lookback_hours": LOOKBACK_HOURS,
        "max_years": MAX_YEARS,
        "locations": list(LOCATIONS),
        "title_filter": SOFTWARE_TITLE_RE.pattern,
        "companies_scanned": [
            asdict(company) for company in eligible_companies()
        ],
        "jobs": [asdict(job) for job in jobs],
    }

    json_path = OUTPUT_DIR / "greenhouse_jobs.json"
    html_path = OUTPUT_DIR / "greenhouse_jobs.html"

    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    cards = "\n".join(
        f"""
        <article>
          <h2>{html.escape(job.title)}</h2>
          <p><strong>{html.escape(job.company)}</strong>
            · ~{job.estimated_employees:,} employees (est.)</p>
          <p>{html.escape(job.location)}</p>
          <p>{html.escape(date_label(job.date_source))}:
            {html.escape(job.published_at)}</p>
          <a href="{html.escape(job.url)}" target="_blank" rel="noopener noreferrer">View job</a>
        </article>
        """
        for job in jobs
    )
    if not cards:
        cards = "<p>No matching Greenhouse software jobs were found.</p>"

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Career Radar — Greenhouse</title>
  <style>
    body {{ max-width: 900px; margin: 40px auto; padding: 0 20px; font-family: Arial, sans-serif; line-height: 1.5; background: #f7f7f7; }}
    article {{ background: white; border: 1px solid #ddd; border-radius: 10px; padding: 20px; margin: 16px 0; }}
    h1, h2 {{ margin-top: 0; }}
    a {{ display: inline-block; margin-top: 8px; }}
  </style>
</head>
<body>
  <h1>Career Radar — Greenhouse</h1>
  <p>Generated at {html.escape(generated_at.isoformat())}.</p>
  <p>
    Software Engineer / Senior Software Engineer roles from the past
    {LOOKBACK_HOURS} hours at Greenhouse companies with more than
    {MIN_EMPLOYEES} estimated employees.
    Locations: {html.escape(", ".join(LOCATIONS))}.
    Experience: keep if unmentioned or all mentioned years &lt;= {MAX_YEARS}.
  </p>
  {cards}
</body>
</html>
"""
    html_path.write_text(document, encoding="utf-8")
    print("\nSaved:")
    print(f"  {json_path}")
    print(f"  {html_path}")


def main() -> None:
    generated_at = datetime.now(timezone.utc)
    jobs = collect_greenhouse_jobs(now=generated_at)
    save_results(jobs, generated_at=generated_at)

    print("\n" + "=" * 72)
    print(
        f"Found {len(jobs)} Greenhouse SWE jobs "
        f"({' / '.join(LOCATIONS)}, past {LOOKBACK_HOURS}h, "
        f"years <= {MAX_YEARS} when mentioned, "
        f"estimated_employees > {MIN_EMPLOYEES})."
    )
    for job in jobs:
        print(
            f"\n[{job.company} · ~{job.estimated_employees:,} est.] {job.title}"
        )
        print(f"Location: {job.location}")
        print(f"{date_label(job.date_source)}: {job.published_at}")
        print(f"URL:      {job.url}")


if __name__ == "__main__":
    main()
