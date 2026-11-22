#!/usr/bin/env python3
"""Collects Data Engineer jobs in India into jobs.json for the job board site.

Main source: the public job feeds behind companies' own career pages (Greenhouse,
Lever, Ashby, SmartRecruiters). They are free and need no sign-up or key. The
company list is in config.json. Optional extra: Adzuna, used only when the
ADZUNA_APP_ID and ADZUNA_APP_KEY secrets exist.

GitHub Actions runs this every 2 hours. Each run adds new jobs, removes jobs a
company has closed, and drops anything older than keep_days.

  python collect_jobs.py              update jobs.json
  python collect_jobs.py --sample F   (testing) use Adzuna-style results from a JSON file
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
DATA_FILE = ROOT / "jobs.json"
ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/in/search/{page}"
PAGE_SIZE = 50
SNIPPET_CHARS = 280
MAX_DETAIL_FETCHES = 80
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
INDIA_RE = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|secunderabad|pune|chennai|gurugram|gurgaon|noida|mumbai|delhi|"
    r"kolkata|ahmedabad|kochi|jaipur|coimbatore|trivandrum|thiruvananthapuram|mysore|mysuru|indore|chandigarh|"
    r"vadodara|nagpur|bhubaneswar|visakhapatnam|mohali|lucknow|surat)\b"
)


# ---------------------------------------------------------------- helpers
TAG_RE = re.compile(r"<[^>]+>")


def clean(text):
    """Strip HTML tags/entities (also double-escaped HTML) and squeeze whitespace."""
    if not text:
        return ""
    text = html.unescape(str(text))
    text = html.unescape(TAG_RE.sub(" ", text))
    return re.sub(r"\s+", " ", text).strip()


def norm(text):
    """Lowercase, letters and digits only, single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def has_word(phrase, text):
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(value):
    """Any of the feeds' date formats -> ISO UTC string ('' if unknown)."""
    if isinstance(value, (int, float)) and value > 0:  # Lever: milliseconds since 1970
        return iso(datetime.fromtimestamp(value / 1000, timezone.utc))
    dt = parse_time(value)
    return iso(dt) if dt else ""


def error_detail(raw):
    """Short, readable reason from an error response (JSON or HTML)."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            keys = ("exception", "display", "description", "error", "message")
            parts = [str(data[k]) for k in keys if data.get(k)]
            if parts:
                return " - ".join(parts)[:300]
    except ValueError:
        pass
    title = re.search(r"<title>(.*?)</title>", raw, re.I | re.S)
    return clean(title.group(1) if title else raw)[:200]


def request_json(url, retries=3):
    """GET and return parsed JSON. Never logs the full URL: Adzuna's holds the API key."""
    headers = {"User-Agent": "job-board/1.0 (personal job search)", "Accept": "application/json"}
    host = urllib.parse.urlsplit(url).netloc
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = error_detail(err.read().decode("utf-8", "replace"))
            if err.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(f"{host} answered HTTP {err.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            if attempt < retries:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(f"Could not get a valid answer from {host}: {err}") from None


def safe_url(url):
    url = str(url or "")
    return url if url.startswith(("https://", "http://")) else ""


# ---------------------------------------------------------------- company career-page feeds
def feed_job(company, ats, job_id, title, place, url, posted, description="", remote=False, extra_where=""):
    where = norm(" ".join([place or "", extra_where]))
    if remote:
        where += " remote"
    place = re.sub(r"\s*,(\s*,)+\s*", ", ", clean(place)).strip(" ,")  # "bangalore, , India" -> "bangalore, India"
    return {
        "id": f"{ats}:{company['slug']}:{job_id}",
        "feed": f"{ats}:{company['slug']}",
        "title": clean(title)[:150],
        "company": company["name"],
        "location": clean(place) or "India",
        "where_text": where,
        "country_only": False,
        "description": clean(description),
        "url": safe_url(url),
        "posted": to_iso(posted),
        "company_feed": True,
    }


def greenhouse(c):
    data = request_json(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs")
    jobs = []
    for j in data.get("jobs") or []:
        place = (j.get("location") or {}).get("name") or ""
        job = feed_job(c, "greenhouse", j.get("id"), j.get("title"), place, j.get("absolute_url"),
                       j.get("first_published") or j.get("updated_at"))
        job["detail_url"] = f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs/{j.get('id')}"
        jobs.append(job)
    return jobs


def lever(c):
    data = request_json(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json")
    jobs = []
    for j in data if isinstance(data, list) else []:
        cat = j.get("categories") or {}
        places = [cat.get("location") or ""] + [p for p in (cat.get("allLocations") or []) if p != cat.get("location")]
        jobs.append(feed_job(
            c, "lever", j.get("id"), j.get("text"), "; ".join(p for p in places if p), j.get("hostedUrl"),
            j.get("createdAt"), j.get("descriptionPlain") or "",
            remote=(j.get("workplaceType") or "").lower() == "remote",
            extra_where="india" if (j.get("country") or "").upper() == "IN" else ""))
    return jobs


def ashby(c):
    data = request_json(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}")
    jobs = []
    for j in data.get("jobs") or []:
        if j.get("isListed") is False:
            continue
        addr = (j.get("address") or {}).get("postalAddress") or {}
        place = ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressCountry")] if x) or j.get("location") or ""
        extra = " ".join([j.get("location") or "", addr.get("addressRegion") or "", addr.get("addressCountry") or ""]
                         + [s.get("location") or "" for s in j.get("secondaryLocations") or []])
        jobs.append(feed_job(c, "ashby", j.get("id"), j.get("title"), place, j.get("jobUrl"), j.get("publishedAt"),
                             j.get("descriptionPlain") or "",
                             remote=(j.get("workplaceType") or "").lower() == "remote", extra_where=extra))
    return jobs


def smartrecruiters(c):
    jobs, offset = [], 0
    while True:
        data = request_json(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings"
                            f"?country=in&limit=100&offset={offset}")
        content = data.get("content") or []
        for j in content:
            loc = j.get("location") or {}
            place = loc.get("fullLocation") or ", ".join(x for x in [loc.get("city"), "India"] if x)
            jobs.append(feed_job(c, "smartrecruiters", j.get("id"), j.get("name"), place,
                                 f"https://jobs.smartrecruiters.com/{c['slug']}/{j.get('id')}", j.get("releasedDate"),
                                 remote=bool(loc.get("remote")), extra_where="india"))
        offset += len(content)
        if not content or offset >= (data.get("totalFound") or 0) or offset >= 1000:
            return jobs
        time.sleep(0.5)


FEEDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters}


def fetch_companies(companies):
    """Returns (jobs, feeds read successfully, errors)."""
    def one(c):
        try:
            return c, FEEDS[c["ats"]](c), None
        except (RuntimeError, KeyError, TypeError, AttributeError) as err:
            return c, [], f"{c.get('name', c.get('slug'))}: {err}"

    jobs, ok, errors = [], set(), []
    with ThreadPoolExecutor(6) as pool:
        for c, found, err in pool.map(one, companies):
            if err:
                errors.append(err)
            else:
                ok.add(f"{c['ats']}:{c['slug']}")
                jobs.extend(found)
    return jobs, ok, errors


def fill_greenhouse_details(raws, known_ids):
    """Greenhouse lists have no description: fetch it for new matching jobs only."""
    todo = [r for r in raws if r.get("detail_url") and r["id"] not in known_ids][:MAX_DETAIL_FETCHES]

    def one(r):
        try:
            r["description"] = clean(request_json(r["detail_url"]).get("content"))
        except RuntimeError:
            pass

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(one, todo))
    return len(todo)


# ---------------------------------------------------------------- Adzuna (optional)
def adzuna_job(r):
    loc = r.get("location") or {}
    area = [clean(a) for a in (loc.get("area") or [])]
    place = clean(loc.get("display_name")) or ", ".join(reversed(area[1:])) or "India"
    return {
        "id": f"adzuna:{r.get('id')}",
        "title": clean(r.get("title"))[:150],
        "company": clean((r.get("company") or {}).get("display_name")) or "Company not listed",
        "location": place,
        "where_text": norm(" ".join([place] + area)),
        "country_only": len(area) <= 1 and norm(place) in ("", "india"),
        "description": clean(r.get("description")),
        "url": safe_url(r.get("redirect_url")),
        "posted": to_iso(r.get("created")),
    }


def adzuna_keys():
    app_id = os.environ.get("ADZUNA_APP_ID", "").strip()
    app_key = os.environ.get("ADZUNA_APP_KEY", "").strip()
    return (app_id, app_key) if app_id and app_key else None


def fetch_adzuna(cfg, known_ids, days, pages):
    keys = adzuna_keys()
    raw, errors = [], []
    for term in cfg["search_terms"]:
        try:
            for page in range(1, pages + 1):
                query = {"app_id": keys[0], "app_key": keys[1], "title_only": term, "results_per_page": PAGE_SIZE,
                         "max_days_old": days, "sort_by": "date", "content-type": "application/json"}
                data = request_json(ADZUNA_URL.format(page=page) + "?" + urllib.parse.urlencode(query))
                if not isinstance(data, dict) or "results" not in data:
                    raise RuntimeError("Adzuna sent an unexpected answer (no 'results' list).")
                batch = [adzuna_job(r) for r in data["results"] or []]
                raw.extend(batch)
                if len(batch) < PAGE_SIZE or all(j["id"] in known_ids for j in batch):
                    break
                time.sleep(1)
        except RuntimeError as err:
            errors.append(f"Adzuna '{term}': {err}")
        time.sleep(1)
    return raw, errors


# ---------------------------------------------------------------- classify
YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs?)\b", re.I
)


def min_years_required(text):
    """Smallest 'N years' figure that is talking about experience, or None."""
    found = []
    for m in YEARS_RE.finditer(text):
        around = (text[max(0, m.start() - 30):m.start()] + " " + text[m.end():m.end() + 40]).lower()
        if "exp" in around:
            found.append(int(m.group(1)))
    return min(found) if found else None


def snippet(text):
    if len(text) <= SNIPPET_CHARS:
        return text
    cut = text[:SNIPPET_CHARS].rsplit(" ", 1)[0]
    return cut.rstrip(" .,;:-") + "…"


def make_classifier(cfg):
    include = [norm(w) for w in cfg["title_must_include_any"]]
    drop = [norm(w) for w in cfg["drop_title_words"]]
    senior = [norm(w) for w in cfg["senior_title_words"]]
    cities = [(name, [norm(a) for a in aliases]) for name, aliases in cfg["cities"].items()]
    remote_words = [norm(w) for w in cfg["remote_words"]]

    def classify(r):
        """Board entry for a data engineering job in India, or None for anything else."""
        title = norm(r["title"])
        if not any(has_word(w, title) for w in include) or any(has_word(w, title) for w in drop):
            return None
        if r.get("company_feed") and not INDIA_RE.search(r["where_text"]):
            return None
        city = next((name for name, aliases in cities
                     if any(has_word(a, r["where_text"]) for a in aliases)), "Other")
        desc = norm(r["description"])
        remote = any(has_word(w, title) or has_word(w, r["where_text"]) for w in remote_words) or (
            r["country_only"] and any(has_word(w, desc) for w in remote_words))
        job = {
            "id": r["id"],
            "title": r["title"],
            "company": r["company"],
            "location": r["location"],
            "city": city,
            "remote": remote,
            "senior": any(has_word(w, title) for w in senior),
            "years": min_years_required(r["title"] + " . " + r["description"]),
            "posted": r["posted"],
            "url": r["url"],
            "snippet": snippet(r["description"]),
            "source": "company" if r.get("company_feed") else "adzuna",
        }
        if r.get("feed"):
            job["feed"] = r["feed"]
        return job

    return classify


def fingerprint(job):
    return "|".join([norm(job["title"]), norm(job["company"]), norm(job["location"])[:40]])


def sort_time(job):
    return parse_time(job.get("posted")) or parse_time(job.get("first_seen")) or EPOCH


def merge(old_jobs, found, stamp, keep_days, company_keep_days, refreshed_feeds):
    """Combine the board with this run's jobs. Returns (jobs newest first, number new).

    Company jobs stay while the company still lists them (up to company_keep_days);
    Adzuna jobs can't be checked that way, so they stay for keep_days."""
    first_seen = {j["id"]: j.get("first_seen", stamp) for j in old_jobs}
    # A company feed that was read this run lists all its open jobs: anything of
    # theirs that is missing now has been closed, so it is not carried over.
    jobs = {j["id"]: j for j in old_jobs if j.get("feed") not in refreshed_feeds}
    for j in found:
        j["first_seen"] = first_seen.get(j["id"], stamp)
        jobs[j["id"]] = j
    # The same job posted again under a new id: keep the newest copy, but remember
    # when it first appeared so it doesn't show up as "new" again.
    by_fp = {}
    for j in jobs.values():
        fp = fingerprint(j)
        other = by_fp.get(fp)
        if other is None:
            by_fp[fp] = j
            continue
        newer, older = (j, other) if sort_time(j) > sort_time(other) else (other, j)
        newer["first_seen"] = min(newer["first_seen"], older["first_seen"])
        by_fp[fp] = newer
    now = now_utc()
    kept = [j for j in by_fp.values()
            if sort_time(j) >= now - timedelta(days=company_keep_days if j.get("source") == "company" else keep_days)]
    kept.sort(key=sort_time, reverse=True)
    return kept, sum(1 for j in kept if j["first_seen"] == stamp)


# ---------------------------------------------------------------- output
def write_data(data):
    """One job per line, so the file's history on GitHub stays readable."""
    jobs = data.pop("jobs", [])
    head = json.dumps(data, ensure_ascii=False, indent=1)[:-2]
    lines = ",\n".join(json.dumps(j, ensure_ascii=False, separators=(",", ":")) for j in jobs)
    DATA_FILE.write_text(f'{head},\n "jobs": [\n{lines}\n ]\n}}\n', encoding="utf-8")
    data["jobs"] = jobs


def main():
    parser = argparse.ArgumentParser(description="Update jobs.json for the job board")
    parser.add_argument("--sample", help="(testing) read Adzuna results from this JSON file instead of the internet")
    args = parser.parse_args()

    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    data = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else {}
    old_jobs = data.get("jobs", [])
    known = {j["id"] for j in old_jobs}
    stamp = iso(now_utc())
    companies = cfg.get("companies", [])
    use_adzuna = bool(adzuna_keys()) or bool(args.sample)

    raw, errors, refreshed = [], [], set()
    if args.sample:
        sample = json.loads(Path(args.sample).read_text(encoding="utf-8"))
        raw = [adzuna_job(r) for r in sample["results"]]
    else:
        feed_raw, refreshed, errors = fetch_companies(companies)
        raw.extend(feed_raw)
        if use_adzuna:
            first = not any(j.get("source") == "adzuna" for j in old_jobs)
            a_raw, a_err = fetch_adzuna(cfg, known, cfg["first_run_days"] if first else cfg["max_days_old"],
                                        cfg["first_run_pages"] if first else cfg["max_pages_per_term"])
            raw.extend(a_raw)
            errors.extend(a_err)

    classify = make_classifier(cfg)
    matched = [r for r in raw if classify(r)]
    details = fill_greenhouse_details(matched, known)
    found = [j for j in (classify(r) for r in matched) if j]

    data.update({
        "attempted": stamp,
        "cities": list(cfg["cities"]),
        "max_years": cfg["max_required_years"],
        "keep_days": cfg["keep_days"],
        "sources": {"companies": len(companies), "adzuna": use_adzuna},
    })
    data.pop("added", None)
    failed_everything = not args.sample and errors and not refreshed and not raw
    if failed_everything:
        data.update(status="error", message=errors[0][:300], jobs=old_jobs)
        print(f"Could not update. Keeping the {len(old_jobs)} jobs already on the board.")
    else:
        jobs, added = merge(old_jobs, found, stamp, cfg["keep_days"], cfg.get("company_keep_days", 180), refreshed)
        data.update(jobs=jobs, updated=stamp, added=added, status="partial" if errors else "ok",
                    message=(f"{len(errors)} source(s) could not be read: " + "; ".join(errors))[:300] if errors else "")
        print(f"Company feeds read: {len(refreshed)}/{len(companies)}. Adzuna: {'on' if use_adzuna else 'off'}. "
              f"Jobs seen: {len(raw)}. Data engineering roles in India: {len(found)} "
              f"(descriptions fetched: {details}). New: {added}. On the board: {len(jobs)}.")
    for e in errors:
        print(f"WARNING: {e}")
    write_data(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
