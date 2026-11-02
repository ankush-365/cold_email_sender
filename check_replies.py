"""Check Gmail for responses to the emails in sent_log.csv and track them in responses.csv.

    python3 check_replies.py

Only counts messages tied to an address you emailed: mail from that address (or its +tag variant, e.g.
careers+noreply@), a bounce about it, or a reply in your email's thread from the same company domain.
Gmail is opened read-only: nothing is marked as read or moved. Re-run any time; the "notes" column is kept.
"""
import csv
import email
import imaplib
import os
import re
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from send_emails import SENT_LOG, load_env, location_map

RESPONSES = Path("responses.csv")
FIELDS = ["company", "email", "location", "sent_at", "status", "reply_from", "reply_at", "snippet", "notes"]
# a real reply beats a bounce beats an auto-reply
RANK = {"replied": 3, "bounced": 2, "auto_reply": 1, "no_response": 0}
DAEMON_RE = re.compile(r"mailer-daemon|postmaster", re.I)


def header(msg, key):
    return str(make_header(decode_header(msg.get(key, ""))))


def bare(addr):
    """careers+noreply@x.com -> careers@x.com"""
    local, _, domain = addr.lower().partition("@")
    return local.split("+")[0] + "@" + domain


def snippet(msg):
    best = ""
    # plain text first; fall back to HTML when the plain part is missing or a stub
    for kind in ("text/plain", "text/html"):
        for part in msg.walk():
            if part.get_content_type() != kind:
                continue
            text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
            if kind == "text/html":
                text = re.sub(r"(?s)<(style|script).*?</\1>|<blockquote.*", "", text)  # drop the quoted original
                text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", text)
                text = re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").replace("&amp;", "&")
            text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(">"))
            text = re.split(r"\nOn .{5,150}wrote:", text)[0]  # drop the quoted original
            text = " ".join(text.split())[:250]
            if len(text) > len(best):
                best = text
        if len(best) >= 80:
            break
    return best


def classify(msg, sender):
    if DAEMON_RE.search(sender):
        return "bounced"
    auto = msg.get("Auto-Submitted", "no").lower() != "no" or msg.get("X-Autoreply") or msg.get("X-Autorespond")
    if auto or re.match(r"(automatic reply|auto[- ]?reply|out of office)", header(msg, "Subject"), re.I) \
            or re.search(r"no-?reply", sender):
        return "auto_reply"
    return "replied"


def search(imap, *criteria):
    return imap.search(None, *criteria)[1][0].split()


def check(imap, to_addr, sent_at):
    """Return the best response to the email sent to to_addr at sent_at: (status, from, date, snippet)."""
    domain = to_addr.split("@")[1]
    ids = set()
    for sent_id in search(imap, "X-GM-RAW", f'"in:sent to:{to_addr}"'):
        thread = re.search(rb"X-GM-THRID (\d+)", imap.fetch(sent_id, "(X-GM-THRID)")[1][0]).group(1).decode()
        ids |= set(search(imap, "X-GM-THRID", thread))
    ids |= set(search(imap, "X-GM-RAW", f'"from:{to_addr} -in:sent"'))
    ids |= set(search(imap, "X-GM-RAW", f'"from:(mailer-daemon OR postmaster) {to_addr}"'))

    best = ("no_response", "", "", "")
    for i in sorted(ids, key=int):
        msg = email.message_from_bytes(imap.fetch(i, "(BODY.PEEK[])")[1][0][1])
        sender = parseaddr(header(msg, "From"))[1].lower()
        if sender == os.environ["GMAIL_USER"].lower():
            continue
        # keep only messages from the address we emailed, its company domain, or a bounce
        if not (bare(sender) == bare(to_addr) or sender.endswith("@" + domain) or DAEMON_RE.search(sender)):
            continue
        when = parsedate_to_datetime(msg["Date"]).astimezone().strftime("%Y-%m-%d %H:%M")
        if when < sent_at[:16]:  # older mail from the same address, e.g. an earlier application
            continue
        status = classify(msg, sender)
        if RANK[status] > RANK[best[0]]:
            best = (status, sender, when, snippet(msg))
    return best


def main():
    load_env()
    with SENT_LOG.open() as f:
        sent = list(csv.DictReader(f))
    notes = {}
    if RESPONSES.exists():
        with RESPONSES.open() as f:
            old = {r["email"]: r for r in csv.DictReader(f)}
        notes = {e: r.get("notes", "") for e, r in old.items()}
    else:
        old = {}
    where = location_map()

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
    # "All Mail" has a different name in non-English Gmail, so find it by its \All flag
    all_mail = next(re.search(rb'"([^"]+)"$', f).group(1).decode() for f in imap.list()[1] if rb"\All" in f)
    imap.select(f'"{all_mail}"', readonly=True)

    rows = []
    for r in sent:
        status, sender, when, text = check(imap, r["email"], r["sent_at"])
        rows.append({"company": r["company"], "email": r["email"], "location": where.get(r["company"], ""),
                     "sent_at": r["sent_at"], "status": status, "reply_from": sender, "reply_at": when,
                     "snippet": text, "notes": notes.get(r["email"], "")})
        if status != "no_response" and old.get(r["email"], {}).get("status") != status:
            print(f"NEW  {status:<11} {r['company']:<24} {sender}\n     {text[:160]}\n")
    imap.logout()

    tmp = RESPONSES.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, RESPONSES)

    counts = {s: sum(r["status"] == s for r in rows) for s in RANK}
    print(f"{len(rows)} sent: " + ", ".join(f"{n} {s}" for s, n in counts.items()) + f"  -> {RESPONSES}")


if __name__ == "__main__":
    main()
