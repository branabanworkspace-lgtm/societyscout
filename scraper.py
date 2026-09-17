#!/usr/bin/env python3
"""
SocietyScout: finds contact details that university societies (or company
clubs) have posted publicly online, and saves them to CSV.

It runs entirely on your computer. No AI service is needed.

Command line:
    python scraper.py "University of Leeds"
    python scraper.py "University of Leeds" --url https://<union-site>/societies
    python scraper.py --file universities.txt
    python scraper.py "Acme Ltd" --company

Point-and-click version:
    streamlit run app.py
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import io
import os
import re
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib import robotparser
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

try:
    import phonenumbers
except ImportError:  # still works without it, just less precise
    phonenumbers = None


# ---------------------------------------------------------------- settings --

def setting(name: str, default: str = "") -> str:
    """A setting from the environment, or from Streamlit's secrets when the app
    runs online. Lets one copy of the code work on a laptop and in the cloud."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:  # only present when running inside Streamlit
        import streamlit as st
        return str(st.secrets.get(name, default)).strip()
    except Exception:
        return default


def running_in_cloud() -> bool:
    """True when hosted (Streamlit Community Cloud), where the disk is wiped
    whenever the app restarts."""
    if setting("SOCIETYSCOUT_CLOUD").lower() in ("1", "true", "yes"):
        return True
    return Path("/mount/src").exists() or bool(os.environ.get("STREAMLIT_SERVER_HEADLESS") == "true"
                                               and Path("/home/appuser").exists())


# Websites see this in every request, so site owners know who is visiting. Set
# it here, or as SOCIETYSCOUT_CONTACT in your environment or Streamlit secrets.
CONTACT_EMAIL = setting("SOCIETYSCOUT_CONTACT", "outreach@your-brand.co.uk")

USER_AGENT = f"SocietyScout/1.0 (society outreach research; {CONTACT_EMAIL})"
ROBOTS_NAME = "SocietyScout"

def _base_dir() -> Path:
    """Folder the CSVs are saved in: this file's folder, or the current folder
    when the code runs in a notebook or is pasted into a Python shell."""
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()


BASE_DIR = _base_dir()
# Where CSVs are written. Overridable, because a hosted app can only write to
# a temporary folder.
DATA_DIR = Path(setting("SOCIETYSCOUT_DATA_DIR") or (BASE_DIR / "data"))
EXPORT_DIR = Path(setting("SOCIETYSCOUT_EXPORT_DIR") or (BASE_DIR / "exports"))
MASTER_CSV = DATA_DIR / "societies_master.csv"
UNIVERSITIES_CSV = BASE_DIR / "uk_universities.csv"   # every UK recognised university
UNION_SITES_CSV = DATA_DIR / "union_sites.csv"        # societies pages found, cached
COVERAGE_CSV = DATA_DIR / "coverage.csv"              # how each university went

DEFAULT_MAX_PAGES = 150   # pages checked per search
DEFAULT_DELAY = 1.0       # seconds between requests to the same website

COLUMNS = [
    ("org", "University / Organisation"),
    ("society", "Society"),
    ("type", "Type"),
    ("president", "President / Lead"),
    ("email", "Email"),
    ("other_emails", "Other emails"),
    ("phone", "Phone"),
    ("instagram", "Instagram"),
    ("other_socials", "Other socials"),
    ("committee", "Committee"),
    ("source", "Source page"),
    ("status", "Status"),
    ("added_via", "Added via"),
    ("date_found", "Date found"),
    ("notes", "Notes"),
]
FIELDS = [key for key, _ in COLUMNS]
HEADERS = [label for _, label in COLUMNS]
STATUSES = ["Needs checking", "Checked", "Contacted"]
TYPES = ["Sports", "Academic", "Cultural", "Arts", "Fashion", "Media",
         "Faith", "Volunteering", "Social", "Other"]

ProgressFn = Callable[[int, int, str], None]
LogFn = Callable[[str], None]
StopFn = Callable[[], bool]


# ----------------------------------------------------------------- helpers --

def today() -> str:
    return dt.date.today().isoformat()


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def slug(value) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:60] or "export"


def bare_host(netloc: str) -> str:
    host = netloc.lower()
    return host[4:] if host.startswith("www.") else host


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def dedupe(items) -> list:
    seen, out = set(), []
    for item in items:
        key = item.lower() if isinstance(item, str) else item
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


NAME_STOPWORDS = {"university", "of", "the", "college", "and", "uni", "school",
                  "institute", "students", "student", "union", "ltd", "limited",
                  "plc", "group", "company", "inc", "llp"}


def name_tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", name.lower())
            if len(t) >= 3 and t not in NAME_STOPWORDS]


DROP_PARAM = re.compile(r"^(utm_.*|fbclid|gclid|mc_.*|sort|order|orderby|view|share|ref|"
                        r"returnurl|redirect.*)$", re.I)


def normalise_url(url: str) -> str:
    try:
        p = urlparse(clean(url))
    except ValueError:
        return ""
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not DROP_PARAM.match(k)])
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path or "/", "", query, ""))


def url_key(url: str) -> str:
    p = urlparse(url)
    return bare_host(p.netloc) + (p.path.rstrip("/") or "/") + (("?" + p.query) if p.query else "")


# -------------------------------------------------------- polite fetching --

class Fetcher:
    """Downloads pages politely: follows robots.txt and waits between requests."""

    def __init__(self, delay: float = DEFAULT_DELAY, timeout: int = 15,
                 respect_robots: bool = True, log: LogFn = print):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        })
        self.delay = delay
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.log = log
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._last_hit: dict[str, float] = {}

    def _robots_for(self, url: str) -> robotparser.RobotFileParser:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            rp.set_url(base + "/robots.txt")
            try:
                r = self.session.get(base + "/robots.txt", timeout=self.timeout)
                if r.status_code in (401, 403):
                    rp.disallow_all = True
                elif r.status_code >= 400:
                    rp.allow_all = True
                else:
                    rp.parse(r.text.splitlines())
            except requests.RequestException:
                rp.allow_all = True
            self._robots[base] = rp
        return self._robots[base]

    def allowed(self, url: str) -> bool:
        return (not self.respect_robots) or self._robots_for(url).can_fetch(ROBOTS_NAME, url)

    def sitemaps(self, url: str) -> list[str]:
        try:
            return list(self._robots_for(url).site_maps() or [])
        except Exception:
            return []

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        delay = self.delay
        try:
            robots_delay = self._robots_for(url).crawl_delay(ROBOTS_NAME)
            if robots_delay:
                delay = max(delay, float(robots_delay))
        except Exception:
            pass
        remaining = delay - (time.time() - self._last_hit.get(host, 0.0))
        if remaining > 0:
            time.sleep(min(remaining, 30))

    def get(self, url: str, want_html: bool = True) -> Optional[requests.Response]:
        if not self.allowed(url):
            self.log(f"Skipped (the site asks bots not to visit this page): {url}")
            return None
        host = urlparse(url).netloc
        for attempt in range(3):
            self._wait(url)
            try:
                r = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                self._last_hit[host] = time.time()
                if attempt == 2:
                    self.log(f"Couldn't load {url} ({exc.__class__.__name__})")
                    return None
                time.sleep(2 * (attempt + 1))
                continue
            self._last_hit[host] = time.time()
            if r.status_code in (429, 503):
                retry = r.headers.get("Retry-After", "")
                time.sleep(min(int(retry) if retry.isdigit() else 10 * (attempt + 1), 60))
                continue
            if r.status_code >= 400:
                return None
            ctype = r.headers.get("Content-Type", "").lower()
            if want_html and "html" not in ctype:
                return None
            return r
        return None


# --------------------------------------------------------------- web search --

def web_search(query: str, max_results: int = 10, log: LogFn = print) -> list[dict]:
    """Returns [{'url', 'title', 'snippet'}]. Uses Brave if BRAVE_API_KEY is set,
    otherwise the free ddgs library (no key needed)."""
    brave_key = setting("BRAVE_API_KEY")
    if brave_key:
        try:
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(max_results, 20)},
                headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
                timeout=20,
            )
            r.raise_for_status()
            return [{"url": x.get("url", ""), "title": x.get("title", ""),
                     "snippet": x.get("description", "")}
                    for x in r.json().get("web", {}).get("results", [])]
        except (requests.RequestException, ValueError) as exc:
            log(f"Brave search failed ({exc.__class__.__name__}), using the free search instead.")

    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # older name of the same library
        except ImportError:
            log("The search library isn't installed. Run: pip install ddgs")
            return []

    for attempt in range(3):
        try:
            results = DDGS().text(query, region="uk-en", max_results=max_results) or []
            return [{"url": x.get("href", ""), "title": x.get("title", ""),
                     "snippet": x.get("body", "")} for x in results]
        except Exception as exc:  # the library raises its own error types
            if attempt == 2:
                log(f"Web search isn't responding right now ({exc.__class__.__name__}). "
                    "Wait a few minutes, or paste the societies page address instead.")
                return []
            time.sleep(5 * (attempt + 1))
    return []


BAD_RESULT_HOSTS = ("wikipedia.org", "facebook.com", "instagram.com", "twitter.com", "x.com",
                    "linkedin.com", "youtube.com", "tiktok.com", "reddit.com", "thestudentroom",
                    "whatuni.com", "ucas.com", "indeed.", "glassdoor.", "tripadvisor.", "yell.com",
                    "company-information.service.gov.uk", "amazon.", "ebay.", "pinterest.",
                    "google.", "bing.com", "duckduckgo.com", "prospects.ac.uk", "timeshighereducation")
DIR_HINTS = ("societ", "/groups", "/clubs", "/club", "/sport", "/activities", "/a-z", "atoz",
             "/organisation")
UNION_HINTS = ("union", "guild", "students", "su.", "sums")


def score_directory(result: dict, tokens: list[str]) -> int:
    url, title = result.get("url", ""), result.get("title", "").lower()
    p = urlparse(url)
    host, path = bare_host(p.netloc), p.path.lower()
    if not host or any(bad in host for bad in BAD_RESULT_HOSTS):
        return -99
    score = 0
    if any(h in path for h in DIR_HINTS):
        score += 4
    if any(h in host for h in UNION_HINTS) or host.startswith("su"):
        score += 3
    if re.search(r"union|guild", title):
        score += 2
    if re.search(r"societ|clubs|sport", title):
        score += 2
    if tokens and any(t in host for t in tokens):
        score += 2
    if tokens and any(t in title for t in tokens):
        score += 1
    if path.count("/") > 4:
        score -= 2
    return score


def find_directories(name: str, log: LogFn = print) -> list[str]:
    tokens = name_tokens(name)
    scored: dict[str, tuple[int, str]] = {}
    queries = [f"{name} students union societies", f"{name} students union sports clubs"]
    for i, query in enumerate(queries):
        if i:
            time.sleep(1.5)
        for res in web_search(query, 10, log):
            url = normalise_url(res.get("url", ""))
            if not url:
                continue
            score = score_directory(res, tokens)
            key = url_key(url)
            if key not in scored or score > scored[key][0]:
                scored[key] = (score, url)
    picks, per_host = [], Counter()
    for score, url in sorted(scored.values(), key=lambda x: -x[0]):
        if score < 5:
            break
        host = bare_host(urlparse(url).netloc)
        if per_host[host] >= 2:
            continue
        per_host[host] += 1
        picks.append(url)
        if len(picks) >= 3:
            break
    return picks


# ------------------------------------------------------------- extraction --

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}")
AT_RE = re.compile(r"\s*[\[\(\{<]\s*at\s*[\]\)\}>]\s*", re.I)
DOT_RE = re.compile(r"\s*[\[\(\{<]\s*dot\s*[\]\)\}>]\s*", re.I)
JUNK_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|ico)$|@(example|domain|yourdomain)\.|"
                        r"sentry|wixpress|noreply|no-reply|donotreply", re.I)
GENERIC_EMAIL = re.compile(r"^(info|enquiries|enquiry|hello|admin|office|reception|contact|"
                           r"help|support|studentvoice|su|union|web|webmaster|marketing)@", re.I)


def decode_cfemail(code: str) -> str:
    """Decodes emails hidden by Cloudflare's 'email protection' (common on union sites)."""
    try:
        key = int(code[:2], 16)
        return "".join(chr(int(code[i:i + 2], 16) ^ key) for i in range(2, len(code) - 1, 2))
    except (ValueError, IndexError):
        return ""


def clean_emails(candidates) -> list[str]:
    out = []
    for candidate in candidates:
        for part in re.split(r"[,;\s]+", candidate or ""):
            part = part.strip().strip(".").lower()
            if EMAIL_RE.fullmatch(part) and not JUNK_EMAIL.search(part):
                out.append(part)
    return dedupe(out)


def find_emails(soup, text: str) -> list[str]:
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            found.append(unquote(href[7:].split("?")[0]))
        elif "/cdn-cgi/l/email-protection#" in low:
            found.append(decode_cfemail(href.split("#", 1)[1]))
    for el in soup.find_all(attrs={"data-cfemail": True}):
        found.append(decode_cfemail(el.get("data-cfemail", "")))
    found.extend(EMAIL_RE.findall(DOT_RE.sub(".", AT_RE.sub("@", text))))
    return clean_emails(found)


PHONE_RE = re.compile(r"(?<!\d)(?:\+44\s?(?:\(0\)\s?)?|0)\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}(?!\d)")


def format_phone(raw: str) -> str:
    raw = clean(raw)
    if not raw:
        return ""
    if phonenumbers:
        try:
            number = phonenumbers.parse(raw, "GB")
        except phonenumbers.NumberParseException:
            return ""
        if not phonenumbers.is_valid_number(number):
            return ""
        style = (phonenumbers.PhoneNumberFormat.NATIONAL
                 if phonenumbers.region_code_for_number(number) == "GB"
                 else phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        return phonenumbers.format_number(number, style)
    national = re.sub(r"^\+44\s?(\(0\)\s?)?", "0", raw)
    digits = re.sub(r"\D", "", national)
    return national if digits.startswith("0") and 10 <= len(digits) <= 11 else ""


def find_phones(soup, text: str) -> list[str]:
    raw = [unquote(a["href"][4:]) for a in soup.find_all("a", href=True)
           if a["href"].lower().startswith("tel:")]
    if phonenumbers:
        raw.extend(m.raw_string for m in phonenumbers.PhoneNumberMatcher(text, "GB"))
    else:
        raw.extend(m.group() for m in PHONE_RE.finditer(text))
    return dedupe(p for p in (format_phone(r) for r in raw) if p)


SOCIAL_SITES = ("instagram.com", "facebook.com", "fb.com", "tiktok.com", "twitter.com", "x.com",
                "linktr.ee", "linkedin.com", "youtube.com", "discord.gg", "discord.com", "strava.com")
SOCIAL_SKIP_PATH = re.compile(r"^/(p|reel|reels|explore|accounts|stories|direct|tv|share|sharer|"
                              r"sharer\.php|share\.php|dialog|plugins|intent|home|hashtag|search|"
                              r"watch|embed|legal|policies|privacy|about|help|login|signup)(/|$)", re.I)
IG_TEXT_RE = re.compile(r"(?:instagram|insta|\bIG\b)[^@\n]{0,40}?(?<![A-Za-z0-9._%+-])@([A-Za-z0-9._]{2,30})"
                        r"|instagram\.com/([A-Za-z0-9._]{2,30})", re.I)


def social_url(href: str) -> str:
    href = (href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    try:
        p = urlparse(href)
    except ValueError:
        return ""
    host = re.sub(r"^(www\.|m\.|mobile\.|web\.|[a-z]{2}-[a-z]{2}\.)", "", p.netloc.lower())
    site = next((s for s in SOCIAL_SITES if host == s or host.endswith("." + s)), None)
    if not site:
        return ""
    path = re.sub(r"/+$", "", p.path)
    if site in ("facebook.com", "fb.com") and path == "/profile.php" and "id=" in p.query:
        return f"https://facebook.com/profile.php?{p.query}"
    if not path or SOCIAL_SKIP_PATH.match(path):
        return ""
    if site == "instagram.com":
        handle = path.strip("/").split("/")[0]
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", handle):
            return ""
        return f"https://www.instagram.com/{handle.lower()}/"
    return f"https://{site}{path}"


def find_socials(soup, text: str) -> list[str]:
    links = [social_url(a["href"]) for a in soup.find_all("a", href=True)]
    for at_handle, url_handle in IG_TEXT_RE.findall(text):
        handle = (at_handle or url_handle).rstrip(".").lower()
        if handle:
            links.append(f"https://www.instagram.com/{handle}/")
    return dedupe(u for u in links if u)


ROLE = (r"(?:(?:Club|Team|Society|Men'?s|Women'?s|Ladies'?|Mixed|Joint|Co)[\s-]+)?"
        r"(?:Vice[\s-]+|Deputy[\s-]+|Assistant[\s-]+)?"
        r"(?:President|Chair(?:person)?|Captain|Treasurer|Secretary|"
        r"(?:Social|Kit|Events?|Welfare|Membership|General|Publicity|Media|Social Media|Marketing|"
        r"Sponsorship|Merch(?:andise)?|Fundraising|Communications|Equality|Inclusion|Diversity|"
        r"Sustainability|Academic|Careers|Tour|Fixtures)[\s-]+"
        r"(?:Officer|Secretary|Sec|Rep(?:resentative)?|Coordinator|Lead|Manager|Director|Chair))")
ROLE_ONLY = re.compile(rf"^\s*({ROLE})\s*:?\s*$", re.I)
ROLE_THEN_NAME = re.compile(rf"^\s*({ROLE})\s*[:\-–—|]\s*(.+?)\s*$", re.I)
NAME_THEN_ROLE = re.compile(rf"^\s*(.+?)\s*[:\-–—|,(]\s*({ROLE})\s*\)?\s*$", re.I)
NAME_TOKEN = r"[A-ZÀ-ÖØ-Þ][^\W\d_]*(?:['’-][^\W\d_]+)*"
NAME_RE = re.compile(rf"^{NAME_TOKEN}(?:\s+{NAME_TOKEN}){{1,3}}$")
NAME_STOP = {"the", "our", "society", "club", "union", "students", "student", "university", "college",
             "contact", "email", "join", "committee", "meet", "team", "more", "about", "find", "news",
             "events", "home", "read", "sign", "log", "membership", "president", "treasurer",
             "secretary", "captain", "officer", "chair", "vice", "welfare", "social", "kit", "get",
             "touch", "follow", "us", "instagram", "facebook", "twitter", "tiktok", "phone", "website",
             "sports", "sport", "page", "view", "all", "details", "information", "info", "and"}


def looks_like_name(value: str) -> bool:
    value = clean(value)
    if not NAME_RE.match(value):
        return False
    return not any(tok.lower().strip("'’") in NAME_STOP for tok in value.split())


def find_committee(lines: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        m = ROLE_THEN_NAME.match(line)
        if m and looks_like_name(m.group(2)):
            found.append((clean(m.group(2)), clean(m.group(1))))
            i += 1
            continue
        m = NAME_THEN_ROLE.match(line)
        if m and looks_like_name(m.group(1)):
            found.append((clean(m.group(1)), clean(m.group(2))))
            i += 1
            continue
        if ROLE_ONLY.match(line) and looks_like_name(nxt):
            found.append((clean(nxt), clean(ROLE_ONLY.match(line).group(1))))
            i += 2
            continue
        if looks_like_name(line) and ROLE_ONLY.match(nxt):
            found.append((clean(line), clean(ROLE_ONLY.match(nxt).group(1))))
            i += 2
            continue
        i += 1
    seen, out = set(), []
    for name, role in found:
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append((name, role))
    return out[:10]


def pick_lead(committee: list[tuple[str, str]]) -> str:
    for pattern in (r"^(?!.*vice).*president", r"^(?!.*vice).*chair", r"^(?!.*vice).*captain",
                    r"secretary"):
        for name, role in committee:
            if re.search(pattern, role, re.I):
                return name
    return committee[0][0] if committee else ""


TYPE_KEYWORDS = [
    ("Fashion", ["fashion", "style", "textile", "modelling", "modeling", "streetwear", "sneaker",
                 "thrift", "vintage", "vogue"]),
    ("Sports", ["football", "futsal", "rugby", "netball", "hockey", "cricket", "rowing", "boat club",
                "basketball", "tennis", "squash", "badminton", "swimming", "water polo", "athletic",
                "cross country", "running", "boxing", "kickboxing", "martial", "karate", "judo",
                "taekwondo", "jitsu", "fencing", "volleyball", "lacrosse", "cheer", "gymnastic",
                "trampolin", "climbing", "mountaineering", "cycling", "triathlon", "golf", "sailing",
                "surf", "ski", "snowboard", "canoe", "kayak", "ultimate", "frisbee", "handball",
                "korfball", "polo", "equestrian", "archery", "table tennis", "snooker", "darts",
                "lifesaving", "dodgeball", "quidditch", "quadball", "rounders", "gaelic", "hurling",
                "ice hockey", "skating", "powerlifting", "weightlifting", "barbell", "fitness",
                "sport", "athletics union", "rugby league", "pole"]),
    ("Faith", ["christian", "catholic", "islamic", "isoc", "muslim", "jewish", "jsoc", "hindu", "sikh",
               "buddhist", "bahai", "methodist", "anglican", "chaplaincy", "orthodox", "faith"]),
    ("Cultural", ["african", "caribbean", "afro", "asian", "chinese", "hong kong", "malaysian",
                  "singapore", "indian", "pakistani", "bangladeshi", "lankan", "nepalese", "korean",
                  "japanese", "vietnamese", "thai", "filipino", "indonesian", "arab", "turkish",
                  "persian", "iranian", "kurdish", "hellenic", "greek", "cypriot", "italian",
                  "spanish", "hispanic", "latin", "portuguese", "brazilian", "french", "german",
                  "polish", "romanian", "ukrainian", "nordic", "scandinavian", "irish", "scottish",
                  "welsh", "nigerian", "ghanaian", "somali", "kenyan", "eritrean", "ethiopian",
                  "egyptian", "lebanese", "palestinian", "international", "cultural", "culture",
                  "erasmus", "tamil", "punjabi", "gujarati", "bengali", "cssa"]),
    ("Media", ["radio", "tv", "television", "newspaper", "magazine", "journalism", "media",
               "publication", "podcast", "press"]),
    ("Arts", ["drama", "theatre", "theater", "musical", "music", "choir", "chorus", "orchestra",
              "band", "jazz", "a cappella", "acapella", "opera", "film", "cinema", "art", "painting",
              "photography", "dance", "ballet", "salsa", "ballroom", "poetry", "comedy", "improv",
              "writing", "literature", "gospel", "dj", "kpop", "k-pop"]),
    ("Academic", ["law", "medic", "medical", "medicine", "dental", "dentistry", "nursing", "midwifery",
                  "pharmacy", "veterinary", "engineering", "engineers", "economics", "finance",
                  "accounting", "business", "entrepreneur", "marketing", "consulting", "investment",
                  "physics", "chemistry", "biology", "biochemistry", "maths", "mathematics",
                  "computing", "computer science", "data science", "robotics", "psychology",
                  "history", "geography", "politics", "philosophy", "classics", "linguistics",
                  "languages", "architecture", "geology", "neuroscience", "sociology",
                  "anthropology", "criminology", "education", "physio", "optometry", "astronomy",
                  "debating", "debate", "model un", "coding", "hack", "tech"]),
    ("Volunteering", ["volunteer", "volunteering", "charity", "rag", "raise and give", "amnesty",
                      "unicef", "oxfam", "red cross", "marrow", "habitat", "enactus", "fundraising",
                      "mentoring", "tutoring", "first aid", "sustainability", "environment",
                      "climate"]),
    ("Social", ["social", "gaming", "games", "board game", "anime", "cooking", "baking", "food",
                "wine", "beer", "cocktail", "book club", "chess", "poker", "magic", "lgbt", "pride",
                "mature students", "postgrad", "travel", "hiking", "walking", "feminist"]),
]


def _keyword_hit(keyword: str, text: str, words: list[str]) -> bool:
    if " " in keyword or "-" in keyword:
        return re.search(r"\b" + re.escape(keyword) + r"\b", text) is not None
    for w in words:
        if w in (keyword, keyword + "s", keyword + "es", keyword + "ing") or \
                (len(keyword) >= 5 and w.startswith(keyword)):
            return True
    return False


def classify(name: str, url: str = "") -> str:
    text = name.lower()
    words = re.findall(r"[a-z0-9]+", text)
    for label, keywords in TYPE_KEYWORDS:
        if any(_keyword_hit(k, text, words) for k in keywords):
            return label
    if re.search(r"/sport|athletic", url.lower()):
        return "Sports"
    return "Other"


GENERIC_NAME = re.compile(
    r"^(home|welcome.*|society|societies( .*)?|clubs?|sports?|sports clubs?|groups?|activities|"
    r"a ?[-–] ?z.*|all .*|find .*|browse .*|start .*|search.*|our .*|join .*|about.*|contact.*|"
    r"committee.*|log ?in|sign ?in|sign ?up|register|page not found|not found|error.*|404.*|"
    r"events?|news|resources?|help|faqs?|instagram|facebook|twitter|x|tiktok|youtube|linkedin|"
    r"e-?mail|website|more info.*|read more|view .*|menu|[\W_]*)$", re.I)


def is_generic_name(name: str, org: str = "") -> bool:
    n = clean(name)
    if len(n) < 2 or len(n) > 90 or GENERIC_NAME.match(n):
        return True
    if org and norm(n) == norm(org):
        return True
    return bool(re.search(r"\b(societies|clubs|groups|activities|directory)$", n, re.I))


def strip_site_suffix(title: str) -> str:
    title = re.sub(r"^welcome to\s+", "", clean(title), flags=re.I)
    parts = [p for p in re.split(r"\s+[|–—•·:»-]\s+", title) if p]
    if len(parts) <= 1:
        return title
    for part in parts:
        if not re.search(r"\b(union|guild|students'?|university|su)\b", part, re.I):
            return part
    return parts[0]


def name_candidates(soup) -> list[str]:
    cands = []
    for h1 in soup.find_all("h1"):
        if h1.find_parent(["nav", "footer"]):
            continue
        if h1.find_parent("header") and not h1.find_parent(["main", "article"]):
            continue
        cands.append(re.sub(r"^welcome to\s+", "", clean(h1.get_text(" ")), flags=re.I))
    for attrs in ({"property": "og:title"}, {"name": "twitter:title"}):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            cands.append(strip_site_suffix(meta["content"]))
    if soup.title:
        cands.append(strip_site_suffix(soup.title.get_text()))
    return dedupe(c for c in cands if c and 2 <= len(c) <= 90)


CHROME_NAME = re.compile(r"^(site-?footer|site-?header|footer|global-?nav|main-?nav|navbar|nav-?bar|"
                         r"mega-?menu|cookie.*|breadcrumbs?|masthead|skip-?link.*)$", re.I)


def strip_page_chrome(soup) -> None:
    """Removes menus, headers and footers so the union's own contact details
    don't get attached to every society."""
    targets = soup.find_all(["script", "style", "noscript", "template", "svg", "iframe", "nav", "footer"])
    targets += [h for h in soup.find_all("header") if not h.find_parent(["main", "article"])]
    targets += soup.find_all(True, attrs={"class": CHROME_NAME})
    targets += soup.find_all(True, attrs={"id": CHROME_NAME})
    for tag in targets:
        if not getattr(tag, "decomposed", False):
            tag.decompose()


BLOCK_TAGS = ["p", "div", "li", "tr", "td", "th", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "aside", "header", "blockquote", "table", "ul", "ol", "dl",
              "figcaption", "main"]


def block_lines(soup) -> list[str]:
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.append("\n")
    return [clean(line) for line in soup.get_text().split("\n") if clean(line)]


# ---------------------------------------------------------------- findings --

@dataclass
class Finding:
    org: str
    society: str
    source: str
    type: str = "Other"
    emails: list = field(default_factory=list)
    phones: list = field(default_factory=list)
    socials: list = field(default_factory=list)
    committee: list = field(default_factory=list)
    own_sites: list = field(default_factory=list)   # the society's own website, if it links to one
    extra_from: list = field(default_factory=list)  # where deep search found anything extra

    def merge(self, other: "Finding") -> None:
        self.emails = dedupe(self.emails + other.emails)
        self.phones = dedupe(self.phones + other.phones)
        self.socials = dedupe(self.socials + other.socials)
        self.own_sites = dedupe(self.own_sites + other.own_sites)
        self.extra_from = dedupe(self.extra_from + other.extra_from)
        names = {n.lower() for n, _ in self.committee}
        self.committee += [(n, r) for n, r in other.committee if n.lower() not in names]

    def has_contact(self) -> bool:
        return bool(self.emails or self.phones or self.socials)

    def to_row(self) -> dict:
        insta = [u for u in self.socials if "instagram.com" in u]
        others = [u for u in self.socials if "instagram.com" not in u]
        notes = []
        if any(re.search(r"kit|merch|sponsor", role, re.I) for _, role in self.committee):
            notes.append("Has a kit, merch or sponsorship role")
        if not self.has_contact():
            notes.append("No contact details published on this page")
        if self.extra_from:
            notes.append("Extra details from " + ", ".join(self.extra_from[:3]))
        return {
            "org": self.org,
            "society": self.society,
            "type": self.type,
            "president": pick_lead(self.committee),
            "email": self.emails[0] if self.emails else "",
            "other_emails": "; ".join(self.emails[1:5]),
            "phone": self.phones[0] if self.phones else "",
            "instagram": insta[0] if insta else "",
            "other_socials": "; ".join(others[:5]),
            "committee": "; ".join(f"{n} ({r})" if r else n for n, r in self.committee[:8]),
            "source": self.source,
            "status": "Needs checking",
            "added_via": "Web search",
            "date_found": today(),
            "notes": "; ".join(notes),
        }


SITE_LABEL = re.compile(r"website|home ?page|our site|visit us|more info", re.I)


def find_own_sites(soup, page_url: str, name: str) -> list[str]:
    """Links from a society's union page out to its own website."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if len(t) >= 4]
    here = bare_host(urlparse(page_url).netloc)
    out = []
    for a in soup.find_all("a", href=True):
        url = normalise_url(urljoin(page_url, a["href"]))
        if not url:
            continue
        host = bare_host(urlparse(url).netloc)
        if not host or host == here or social_url(url) or FILE_EXT.search(urlparse(url).path):
            continue
        if any(bad in host for bad in BAD_RESULT_HOSTS) or host.endswith(".ac.uk") is False and \
                not any(t in host.replace("-", "") for t in tokens) and \
                not SITE_LABEL.search(clean(a.get_text(" "))):
            continue
        out.append(f"{urlparse(url).scheme}://{host}/")
    return dedupe(out)[:2]


def extract_finding(soup, url: str, org: str, name: str) -> Finding:
    lines = block_lines(soup)
    text = "\n".join(lines)
    return Finding(org=org, society=name, source=url,
                   emails=find_emails(soup, text), phones=find_phones(soup, text),
                   socials=find_socials(soup, text), committee=find_committee(lines),
                   own_sites=find_own_sites(soup, url, name))


def inline_findings(soup, url: str, org: str) -> list[Finding]:
    """Picks up societies listed with their contacts directly on an A-Z page."""
    out = []
    for el in soup.find_all(["tr", "li", "article"]):
        if el.find(["tr", "li", "article"]):
            continue
        text = clean(el.get_text(" "))
        if not text or len(text) > 400:
            continue
        emails, socials = find_emails(el, text), find_socials(el, text)
        if not emails and not socials:
            continue
        name = ""
        if el.name == "tr":
            cells = el.find_all(["td", "th"])
            if cells:
                name = clean(cells[0].get_text(" "))
        if not name:
            for tag in el.find_all(["h2", "h3", "h4", "h5", "strong", "b", "a"]):
                cand = clean(tag.get_text(" "))
                if cand and "@" not in cand and not social_url(tag.get("href", "")) and len(cand) <= 80:
                    name = cand
                    break
        if not name:
            name = clean(re.split(r"[:\-–—|@]", text)[0])[:80]
        if not name or "@" in name or is_generic_name(name, org):
            continue
        out.append(Finding(org=org, society=name, source=url, emails=emails, socials=socials,
                           phones=find_phones(el, text)))
    return out


# ----------------------------------------------------------------- crawling --

SOCIETY_HINTS = ("societ", "/groups/", "/group/", "/organisation", "/clubs/", "/club/", "/sport",
                 "/activities/", "/activity/", "/teams/", "/team/")
SKIP_SEGMENTS = {"login", "logout", "signin", "sign-in", "register", "basket", "cart", "checkout",
                 "shop", "store", "news", "events", "event", "tickets", "ticket", "jobs", "vacancies",
                 "elections", "vote", "privacy", "cookies", "terms", "accessibility", "search",
                 "calendar", "feed", "rss", "account", "myaccount", "sitemap", "join", "purchase",
                 "buy", "help", "faq", "faqs", "documents", "resources", "awards", "training",
                 "admin", "wp-admin", "cdn-cgi", "print", "share", "gallery", "media-library"}
FILE_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|ico|docx?|xlsx?|pptx?|zip|mp4|mp3|ics|css|js|"
                      r"json|xml|txt)$", re.I)
LISTING_MIN_LINKS = 12


def is_society_url(url: str, hosts: set[str]) -> bool:
    p = urlparse(url)
    if bare_host(p.netloc) not in hosts:
        return False
    path = p.path.lower()
    if FILE_EXT.search(path):
        return False
    if any(seg in SKIP_SEGMENTS for seg in path.split("/") if seg):
        return False
    return any(h in path + "/" for h in SOCIETY_HINTS)


def society_links(soup, base_url: str, hosts: set[str]) -> list[str]:
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        url = normalise_url(urljoin(base_url, href))
        if not url or not is_society_url(url, hosts):
            continue
        key = url_key(url)
        if key not in seen:
            seen.add(key)
            out.append(url)
    return out


def contact_subpages(soup, page_url: str, hosts: set[str]) -> list[str]:
    """Finds 'Contact' or 'Committee' pages that sit under a society's page."""
    base = urlparse(page_url).path.rstrip("/")
    if base.count("/") < 1 or not base:
        return []
    out = []
    for a in soup.find_all("a", href=True):
        url = normalise_url(urljoin(page_url, a["href"]))
        if not url:
            continue
        p = urlparse(url)
        if bare_host(p.netloc) not in hosts or not p.path.startswith(base + "/"):
            continue
        label = clean(a.get_text(" ")).lower() + " " + p.path.lower()
        if re.search(r"contact|committee|exec|who we are|meet the", label):
            out.append(url)
    return dedupe(out)[:2]


def sitemap_society_urls(start_urls: list[str], fetcher: Fetcher, hosts: set[str],
                         limit: int = 600) -> list[str]:
    roots = []
    for u in start_urls:
        p = urlparse(u)
        roots += fetcher.sitemaps(u) or [f"{p.scheme}://{p.netloc}/sitemap.xml"]
    queue, seen, found = deque(dedupe(roots)), set(), []
    while queue and len(seen) < 15 and len(found) < limit:
        sitemap = queue.popleft()
        if sitemap in seen or sitemap.lower().endswith(".gz"):
            continue
        seen.add(sitemap)
        r = fetcher.get(sitemap, want_html=False)
        if r is None:
            continue
        for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text, re.I | re.S):
            loc = html.unescape(loc)
            if re.search(r"sitemap[^/]*\.xml", loc, re.I):
                queue.append(loc)
                continue
            url = normalise_url(loc)
            if url and is_society_url(url, hosts):
                found.append(url)
    return dedupe(found)[:limit]


def crawl_site(start_urls: list[str], org: str, fetcher: Fetcher, max_pages: int,
               progress: ProgressFn, log: LogFn, should_stop: StopFn) -> tuple[list[Finding], int]:
    hosts = {bare_host(urlparse(u).netloc) for u in start_urls}
    queue: deque = deque((u, None) for u in start_urls)
    seen = {url_key(u) for u in start_urls}
    findings: dict[str, Finding] = {}
    h1_seen: Counter = Counter()
    pages = 0
    sitemap_tried = False

    def add(f: Finding) -> None:
        key = norm(f.society)
        if key in findings:
            findings[key].merge(f)
        else:
            findings[key] = f

    def enqueue(url: str, parent: Optional[str] = None, front: bool = False) -> None:
        key = url_key(url)
        if key in seen:
            return
        seen.add(key)
        (queue.appendleft if front else queue.append)((url, parent))

    try:
        while pages < max_pages and not should_stop():
            if not queue:
                if not sitemap_tried and len(findings) < 5:
                    sitemap_tried = True
                    log("Checking the site map for more society pages.")
                    for u in sitemap_society_urls(start_urls, fetcher, hosts):
                        enqueue(u)
                    if queue:
                        continue
                break
            url, parent = queue.popleft()
            resp = fetcher.get(url)
            pages += 1
            progress(pages, max_pages, f"Checked {pages} pages, found {len(findings)} societies")
            if resp is None:
                continue
            final_url = normalise_url(resp.url) or url
            soup = BeautifulSoup(resp.content, "html.parser")

            names = name_candidates(soup)
            name = names[0] if names else ""
            if name and h1_seen[norm(name)] and len(names) > 1:
                name = names[1]  # same heading on several pages = site name, not society name
            if names:
                h1_seen[norm(names[0])] += 1
            subpages = contact_subpages(soup, final_url, hosts) if parent is None else []

            strip_page_chrome(soup)
            links = society_links(soup, final_url, hosts)

            if parent is not None:
                if parent in findings:
                    findings[parent].merge(extract_finding(soup, final_url, org, findings[parent].society))
                continue

            sub_keys = {url_key(u) for u in subpages}
            for link in links:
                if url_key(link) not in sub_keys:
                    enqueue(link)

            if len(links) >= LISTING_MIN_LINKS or not name or is_generic_name(name, org):
                for f in inline_findings(soup, final_url, org):
                    add(f)
                continue

            finding = extract_finding(soup, final_url, org, name)
            add(finding)
            key = norm(finding.society)
            for sub in subpages:  # committee/contact pages often list the kit or merch secretary
                enqueue(sub, parent=key, front=True)
    except KeyboardInterrupt:
        log("Stopped. Keeping what was found so far.")

    if pages >= max_pages and queue:
        log(f"Reached the {max_pages}-page limit. Raise 'Pages to check' to find more.")
    return list(findings.values()), pages


def remove_sitewide(findings: list[Finding]) -> None:
    """Drops contacts that appear on lots of pages (usually the union's own details)."""
    if len(findings) < 4:
        return
    counts: Counter = Counter()
    for f in findings:
        for value in set(f.emails) | set(f.phones) | set(f.socials):
            counts[value] += 1
    limit = max(3, round(len(findings) * 0.3))
    common = {v for v, c in counts.items() if c >= limit}
    for f in findings:
        f.emails = [e for e in f.emails if e not in common]
        f.phones = [p for p in f.phones if p not in common]
        f.socials = [s for s in f.socials if s not in common]


def rank_emails(f: Finding) -> None:
    tokens = [t for t in re.findall(r"[a-z]+", f.society.lower()) if len(t) >= 3]

    def score(email: str) -> int:
        local = email.split("@")[0]
        s = 0
        if any(t in local for t in tokens):
            s += 3
        if re.search(r"president|captain|chair|sec|soc|club|sponsor|kit|merch", local):
            s += 2
        if GENERIC_EMAIL.match(email):
            s -= 2
        return -s

    f.emails.sort(key=score)


# ---------------------------------------------------------- company search --

def company_search(name: str, fetcher: Fetcher, max_pages: int, progress: ProgressFn,
                   log: LogFn, should_stop: StopFn) -> tuple[list[Finding], int]:
    tokens = name_tokens(name) or [name.lower()]
    results: dict[str, dict] = {}
    queries = [f'"{name}" sports club', f'"{name}" social club', f'"{name}" staff network',
               f'"{name}" football team', f'"{name}" running club']
    for i, query in enumerate(queries):
        if should_stop():
            break
        if i:
            time.sleep(1.5)
        progress(0, max_pages, f"Searching the web ({i + 1} of {len(queries)})")
        for res in web_search(query, 8, log):
            url = normalise_url(res.get("url", ""))
            if url:
                results.setdefault(url_key(url), dict(res, url=url))

    findings: dict[str, Finding] = {}
    pages = 0
    todo = list(results.values())[:max_pages]
    for res in todo:
        if should_stop():
            break
        pages += 1
        progress(pages, len(todo), f"Checked {pages} of {len(todo)} results, found {len(findings)} groups")
        url, title, snippet = res["url"], clean(res.get("title")), clean(res.get("snippet"))
        blob = f"{title} {snippet}".lower()
        if not all(t in blob for t in tokens):
            continue
        social = social_url(url)
        if social:
            label = re.split(r"\s+[|•·(]\s*|\s+-\s+", title)[0] or title
            f = Finding(org=name, society=label, source=url, socials=[social],
                        emails=find_emails(BeautifulSoup("", "html.parser"), snippet))
        else:
            resp = fetcher.get(url)
            if resp is None:
                continue
            soup = BeautifulSoup(resp.content, "html.parser")
            cands = name_candidates(soup)
            label = cands[0] if cands else title
            strip_page_chrome(soup)
            if not all(t in soup.get_text(" ").lower() for t in tokens):
                continue
            f = extract_finding(soup, url, name, label)
        if f.has_contact() and not is_generic_name(f.society):
            key = norm(f.society)
            if key in findings:
                findings[key].merge(f)
            else:
                findings[key] = f
    return list(findings.values()), pages


# ----------------------------------------------- deep search for contacts --
# For societies whose union page shows no contact details, this looks further:
# the society's own website, its Linktree, and targeted web searches. Anything
# it finds is checked against the society's name before being kept.

DEEP_MAX_PAGES = 4          # pages opened per society, so runs stay reasonable
DEEP_SEARCH_RESULTS = 6

# Instagram, Facebook and TikTok are deliberately not opened: they need a login,
# block automated visits, and their terms forbid it. Linktree is a plain page.
LINK_HUBS = ("linktr.ee", "linktree.com", "beacons.ai", "bio.link", "carrd.co", "campsite.bio")


def society_tokens(name: str) -> list[str]:
    generic = {"society", "societies", "club", "union", "students", "student", "university",
               "college", "association", "group", "team", "the", "and", "for", "of"}
    return [t for t in re.findall(r"[a-z0-9]+", name.lower()) if len(t) >= 4 and t not in generic]


_OTHER_UNI_TOKENS: Optional[dict] = None


def _uni_token_map() -> dict:
    """Distinctive word -> university, from the bundled UK list. Used to spot a
    page that belongs to a different university."""
    global _OTHER_UNI_TOKENS
    if _OTHER_UNI_TOKENS is None:
        counts: Counter = Counter()
        per_uni = {}
        for uni in load_universities():
            toks = set(name_tokens(uni["name"])) | set(name_tokens(uni.get("city", "")))
            per_uni[uni["name"]] = toks
            counts.update(toks)
        _OTHER_UNI_TOKENS = {t: name for name, toks in per_uni.items() for t in toks
                             if counts[t] == 1}  # words that point at one university only
    return _OTHER_UNI_TOKENS


def names_another_university(headline: str, org: str) -> bool:
    low = headline.lower()
    ours = set(name_tokens(org))
    for token, uni in _uni_token_map().items():
        if token in ours:
            continue
        if re.search(r"\b" + re.escape(token) + r"\b", low):
            return True
    return False


def page_is_about(soup, tokens: list[str], org: str) -> bool:
    """Keeps deep search honest. The page must be about this society at this
    university, not a society of the same name somewhere else."""
    if not tokens:
        return False
    headline = clean(" ".join(
        [soup.title.get_text(" ") if soup.title else ""] +
        [h.get_text(" ") for h in soup.find_all(["h1", "h2"])[:3]]))
    low = soup.get_text(" ").lower()
    hits = sum(1 for t in tokens if t in low)
    if hits < 1:
        return False
    ours_present = any(t in low for t in name_tokens(org))

    # A page naming a different university is rejected unless ours is in its heading.
    if names_another_university(headline, org) and \
            not any(t in headline.lower() for t in name_tokens(org)):
        return False
    if len(tokens) >= 2:
        return hits >= 2 or (hits >= 1 and ours_present)
    return ours_present  # one-word society names need the university named too


def deep_candidates(f: Finding, log: LogFn) -> list[str]:
    """Where to look next, best first."""
    urls = list(f.own_sites)
    urls += [u for u in f.socials if any(h in u for h in LINK_HUBS)]

    tokens = society_tokens(f.society)
    query = f'"{f.society}" {f.org} society contact email'
    for res in web_search(query, DEEP_SEARCH_RESULTS, log):
        url = normalise_url(res.get("url", ""))
        if not url:
            continue
        host = bare_host(urlparse(url).netloc)
        blob = f"{res.get('title', '')} {res.get('snippet', '')}".lower()
        if not host or FILE_EXT.search(urlparse(url).path):
            continue
        if any(hub in host for hub in LINK_HUBS):
            urls.append(url)
            continue
        if any(bad in host for bad in BAD_RESULT_HOSTS):
            continue  # social networks and directories: can't or shouldn't be scraped
        if url_key(url) == url_key(f.source):
            continue  # the union page we already read
        if sum(1 for t in tokens if t in blob or t in host.replace("-", "")) >= 1:
            urls.append(url)
    return dedupe(urls)


def deep_search_one(f: Finding, fetcher: Fetcher, log: LogFn, should_stop: StopFn) -> bool:
    """Fills in one society's missing contacts. Returns True if anything was added."""
    tokens = society_tokens(f.society)
    before = (len(f.emails), len(f.phones), len(f.socials), len(f.committee))
    opened = 0
    for url in deep_candidates(f, log):
        if opened >= DEEP_MAX_PAGES or should_stop():
            break
        resp = fetcher.get(url)
        opened += 1
        if resp is None:
            continue
        final = normalise_url(resp.url) or url
        soup = BeautifulSoup(resp.content, "html.parser")
        if not page_is_about(soup, tokens, f.org):
            continue  # a different society, or an unrelated page
        strip_page_chrome(soup)
        found = extract_finding(soup, final, f.org, f.society)
        if found.has_contact() or found.committee:
            f.merge(found)
            f.extra_from.append(bare_host(urlparse(final).netloc))
        if f.emails and f.committee:
            break  # got what was missing
    return (len(f.emails), len(f.phones), len(f.socials), len(f.committee)) != before


def deep_search(findings: list, fetcher: Fetcher, progress: ProgressFn, log: LogFn,
                should_stop: StopFn, limit: int = 60) -> int:
    """Runs the deep pass over every society still missing an email."""
    todo = [f for f in findings if not f.emails][:limit]
    if not todo:
        return 0
    log(f"Looking further for {plural(len(todo), 'society', 'societies')} with no email yet.")
    improved = 0
    for i, f in enumerate(todo, 1):
        if should_stop():
            break
        progress(i, len(todo), f"Deeper search {i} of {len(todo)}: {f.society}")
        try:
            if deep_search_one(f, fetcher, log, should_stop):
                improved += 1
        except requests.RequestException:
            continue
        if i < len(todo):
            time.sleep(1.0)  # the search engine is a shared resource too
    return improved


# ----------------------------------------------- the UK universities list --

def load_universities(path: Path = None) -> list[dict]:
    """The bundled list of every UK university with degree-awarding powers."""
    path = Path(path or UNIVERSITIES_CSV)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [{k: clean(v) for k, v in row.items() if k} for row in csv.DictReader(fh)]
    return [r for r in rows if r.get("name")]


def load_union_sites() -> dict:
    """Societies pages already found, so later runs skip the searching."""
    sites = {}
    if UNION_SITES_CSV.exists():
        with open(UNION_SITES_CSV, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if row.get("name") and row.get("union_url"):
                    sites[norm(row["name"])] = {"union_url": clean(row["union_url"]),
                                                "found_on": clean(row.get("found_on"))}
    return sites


def save_union_site(name: str, url: str) -> None:
    sites = load_union_sites()
    sites[norm(name)] = {"union_url": url, "found_on": today()}
    known = {norm(u["name"]): u["name"] for u in load_universities()}
    UNION_SITES_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = UNION_SITES_CSV.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, lineterminator="\r\n")
        writer.writerow(["name", "union_url", "found_on"])
        for key, row in sorted(sites.items()):
            writer.writerow([known.get(key, name if key == norm(name) else key),
                             row["union_url"], row["found_on"]])
    try:
        os.replace(tmp, UNION_SITES_CSV)
    except PermissionError:
        tmp.unlink(missing_ok=True)


# Paths used by the platforms most UK students' unions run on, plus common
# hand-built ones. Probed directly so a university works even when the web
# search can't find its societies page.
UNION_PATHS = ["/organisation/", "/groups/", "/activities/", "/societies",
               "/societies/", "/clubs-and-societies", "/student-groups",
               "/sports-and-societies", "/get-involved/societies", "/activities/societies",
               "/clubs", "/sport/clubs", "/whats-on/societies", "/join/societies"]


def probe_union_paths(host_url: str, fetcher: Fetcher, org: str) -> tuple[str, int]:
    """Tries the standard societies-page paths on one union website.
    Returns the page with the most society links, and how many it had."""
    hosts = {bare_host(urlparse(host_url).netloc)}
    best, best_count = "", 0
    misses = 0
    for path in UNION_PATHS:
        url = normalise_url(urljoin(host_url, path))
        if not url:
            continue
        resp = fetcher.get(url)
        if resp is None:
            misses += 1
            if misses >= 4 and not best_count:
                break  # the website isn't answering, or isn't laid out this way
            continue
        final = normalise_url(resp.url) or url
        soup = BeautifulSoup(resp.content, "html.parser")
        strip_page_chrome(soup)
        count = len(society_links(soup, final, hosts))
        if count > best_count:
            best, best_count = final, count
        if best_count >= 40:  # clearly the full A-Z, no need to keep probing
            break
    return best, best_count


def find_union_site(name: str, fetcher: Fetcher, log: LogFn = print) -> str:
    """Finds a university's societies page: cache first, then the standard
    paths on its union website, then a plain web search."""
    cached = load_union_sites().get(norm(name))
    if cached:
        return cached["union_url"]

    tokens = name_tokens(name)
    candidates: list[tuple[int, str]] = []
    for res in web_search(f"{name} students union", 8, log):
        url = normalise_url(res.get("url", ""))
        if not url:
            continue
        host = bare_host(urlparse(url).netloc)
        if not host or any(bad in host for bad in BAD_RESULT_HOSTS):
            continue
        score = 0
        if any(h in host for h in UNION_HINTS):
            score += 3
        if any(t in host for t in tokens):
            score += 2
        if re.search(r"union|guild|students", res.get("title", ""), re.I):
            score += 1
        candidates.append((score, f"https://{host}/"))

    seen, ordered = set(), []
    for score, root in sorted(candidates, key=lambda x: -x[0]):
        if root not in seen and score > 0:
            seen.add(root)
            ordered.append(root)

    for root in ordered[:3]:
        url, count = probe_union_paths(root, fetcher, name)
        if count >= 8:
            save_union_site(name, url)
            return url

    found = find_directories(name, log)
    if found:
        save_union_site(name, found[0])
        return found[0]
    return ""


# ------------------------------------------------------------- main search --

@dataclass
class SearchResult:
    org: str
    rows: list
    pages_checked: int = 0
    start_urls: list = field(default_factory=list)
    note: str = ""


def run_search(name: str, url: Optional[str] = None, company: bool = False,
               max_pages: int = DEFAULT_MAX_PAGES, delay: float = DEFAULT_DELAY,
               only_with_contacts: bool = False, deep: bool = False,
               progress: Optional[ProgressFn] = None, log: Optional[LogFn] = None,
               should_stop: Optional[StopFn] = None) -> SearchResult:
    progress = progress or (lambda done, total, msg: None)
    log = log or (lambda msg: None)
    should_stop = should_stop or (lambda: False)
    org = clean(name)
    if not org:
        raise ValueError("Enter a university or company name.")
    fetcher = Fetcher(delay=delay, log=log)
    start_urls: list[str] = []

    if url and clean(url):
        raw = clean(url)
        start = normalise_url(raw if re.match(r"^https?://", raw, re.I) else "https://" + raw)
        if not start:
            raise ValueError("That web address doesn't look right. It should start with https://")
        start_urls = [start]
        findings, pages = crawl_site(start_urls, org, fetcher, max_pages, progress, log, should_stop)
        if findings:
            save_union_site(org, start)  # reuse it next time instead of searching
    elif company:
        findings, pages = company_search(org, fetcher, max_pages, progress, log, should_stop)
    else:
        progress(0, max_pages, "Looking for the students' union societies page")
        found = find_union_site(org, fetcher, log)
        start_urls = [found] if found else find_directories(org, log)
        if not start_urls:
            return SearchResult(org, [], 0, [], note=(
                "Couldn't find the students' union societies page automatically. Open the union's "
                "website, find its Societies or Clubs A-Z page, and paste that address in."))
        log("Starting from: " + ", ".join(start_urls))
        findings, pages = crawl_site(start_urls, org, fetcher, max_pages, progress, log, should_stop)

    remove_sitewide(findings)
    if deep and findings and not should_stop():
        improved = deep_search(findings, fetcher, progress, log, should_stop)
        if improved:
            log(f"Deeper search filled in {plural(improved, 'society', 'societies')}.")
    for f in findings:
        rank_emails(f)
        f.type = classify(f.society, f.source)
    rows = [f.to_row() for f in findings]
    if only_with_contacts:
        rows = [r for r in rows if r["email"] or r["phone"] or r["instagram"] or r["other_socials"]]
    rows.sort(key=lambda r: r["society"].lower())
    note = "" if rows else ("No societies found. If you searched by name, try pasting the union's "
                            "Societies A-Z page address instead.")
    return SearchResult(org, rows, pages, start_urls, note)


# ------------------------------------------------------------ saving to CSV --

class FileLockedError(Exception):
    """Raised when a CSV can't be replaced, usually because it's open in Excel."""

    def __init__(self, path: Path):
        super().__init__(str(path))
        self.path = path


def safe_cell(value) -> str:
    """Stops spreadsheet apps from treating text as a formula."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def to_csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow([safe_cell(row.get(k, "")) for k in FIELDS])
    return buf.getvalue()


def write_csv(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        fh.write(to_csv_text(rows))
    try:
        os.replace(tmp, path)
    except PermissionError:
        alt = path.with_name(f"{path.stem} (copy {dt.datetime.now():%H%M%S}){path.suffix}")
        os.replace(tmp, alt)
        raise FileLockedError(alt)
    return path


def read_csv(path: Path = MASTER_CSV) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = []
        for raw in csv.DictReader(fh):
            row = {}
            for key, label in COLUMNS:
                value = (raw.get(label) or raw.get(key) or "").strip()
                if value.startswith("'") and value[1:2] in ("=", "+", "-", "@"):
                    value = value[1:]
                row[key] = value
            rows.append(row)
    return rows


def row_key(row: dict) -> str:
    return norm(row.get("org")) + "|" + norm(row.get("society"))


def merge_rows(master: list[dict], new_rows: list[dict]) -> tuple[int, int]:
    """Adds new societies and fills blanks in ones already saved. Never overwrites."""
    index = {row_key(r): r for r in master}
    added = updated = 0
    for row in new_rows:
        key = row_key(row)
        if key not in index:
            master.append(dict(row))
            index[key] = master[-1]
            added += 1
            continue
        current, changed = index[key], False
        for f in FIELDS:
            if f in ("status", "added_via", "date_found"):
                continue
            if not current.get(f) and row.get(f):
                current[f] = row[f]
                changed = True
        updated += changed
    return added, updated


def import_master(csv_text: str) -> tuple[int, int]:
    """Merges a previously downloaded master CSV back in. Used online, where the
    app's own storage is wiped whenever it restarts."""
    incoming = []
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    for raw in reader:
        row = {}
        for key, label in COLUMNS:
            value = clean(raw.get(label) or raw.get(key) or "")
            if value.startswith("'") and value[1:2] in ("=", "+", "-", "@"):
                value = value[1:]
            row[key] = value
        if row.get("org") and row.get("society"):
            incoming.append(row)
    if not incoming:
        raise ValueError("That file has no societies in it. Upload a CSV that SocietyScout made.")
    master = read_csv(MASTER_CSV)
    added, updated = merge_rows(master, incoming)
    write_csv(master, MASTER_CSV)
    return added, updated


def export_path(org: str) -> Path:
    return EXPORT_DIR / f"societies-{slug(org)}-{today()}.csv"


def save_results(org: str, rows: list[dict]) -> dict:
    """Merges results into the master CSV and writes a per-university CSV."""
    master = read_csv(MASTER_CSV)
    added, updated = merge_rows(master, rows)
    info = {"added": added, "updated": updated, "master": str(MASTER_CSV), "locked": ""}
    try:
        write_csv(master, MASTER_CSV)
    except FileLockedError as exc:
        info["locked"] = str(exc.path)
    org_rows = [r for r in master if norm(r["org"]) == norm(org)]
    try:
        info["export"] = str(write_csv(org_rows, export_path(org)))
    except FileLockedError as exc:
        info["export"] = str(exc.path)
    return info


def parse_committee_text(text: str) -> str:
    members = []
    for line in (text or "").splitlines():
        line = clean(line)
        if not line:
            continue
        parts = re.split(r"\s+[-–—]\s+|\s*[:,(]\s*", line, maxsplit=1)
        name = clean(parts[0])
        role = clean(parts[1].rstrip(")")) if len(parts) > 1 else ""
        members.append(f"{name} ({role})" if role else name)
    return "; ".join(members)


def add_manual(org: str, society: str, type_: str = "Other", president: str = "", email: str = "",
               phone: str = "", instagram: str = "", website: str = "", committee: str = "",
               notes: str = "") -> tuple[bool, str]:
    org, society = clean(org), clean(society)
    if not org or not society:
        return False, "Add the university or company and the society name, then save."
    emails = clean_emails([email])
    warning = ""
    if clean(email) and not emails:
        emails = [clean(email)]
        warning = " The email looks incomplete, so it was saved as typed. Check it in Saved societies."
    ig = clean(instagram)
    if "instagram.com" in ig.lower():
        ig = social_url(ig if ig.startswith("http") else "https://" + ig) or ig
    elif re.fullmatch(r"@?[A-Za-z0-9._]{1,30}", ig):
        ig = f"https://www.instagram.com/{ig.lstrip('@').lower()}/"
    row = {
        "org": org, "society": society, "type": type_ or "Other", "president": clean(president),
        "email": emails[0] if emails else "", "other_emails": "; ".join(emails[1:]),
        "phone": format_phone(phone) or clean(phone), "instagram": ig, "other_socials": "",
        "committee": parse_committee_text(committee), "source": clean(website),
        "status": "Checked", "added_via": "Manual", "date_found": today(), "notes": clean(notes),
    }
    master = read_csv(MASTER_CSV)
    added, updated = merge_rows(master, [row])
    try:
        write_csv(master, MASTER_CSV)
    except FileLockedError as exc:
        return False, ("The master file is open in another program (probably Excel), so it couldn't "
                       f"be updated. Close it and save again. A copy was saved to {exc.path}")
    if added:
        return True, f"Saved {society}.{warning}"
    if updated:
        return True, f"{society} was already saved, so its missing details were filled in.{warning}"
    return True, f"{society} is already saved with these details."


# -------------------------------------------- running the whole UK at once --

COVERAGE_COLUMNS = ["name", "city", "nation", "societies", "with_email", "union_url",
                    "status", "last_run", "note"]


def load_coverage() -> dict:
    rows = {}
    if COVERAGE_CSV.exists():
        with open(COVERAGE_CSV, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if row.get("name"):
                    rows[norm(row["name"])] = {k: clean(row.get(k)) for k in COVERAGE_COLUMNS}
    return rows


def save_coverage(rows: dict) -> None:
    COVERAGE_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = COVERAGE_CSV.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=COVERAGE_COLUMNS, lineterminator="\r\n",
                                extrasaction="ignore")
        writer.writeheader()
        for row in sorted(rows.values(), key=lambda r: r["name"].lower()):
            writer.writerow({k: safe_cell(row.get(k, "")) for k in COVERAGE_COLUMNS})
    try:
        os.replace(tmp, COVERAGE_CSV)
    except PermissionError:
        tmp.unlink(missing_ok=True)


def coverage_summary() -> dict:
    universities = load_universities()
    coverage = load_coverage()
    done = [r for r in coverage.values() if r.get("status") == "Done"]
    return {
        "total": len(universities),
        "done": len(done),
        "no_page": sum(1 for r in coverage.values() if r.get("status") == "No societies page"),
        "failed": sum(1 for r in coverage.values() if r.get("status") == "Failed"),
        "remaining": len([u for u in universities if norm(u["name"]) not in coverage]),
        "societies": sum(int(r.get("societies") or 0) for r in done),
    }


def run_all(universities: Optional[list] = None, max_pages: int = DEFAULT_MAX_PAGES,
            delay: float = DEFAULT_DELAY, redo: bool = False,
            only_with_contacts: bool = False, deep: bool = False,
            progress: Optional[ProgressFn] = None,
            log: Optional[LogFn] = None, should_stop: Optional[StopFn] = None) -> dict:
    """Works through every UK university. Safe to stop and restart: it skips the
    ones already done and picks up where it left off."""
    progress = progress or (lambda done, total, msg: None)
    log = log or (lambda msg: None)
    should_stop = should_stop or (lambda: False)
    universities = universities if universities is not None else load_universities()
    coverage = load_coverage()

    todo = [u for u in universities
            if redo or coverage.get(norm(u["name"]), {}).get("status") != "Done"]
    total = len(todo)
    log(f"{total} to do, {len(universities) - total} already done.")

    for i, uni in enumerate(todo, 1):
        if should_stop():
            log("Stopped. Run it again to carry on from here.")
            break
        name = uni["name"]
        progress(i - 1, total, f"{i} of {total}: {name}")
        record = {"name": name, "city": uni.get("city", ""), "nation": uni.get("nation", ""),
                  "societies": "0", "with_email": "0", "union_url": uni.get("union_url", ""),
                  "status": "", "last_run": today(), "note": ""}
        try:
            result = run_search(name, url=uni.get("union_url") or None, max_pages=max_pages,
                                delay=delay, only_with_contacts=only_with_contacts, deep=deep,
                                progress=lambda d, t, m: progress(i - 1, total, f"{i}/{total} {name}: {m}"),
                                log=log, should_stop=should_stop)
            if result.rows:
                saved = save_results(result.org, result.rows)
                record.update(
                    societies=str(len(result.rows)),
                    with_email=str(sum(1 for r in result.rows if r["email"])),
                    union_url=result.start_urls[0] if result.start_urls else "",
                    status="Done",
                    note=f"{saved['added']} new, {saved['updated']} updated",
                )
                log(f"{name}: {len(result.rows)} societies.")
            else:
                record.update(status="No societies page", note=result.note[:200])
                log(f"{name}: nothing found.")
        except KeyboardInterrupt:
            log("Stopped. Run it again to carry on from here.")
            break
        except Exception as exc:  # one bad website must not end the whole run
            record.update(status="Failed", note=f"{exc.__class__.__name__}: {exc}"[:200])
            log(f"{name}: failed ({exc.__class__.__name__}). Moving on.")
        coverage[norm(name)] = record
        save_coverage(coverage)  # saved after each one, so nothing is lost
        progress(i, total, f"{i} of {total} done")
    return coverage_summary()


# ------------------------------------------------------------ command line --

def _read_jobs(path: str) -> list[tuple[str, Optional[str]]]:
    jobs = []
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.search(r"https?://\S+", line)
            url = match.group() if match else None
            name = clean(line.replace(url, "").strip(" ,\t")) if url else line
            jobs.append((name, url))
    return jobs


def _run_and_report(name: str, url: Optional[str] = None, company: bool = False,
                    max_pages: int = DEFAULT_MAX_PAGES, delay: float = DEFAULT_DELAY,
                    only_with_contacts: bool = False, deep: bool = False) -> list[dict]:
    def progress(done, total, msg):
        print(f"\r  {msg}".ljust(70), end="", flush=True)

    def log(msg):
        print(f"\n  {msg}")

    result = run_search(name, url=url, company=company, max_pages=max_pages, delay=delay,
                        only_with_contacts=only_with_contacts, deep=deep,
                        progress=progress, log=log)
    print()
    if not result.rows:
        print(f"  {result.note}")
        return []
    info = save_results(result.org, result.rows)
    with_email = sum(1 for r in result.rows if r["email"])
    print(f"  Found {len(result.rows)} societies ({with_email} with an email): "
          f"{info['added']} new, {info['updated']} updated.")
    print(f"  Saved: {info['export']}")
    if info["locked"]:
        print(f"  The master file is open in another program, so a copy was saved to {info['locked']}")
    else:
        print(f"  Master list: {info['master']}")
    return result.rows


def find_societies(name: str, url: Optional[str] = None, company: bool = False,
                   max_pages: int = DEFAULT_MAX_PAGES, only_with_contacts: bool = False,
                   deep: bool = False) -> list[dict]:
    """For notebooks (Jupyter, Colab) and the Python shell:
        find_societies("University of Leeds")
        find_societies("University of Leeds", url="https://<union-site>/societies")
    Saves the CSVs and returns the rows."""
    return _run_and_report(name, url=url, company=company, max_pages=max_pages,
                           only_with_contacts=only_with_contacts, deep=deep)


def _running_interactively() -> bool:
    return "ipykernel" in sys.modules or "google.colab" in sys.modules or hasattr(sys, "ps1")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    parser = argparse.ArgumentParser(
        description="Find publicly posted contact details for university societies and save them to CSV.")
    parser.add_argument("name", nargs="?", help='University or company name, e.g. "University of Leeds"')
    parser.add_argument("--url", help="Students' union societies page to start from (skips the web search)")
    parser.add_argument("--company", action="store_true", help="Look for a company's staff clubs instead")
    parser.add_argument("--file", help="Text file with one university per line (optionally followed by a URL)")
    parser.add_argument("--all", action="store_true",
                        help="Work through every UK university in uk_universities.csv. "
                             "Safe to stop and restart: it carries on where it left off.")
    parser.add_argument("--redo", action="store_true", help="With --all, run universities already done again")
    parser.add_argument("--nation", help="With --all, limit to England, Scotland, Wales or Northern Ireland")
    parser.add_argument("--coverage", action="store_true", help="Show how far through the UK list you are")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Pages to check per search")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Seconds between requests")
    parser.add_argument("--deep", action="store_true",
                        help="For societies with no email on the union page, also check their own "
                             "website, Linktree and web search results. Slower but finds more.")
    parser.add_argument("--only-with-contacts", action="store_true",
                        help="Leave out societies with no contact details")
    args = parser.parse_args(argv)

    if args.coverage:
        c = coverage_summary()
        print(f"{c['done']} of {c['total']} UK universities done, holding {c['societies']} societies.")
        print(f"  {c['remaining']} not tried yet, {c['no_page']} with no societies page found, "
              f"{c['failed']} failed.")
        if COVERAGE_CSV.exists():
            print(f"  Details: {COVERAGE_CSV}")
        return 0

    if args.all:
        universities = load_universities()
        if not universities:
            print(f"Couldn't find {UNIVERSITIES_CSV}. Keep it next to scraper.py.")
            return 1
        if args.nation:
            want = norm(args.nation)
            universities = [u for u in universities if norm(u.get("nation")).startswith(want)]
            if not universities:
                print("No universities matched that nation. Use England, Scotland, Wales or Northern Ireland.")
                return 1
        print(f"Working through {len(universities)} UK universities. This takes hours.")
        print("Press Ctrl+C at any point; progress is saved after each university.\n")
        summary = run_all(universities, max_pages=args.max_pages, delay=args.delay, redo=args.redo,
                          only_with_contacts=args.only_with_contacts, deep=args.deep,
                          progress=lambda d, t, m: print(f"\r  {m}".ljust(78), end="", flush=True),
                          log=lambda m: print(f"\n  {m}"))
        print(f"\n\n{summary['done']} of {summary['total']} universities done, "
              f"{summary['societies']} societies collected.")
        print(f"  Master list: {MASTER_CSV}")
        print(f"  Progress: {COVERAGE_CSV}")
        return 0

    if args.file:
        jobs = _read_jobs(args.file)
    elif args.name:
        jobs = [(args.name, args.url)]
    else:
        parser.error('Give a name, e.g. python scraper.py "University of Leeds", '
                     'or use --all for every UK university.')

    for name, url in jobs:
        print(f"\n=== {name} ===")
        _run_and_report(name, url=url, company=args.company, max_pages=args.max_pages,
                        delay=args.delay, only_with_contacts=args.only_with_contacts,
                        deep=args.deep)
    return 0


if __name__ == "__main__":
    if _running_interactively():
        print('SocietyScout is ready. Try:  find_societies("University of Leeds")')
    else:
        sys.exit(main())
