#!/usr/bin/env python3
"""Collects Data Engineer jobs in India from Adzuna into jobs.json for the job board site.

GitHub Actions runs this every 2 hours. Each run adds newly posted jobs, refreshes
ones it sees again, and drops jobs older than keep_days. If Adzuna can't be reached,
the old jobs are kept and the site shows a notice instead.

  python collect_jobs.py              update jobs.json
  python collect_jobs.py --sample F   (testing) use Adzuna results from a JSON file
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
DATA_FILE = ROOT / "jobs.json"
ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/in/search/{page}"
PAGE_SIZE = 50
SNIPPET_CHARS = 280
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------- helpers
TAG_RE = re.compile(r"<[^>]+>")


def clean(text):
    """Strip HTML tags/entities and squeeze whitespace."""
    if not text:
        return ""
    text = html.unescape(TAG_RE.sub(" ", str(text)))
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
    """GET and return parsed JSON. Never logs the URL: it holds the API key."""
    headers = {"User-Agent": "job-board/1.0 (personal job search)", "Accept": "application/json"}
    host = urllib.parse.urlsplit(url).netloc
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = error_detail(err.read().decode("utf-8", "replace"))
            if err.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(10 * attempt)
                continue
            raise RuntimeError(f"{host} answered HTTP {err.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            if attempt < retries:
                time.sleep(10 * attempt)
                continue
            raise RuntimeError(f"Could not get a valid answer from {host}: {err}") from None


# ---------------------------------------------------------------- Adzuna
class SetupNeeded(Exception):
    pass


def adzuna_job(r):
    loc = r.get("location") or {}
    area = [clean(a) for a in (loc.get("area") or [])]
    place = clean(loc.get("display_name")) or ", ".join(reversed(area[1:])) or "India"
    url = str(r.get("redirect_url") or "")
    return {
        "id": f"adzuna:{r.get('id')}",
        "title": clean(r.get("title"))[:150],
        "company": clean((r.get("company") or {}).get("display_name")) or "Company not listed",
        "location": place,
        "where_text": norm(" ".join([place] + area)),
        "country_only": len(area) <= 1 and norm(place) in ("", "india"),
        "description": clean(r.get("description")),
        "url": url if url.startswith(("https://", "http://")) else "",
        "posted": r.get("created") or "",
    }


def adzuna_page(term, page, days):
    app_id = os.environ.get("ADZUNA_APP_ID", "").strip()
    app_key = os.environ.get("ADZUNA_APP_KEY", "").strip()
    if not app_id or not app_key:
        raise SetupNeeded(
            "Add your ADZUNA_APP_ID and ADZUNA_APP_KEY in the GitHub repository under "
            "Settings > Secrets and variables > Actions, then run the workflow again."
        )
    query = {
        "app_id": app_id,
        "app_key": app_key,
        "title_only": term,
        "results_per_page": PAGE_SIZE,
        "max_days_old": days,
        "sort_by": "date",
        "content-type": "application/json",
    }
    data = request_json(ADZUNA_URL.format(page=page) + "?" + urllib.parse.urlencode(query))
    if not isinstance(data, dict) or "results" not in data:
        raise RuntimeError("Adzuna sent an unexpected answer (no 'results' list).")
    return [adzuna_job(r) for r in data["results"] or []]


def fetch(cfg, known_ids, days, pages):
    raw, calls, errors = [], 0, []
    for term in cfg["search_terms"]:
        try:
            for page in range(1, pages + 1):
                batch = adzuna_page(term, page, days)
                calls += 1
                raw.extend(batch)
                # Newest first: stop when a page is short or already fully known.
                if len(batch) < PAGE_SIZE or all(j["id"] in known_ids for j in batch):
                    break
                time.sleep(1)
        except RuntimeError as err:
            errors.append(f"search '{term}': {err}")
        time.sleep(1)
    return raw, calls, errors


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
        """Board entry for a data engineering job, or None for anything else."""
        title = norm(r["title"])
        if not any(has_word(w, title) for w in include) or any(has_word(w, title) for w in drop):
            return None
        city = next((name for name, aliases in cities
                     if any(has_word(a, r["where_text"]) for a in aliases)), "Other")
        desc = norm(r["description"])
        remote = any(has_word(w, title) or has_word(w, r["where_text"]) for w in remote_words) or (
            r["country_only"] and any(has_word(w, desc) for w in remote_words))
        return {
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
        }

    return classify


def fingerprint(job):
    return "|".join([norm(job["title"]), norm(job["company"]), norm(job["location"])[:40]])


def sort_time(job):
    return parse_time(job.get("posted")) or parse_time(job.get("first_seen")) or EPOCH


def merge(old_jobs, found, stamp, keep_days):
    """Combine the board with this run's jobs. Returns (jobs newest first, number new)."""
    jobs = {j["id"]: j for j in old_jobs}
    for j in found:
        prev = jobs.get(j["id"])
        j["first_seen"] = prev["first_seen"] if prev else stamp
        jobs[j["id"]] = j
    # The same job re-posted under a new id: keep the newest copy, but remember
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
    cutoff = now_utc() - timedelta(days=keep_days)
    kept = [j for j in by_fp.values() if sort_time(j) >= cutoff]
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
    parser.add_argument("--sample", help="(testing) read Adzuna results from this JSON file")
    args = parser.parse_args()

    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    data = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else {}
    old_jobs = data.get("jobs", [])
    stamp = iso(now_utc())
    first_run = not old_jobs
    days = cfg["first_run_days"] if first_run else cfg["max_days_old"]
    pages = cfg["first_run_pages"] if first_run else cfg["max_pages_per_term"]

    data.update({
        "attempted": stamp,
        "cities": list(cfg["cities"]),
        "max_years": cfg["max_required_years"],
        "keep_days": cfg["keep_days"],
    })
    data.setdefault("jobs", old_jobs)

    try:
        if args.sample:
            sample = json.loads(Path(args.sample).read_text(encoding="utf-8"))
            raw, calls, errors = [adzuna_job(r) for r in sample["results"]], 0, []
        else:
            raw, calls, errors = fetch(cfg, {j["id"] for j in old_jobs}, days, pages)
    except SetupNeeded as err:
        data.update(status="setup", message=str(err))
        write_data(data)
        print(f"SETUP NEEDED: {err}")
        return 0

    classify = make_classifier(cfg)
    found = [j for j in (classify(r) for r in raw) if j]

    if errors and not raw:
        data.update(status="error", message=errors[0][:300])
        print(f"Could not update. Keeping the {len(old_jobs)} jobs already on the board.")
    else:
        jobs, added = merge(old_jobs, found, stamp, cfg["keep_days"])
        data.update(jobs=jobs, updated=stamp, added=added,
                    status="partial" if errors else "ok",
                    message=("Some searches failed: " + "; ".join(errors))[:300] if errors else "")
        print(f"Adzuna requests: {calls}. Jobs returned: {len(raw)}. Data engineering roles: {len(found)}. "
              f"New: {added}. On the board: {len(jobs)}.")
    for e in errors:
        print(f"WARNING: {e}")
    write_data(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
