from __future__ import annotations

import html
import re
from dataclasses import dataclass

from playwright.async_api import Locator, Page
from urllib.parse import urlencode


COMPANY = "Google"
BASE_SEARCH_URL = "https://www.google.com/about/careers/applications/jobs/results"
# Exact-phrase search, matching the Google Careers UI.
QUERY = '"Software Engineer"'
LOCATIONS = (
    "California, USA",
    "New York, USA",
)
TOP_N = 10
# Keep roles whose Minimum qualifications "X years of experience" are all < this.
MAX_YEARS_EXCLUSIVE = 5
MAX_PAGES = 5

TITLE_PATTERN = re.compile(r"\bsoftware engineer\b", re.IGNORECASE)
DETAIL_LINK_SELECTOR = 'a[href*="jobs/results/"]'
DETAIL_URL_RE = re.compile(r"/jobs/results/\d+")
LOCATION_LINE_RE = re.compile(r".+,\s*[A-Z]{2},\s*USA")
YEARS_OF_EXPERIENCE_RE = re.compile(
    r"(\d+)\s*\+?\s*years?\s+of\s+experience",
    re.IGNORECASE,
)
MIN_QUAL_SECTION_RE = re.compile(
    r"Minimum qualifications\s*(.*?)(?:Preferred qualifications|Learn more|$)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class Job:
    company: str
    title: str
    location: str
    date_posted: str
    url: str


def build_search_url(
    query: str = QUERY,
    locations: tuple[str, ...] = LOCATIONS,
    page: int = 1,
) -> str:
    params: list[tuple[str, str]] = [("q", query)]
    params.extend(("location", location) for location in locations)
    params.append(("sort_by", "date"))
    if page > 1:
        params.append(("page", str(page)))
    return f"{BASE_SEARCH_URL}?{urlencode(params)}"


SEARCH_URL = build_search_url()


async def first_text(locator: Locator) -> str | None:
    if await locator.count() == 0:
        return None
    text = (await locator.first.inner_text()).strip()
    return text or None


def location_from_card_text(card_text: str) -> str:
    locations = [
        line.strip(" ;")
        for line in card_text.splitlines()
        if LOCATION_LINE_RE.fullmatch(line.strip(" ;"))
    ]
    return " | ".join(dict.fromkeys(locations)) or "Location not listed"


def experience_years_from_min_qualifications(card_text: str) -> list[int]:
    match = MIN_QUAL_SECTION_RE.search(card_text)
    if not match:
        return []
    return [int(value) for value in YEARS_OF_EXPERIENCE_RE.findall(match.group(1))]


def meets_experience_cap(card_text: str) -> bool:
    years = experience_years_from_min_qualifications(card_text)
    if not years:
        return False
    return all(year < MAX_YEARS_EXCLUSIVE for year in years)


async def read_job_from_link(link: Locator, rank: int) -> Job | None:
    url = (await link.evaluate("el => el.href")).split("?")[0]
    if not DETAIL_URL_RE.search(url):
        return None

    card = link.locator("xpath=ancestor::li[1]")
    if await card.count() == 0:
        card = link.locator("xpath=ancestor::div[.//h3][1]")
    if await card.count() == 0:
        return None

    title = await first_text(card.locator("h3"))
    if title is None or not TITLE_PATTERN.search(title):
        return None

    card_text = await card.inner_text()
    if not meets_experience_cap(card_text):
        return None

    return Job(
        company=COMPANY,
        title=html.unescape(title),
        location=location_from_card_text(card_text),
        date_posted=f"rank #{rank} (Google sort_by=date)",
        url=url,
    )


async def collect_jobs_from_current_page(
    page: Page,
    *,
    top_n: int,
    jobs: list[Job],
    seen_urls: set[str],
    rank_offset: int,
) -> int:
    links = page.locator(DETAIL_LINK_SELECTOR)
    total_links = await links.count()
    print(f"  Job links on page: {total_links}")

    for index in range(total_links):
        if len(jobs) >= top_n:
            break

        link = links.nth(index)
        rank = rank_offset + index + 1
        try:
            job = await read_job_from_link(link=link, rank=rank)
        except Exception as exc:
            try:
                preview = (await link.inner_text()).strip()[:300]
            except Exception:
                preview = "(could not read link text)"
            print(f"\n  Link #{rank} parse failed")
            print(f"  Preview: {preview!r}")
            print(f"  Error: {exc}")
            continue

        if job is None or job.url in seen_urls:
            continue

        seen_urls.add(job.url)
        jobs.append(job)
        print(f"  [{len(jobs)}] {job.title} — {job.location}")

    return total_links


async def collect_google_jobs(
    page: Page,
    top_n: int = TOP_N,
    query: str = QUERY,
    locations: tuple[str, ...] = LOCATIONS,
) -> list[Job]:
    print(f"\nSearching {COMPANY}...")
    print(f"  Query: {query}")
    print(f"  Locations: {', '.join(locations)}")
    print(f"  Experience: all 'years of experience' in Minimum qualifications < {MAX_YEARS_EXCLUSIVE}")

    jobs: list[Job] = []
    seen_urls: set[str] = set()
    rank_offset = 0

    for page_number in range(1, MAX_PAGES + 1):
        if len(jobs) >= top_n:
            break

        search_url = build_search_url(
            query=query,
            locations=locations,
            page=page_number,
        )
        print(f"  URL (page {page_number}): {search_url}")

        await page.goto(search_url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(3_000)
        await page.locator(DETAIL_LINK_SELECTOR).first.wait_for(
            state="attached",
            timeout=20_000,
        )

        link_count = await collect_jobs_from_current_page(
            page,
            top_n=top_n,
            jobs=jobs,
            seen_urls=seen_urls,
            rank_offset=rank_offset,
        )
        rank_offset += link_count

        if link_count == 0:
            break

    print(f"  Kept top {len(jobs)} matching Google roles")
    return jobs
