"""Crawl each company's website (home + careers/contact pages) and collect HR/careers email addresses.

Usage: python3 scrape_emails.py [companies.csv] [contacts.csv] [--refresh]
Only companies missing from contacts.csv are scraped; --refresh re-scrapes all of them.
"""
import csv
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}")  # bounded: unbounded runs are very slow on big pages
LINK_KEYWORDS = ("career", "job", "hiring", "join", "work-with", "openings", "vacanc", "contact", "about")
HR_KEYWORDS = ("hr", "career", "job", "recruit", "talent", "hiring", "people", "resume", "cv",
               "karriere", "bewerb", "werk", "work", "vacature", "sollicit")  # German / Dutch too
# keyword must start the address or a ._- separated part, so "shruti@" doesn't count as "hr"
HR_RE = re.compile(r"(^|[._-])(" + "|".join(HR_KEYWORDS) + ")")
JUNK_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
JUNK_DOMAINS = ("sentry", "wixpress", "example.com", "domain.com", "email.com", "yourcompany")
PLACEHOLDER_RE = re.compile(r"(your\.?)?name|first\.?name.*|you|someone|user")  # name@company.com etc.
MAX_PAGES = 15
COMMON_PATHS = ("/careers", "/career", "/careers/", "/jobs", "/contact", "/contact-us", "/contact-us/")


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.ok and "text/html" in r.headers.get("Content-Type", ""):
            return r.text
    except requests.RequestException:
        pass
    return ""


def decode_cfemail(hexstr):
    # Cloudflare "email protection" obfuscation
    key = int(hexstr[:2], 16)
    return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))


def same_org(email, website):
    # keep only addresses on the company's own domain (e.g. acme.in for acme.com)
    site = urlparse(website).netloc.replace("www.", "").split(".")[0]
    return site in email.split("@")[1]


def extract_emails(html):
    soup = BeautifulSoup(html, "html.parser")
    found = set(EMAIL_RE.findall(html))
    for a in soup.select("a[href^=mailto]"):
        found.add(unquote(a["href"][7:].split("?")[0]))
    for el in soup.select("[data-cfemail]"):
        found.add(decode_cfemail(el["data-cfemail"]))
    clean = set()
    for e in found:
        e = unquote(e).strip().strip(".").lower()
        e = re.sub(r"^(u00[0-9a-f]{2})+", "", e)  # leftover JSON escapes, e.g. "\u003ehello@x.com"
        if not EMAIL_RE.fullmatch(e):
            continue
        if e.endswith(JUNK_SUFFIXES) or any(j in e for j in JUNK_DOMAINS) or re.fullmatch(r"[0-9a-f]{24,}", e.split("@")[0]) \
                or PLACEHOLDER_RE.fullmatch(e.split("@")[0]):
            continue
        clean.add(e)
    return clean, soup


def candidate_links(soup, base):
    host = urlparse(base).netloc.replace("www.", "")
    links = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base, a["href"]).split("#")[0]
        text = (a.get_text() + " " + url).lower()
        if host in urlparse(url).netloc and any(k in text for k in LINK_KEYWORDS):
            links.append(url)
    # careers pages first
    return sorted(set(links), key=lambda u: not any(k in u.lower() for k in ("career", "job", "hiring", "join")))


def score(email):
    local = email.split("@")[0]
    return 0 if HR_RE.search(local) else 1


def scrape(company, website):
    html = fetch(website)
    if not html:
        return {"company": company, "website": website, "hr_email": "", "all_emails": "", "pages": "", "status": "unreachable"}
    emails, soup = extract_emails(html)
    pages = [website]
    guesses = [website.rstrip("/") + p for p in COMMON_PATHS]
    links = list(dict.fromkeys(candidate_links(soup, website)[:MAX_PAGES] + guesses))
    for url in links:
        page = fetch(url)
        if page:
            pages.append(url)
            emails |= extract_emails(page)[0]
    ranked = sorted((e for e in emails if same_org(e, website)), key=lambda e: (score(e), e))
    hr = [e for e in ranked if score(e) == 0]
    return {
        "company": company,
        "website": website,
        "hr_email": hr[0] if hr else (ranked[0] if ranked else ""),
        "all_emails": ";".join(ranked),
        "pages": " ".join(p for p in pages if any(k in p.lower() for k in ("career", "job", "hiring", "join"))),
        "status": "hr" if hr else ("generic" if ranked else "none_found"),
    }


def main():
    refresh = "--refresh" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--refresh"]
    src = args[0] if args else "companies.csv"
    dst = args[1] if len(args) > 1 else "contacts.csv"
    with open(src) as f:
        rows = list(csv.DictReader(f))
    # reuse earlier results; only scrape companies not yet in contacts.csv (unless --refresh)
    done = {}
    if not refresh:
        try:
            with open(dst) as f:
                done = {r["company"]: r for r in csv.DictReader(f)}
        except FileNotFoundError:
            pass
    todo = [r for r in rows if r["company"] not in done]
    print(f"Scraping {len(todo)} companies ({len(rows) - len(todo)} already done)...", flush=True)

    def work(r):
        res = scrape(r["company"], r["website"])
        print(f"  {res['status']:<11} {res['company']:<24} {res['hr_email']}", flush=True)
        return res

    with ThreadPoolExecutor(max_workers=12) as pool:
        scraped = {r["company"]: r for r in pool.map(work, todo)}
    results = [done.get(r["company"]) or scraped[r["company"]] for r in rows]
    # hand-verified addresses for sites the scraper can't read (JS-rendered / bot-blocked)
    try:
        with open("manual_emails.csv") as f:
            manual = {r["company"]: r["hr_email"] for r in csv.DictReader(f)}
    except FileNotFoundError:
        manual = {}
    for r in results:
        if r["company"] in manual:
            r.update(hr_email=manual[r["company"]], status="hr")
    # write to a temp file first so a crash never leaves contacts.csv half-written
    tmp = dst + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "website", "hr_email", "all_emails", "pages", "status"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    os.replace(tmp, dst)
    print(f"\nSaved {len(results)} rows to {dst}")


if __name__ == "__main__":
    main()
