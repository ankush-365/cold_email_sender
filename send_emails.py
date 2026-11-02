"""Send personalised emails to the contacts in contacts.csv via Gmail SMTP.

Dry run (prints emails, sends nothing):   python3 send_emails.py
Send for real:                            python3 send_emails.py --send
Second round to info@/contact@/hello@ addresses (uses template_generic.txt): add --generic
Test one email to yourself:               python3 send_emails.py --send --test-to you@example.com
Cities come from schedule.txt (today's weekday); override with --day Tuesday or --city Pune / --city all
Abroad list (companies_abroad.csv / contacts_abroad.csv, filtered by country, template_abroad*.txt): add --abroad
Sent emails get a Gmail label per city/country (Jobs/Pune, Jobs/Germany); label older ones with --label

Settings come from .env (see .env.example). Gmail needs an App Password:
https://myaccount.google.com/apppasswords
"""
import argparse
import csv
import imaplib
import os
import re
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

SENT_LOG = Path("sent_log.csv")
# general inboxes worth writing to; sales@/support@/enquiry@ etc. are never used
GENERIC_OK = ("info", "contact", "contactus", "hello", "namaste")
# departments that should never get a job application (also skipped as a personal-address fallback)
NOT_FOR_JOBS = re.compile(
    r"sales|support|enquir|inquir|legal|dpo|privacy|security|whistle|investor|^ir$|relations|press|media|"
    r"marketing|marcom|business|^biz|admin|account|billing|partner|agenc|success|care|help|noreply|no-reply|"
    r"webmaster|abuse|anonymous|team|manager|engage|connect|reach|enterprise|getstarted|letstalk|^bd$|^dev$|"
    r"servico|corpcom|digital|supertouch|community|report|license|^csr$|^nodes$|^pr$|"
    r"communication|safety|product|sustainab|ventur|^bibl|ouvidoria|coffee|academy|accessib|service|compliance|commercial|"
    r"web|^china$|^germany$|^(your\.?)?name$|"
    r"^(info|contact)[._-]|^(uae|usa|india|anz|japan\w*|north\.america)$")  # info-jp@, contact.poland@ = regional


def generic_address(all_emails):
    emails = [e for e in all_emails.split(";") if e]
    # prefer a named person at the company (could be HR, a founder, a lead) over info@/contact@/hello@
    for e in emails:
        local = e.split("@")[0]
        if local not in GENERIC_OK and not NOT_FOR_JOBS.search(local):
            return e
    for e in emails:
        if e.split("@")[0] in GENERIC_OK:
            return e
    return ""


def load_env(path=".env"):
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def already_sent():
    if not SENT_LOG.exists():
        return set()
    with SENT_LOG.open() as f:
        return {row["email"] for row in csv.DictReader(f)}


def log_sent(company, email):
    new = not SENT_LOG.exists()
    with SENT_LOG.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["company", "email", "sent_at"])
        w.writerow([company, email, time.strftime("%Y-%m-%d %H:%M:%S")])


def location_map():
    """company -> city (India list) or country (abroad list)."""
    where = {}
    for path, col in (("companies.csv", "city"), ("companies_abroad.csv", "country")):
        if Path(path).exists():
            with open(path) as f:
                where.update({r["company"]: r.get(col, "") for r in csv.DictReader(f)})
    return where


def label_sent(pairs):
    """Give each sent email a Gmail label: pairs of (recipient, label). Replies show up in the same thread."""
    if not pairs:
        return
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
        # "All Mail" has a different name in non-English Gmail, so find it by its \All flag
        all_mail = next(re.search(rb'"([^"]+)"$', f).group(1).decode()
                        for f in imap.list()[1] if rb"\All" in f)
        imap.select(f'"{all_mail}"')
        labelled = 0
        for addr, label in pairs:
            ids = imap.search(None, "X-GM-RAW", f'"in:sent to:{addr}"')[1][0].split()
            if ids:
                imap.store(b",".join(ids).decode(), "+X-GM-LABELS", f'("{label}")')
                labelled += 1
        imap.logout()
        print(f"\nLabelled {labelled}/{len(pairs)} sent emails in Gmail")
    except (imaplib.IMAP4.error, OSError, StopIteration) as e:
        print(f"\nCould not label emails in Gmail ({e}); sending was not affected. Retry with --label")


def load_schedule(path="schedule.txt"):
    schedule = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                day, cities = line.split("=", 1)
                schedule[day.strip().lower()] = {c.strip().lower() for c in cities.split(",") if c.strip()}
    return schedule


def pick_cities(args):
    """Return the set of cities to email (lowercase), or None for all."""
    if args.city:
        return None if args.city.lower() == "all" else {c.strip().lower() for c in args.city.split(",")}
    day = (args.day or time.strftime("%A")).lower()
    schedule = load_schedule()
    if day not in schedule:
        raise SystemExit(f"No cities scheduled for {day.title()} in schedule.txt. Use --city or --day.")
    return schedule[day]


def build(company, to_addr, template, fields, resume, country=""):
    subject_line, body = template.split("\n", 1)
    values = {**fields, "company": company, "country": country}
    msg = EmailMessage()
    msg["From"] = f'{fields["name"]} <{os.environ["GMAIL_USER"]}>'
    msg["To"] = to_addr
    msg["Subject"] = subject_line.removeprefix("Subject:").strip().format(**values)
    msg.set_content(body.strip().format(**values))
    if resume:
        msg.add_attachment(resume.read_bytes(), maintype="application", subtype="pdf", filename=resume.name)
    return msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send (default is dry run)")
    ap.add_argument("--generic", action="store_true",
                    help="email companies with no HR address at info@/contact@/hello@, using template_generic.txt")
    ap.add_argument("--test-to", help="send only the first email, to this address instead of HR (not logged)")
    ap.add_argument("--delay", type=int, default=45, help="seconds between emails")
    ap.add_argument("--city", "--country", dest="city",
                    help='comma-separated cities (countries with --abroad), or "all" (overrides schedule.txt)')
    ap.add_argument("--day", help="use this weekday's cities from schedule.txt instead of today's")
    ap.add_argument("--label", action="store_true",
                    help="only label every email in sent_log.csv in Gmail (Jobs/<city or country>); sends nothing")
    ap.add_argument("--abroad", action="store_true",
                    help="use companies_abroad.csv / contacts_abroad.csv (country column) instead of the India list")
    args = ap.parse_args()
    load_env()
    if args.label:
        where = location_map()
        with SENT_LOG.open() as f:
            label_sent([(r["email"], f"Jobs/{where.get(r['company']) or 'Other'}") for r in csv.DictReader(f)])
        return
    cities = pick_cities(args)

    fields = {k: os.environ.get(k.upper(), "") for k in ("name", "phone", "linkedin", "github")}
    resume = Path(os.environ["RESUME_PATH"]) if os.environ.get("RESUME_PATH") else None
    if resume and not resume.exists():
        raise SystemExit(f"Resume not found: {resume}")
    # template.txt, template_generic.txt, template_abroad.txt or template_abroad_generic.txt
    template_path = Path(f"template{'_abroad' if args.abroad else ''}{'_generic' if args.generic else ''}.txt")
    template = template_path.read_text()
    if args.send and not args.test_to and re.search(r"\[[^\]\n]+\]", template):
        raise SystemExit(f"{template_path} still has [placeholders] to fill in. Edit it first.")

    suffix = "_abroad" if args.abroad else ""
    with open(f"companies{suffix}.csv") as f:
        city_of = {r["company"]: r.get("country" if args.abroad else "city", "") for r in csv.DictReader(f)}
    with open(f"contacts{suffix}.csv") as f:
        rows = list(csv.DictReader(f))
    if args.generic:
        targets = [{**r, "hr_email": generic_address(r["all_emails"])} for r in rows if r["status"] == "generic"]
        targets = [r for r in targets if r["hr_email"]]
    else:
        targets = [r for r in rows if r["status"] == "hr" and r["hr_email"]]
    if cities is not None:
        targets = [r for r in targets if city_of.get(r["company"], "").lower() in cities]
    done = already_sent()
    targets = [r for r in targets if r["hr_email"] not in done]
    if args.test_to:
        if not targets:
            raise SystemExit("No matching recipients to base the test email on.")
        targets = [{**targets[0], "hr_email": args.test_to}]
    where = "all cities" if cities is None else ", ".join(sorted(c.title() for c in cities))
    kind = "generic-inbox" if args.generic else "HR"
    print(f"{len(targets)} {kind} emails to {'SEND' if args.send else 'preview (dry run)'} ({where})\n")

    smtp = None
    sent = []
    if args.send:
        smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        smtp.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])

    for i, r in enumerate(targets):
        msg = build(r["company"], r["hr_email"], template, fields, resume,
                    city_of.get(r["company"], "") if args.abroad else "")
        if not args.send:
            print(f"--- To: {msg['To']} | Subject: {msg['Subject']}")
            continue
        try:
            smtp.send_message(msg)
        except smtplib.SMTPException as e:
            print(f"FAIL  {r['company']:<24} {r['hr_email']}  ({e})")
            continue
        if not args.test_to:
            log_sent(r["company"], r["hr_email"])
            sent.append((r["hr_email"], f"Jobs/{city_of.get(r['company']) or 'Other'}"))
        print(f"sent  {r['company']:<24} {r['hr_email']}")
        if i < len(targets) - 1:
            time.sleep(args.delay)

    if smtp:
        smtp.quit()
    if sent:
        time.sleep(5)  # give Gmail a moment to file the last email under Sent
        label_sent(sent)


if __name__ == "__main__":
    main()
