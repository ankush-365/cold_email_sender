# Cold Email Sender

Send personalised job-application emails to companies' HR teams from your own Gmail, with your resume attached.

1. **Scrape** each company's website for careers/HR email addresses.
2. **Send** one personalised email per company, spaced out, never emailing the same address twice.
3. **Label** every sent email in Gmail by city or country (`Jobs/Pune`, `Jobs/Germany`).
4. **Track** replies, auto-replies and bounces in a CSV.

Only Python's standard library is needed for sending; the scraper uses `requests` and `beautifulsoup4`.

## Files

| File | What it is |
|---|---|
| `companies.csv` | Companies to target: `company,website,city` |
| `companies_abroad.csv` | Companies outside your country: `company,website,country` |
| `manual_emails.csv` | Addresses you found by hand; these override the scraper |
| `schedule.txt` | Which cities/countries get emailed on which weekday |
| `template.txt` | The email. First line is the subject |
| `template_generic.txt` | Same plus "please forward to HR", for general inboxes like `info@` |
| `template_abroad.txt`, `template_abroad_generic.txt` | Versions for companies abroad (mention remote work / relocation) |
| `scrape_emails.py` | Finds addresses and writes `contacts.csv` / `contacts_abroad.csv` |
| `send_emails.py` | Sends the emails and labels them in Gmail |
| `check_replies.py` | Checks Gmail for responses and writes `responses.csv` |
| `.env.example` | Settings template. Copy to `.env` |

Created when you run it (and kept out of git by `.gitignore`): `.env`, `contacts*.csv`, `sent_log.csv`, `responses.csv`.

## Setup

1. **Python 3.9+.** On Windows, use `python` instead of `python3` below.

2. **Install the scraper's packages:**
   ```
   pip install -r requirements.txt
   ```

3. **Create a Gmail App Password.** Turn on 2-Step Verification for your Google account, then go to https://myaccount.google.com/apppasswords and create one. Copy the 16 characters. This is not your normal Gmail password, and it can be revoked at any time on the same page.

4. **Create `.env`:**
   ```
   cp .env.example .env
   ```
   Fill in your Gmail address, the app password, your name, phone, LinkedIn, GitHub and the full path to your resume PDF. `.env` is in `.gitignore`. **Never commit it or share it.**

5. **Write your email.** Edit `template.txt` and the other three templates and replace every `[bracketed]` part with your own text. Placeholders in `{braces}` are filled in automatically:

   | Placeholder | Filled with |
   |---|---|
   | `{company}` | Company name from the CSV |
   | `{country}` | Country (abroad templates only) |
   | `{name}`, `{phone}`, `{linkedin}`, `{github}` | Values from `.env` |

   The script refuses to send while a template still contains `[brackets]`.

6. **Add companies** to `companies.csv` (replace the example row):
   ```
   company,website,city
   Acme AI,https://www.acme.ai,Pune
   ```

7. **Set your schedule** in `schedule.txt` (city names must match the `city` column):
   ```
   Monday  = Pune
   Tuesday = Bangalore
   ```

## Usage

### 1. Find addresses
```
python3 scrape_emails.py
```
Each company gets a status in `contacts.csv`:
- `hr`: an HR/careers address was found (`hr@`, `careers@`, `jobs@`, `recruitment@`, and German/Dutch equivalents). These get the main email.
- `generic`: only other addresses. These are emailed only in the `--generic` round.
- `none_found` / `unreachable`: nothing usable. Check the site yourself and add an address to `manual_emails.csv`:
  ```
  Acme AI,careers@acme.ai,acme.ai/careers
  ```

Only new companies are scraped on each run. Add `--refresh` to re-scrape everything.

### 2. Preview and test
```
python3 send_emails.py                                  # preview today's list, sends nothing
python3 send_emails.py --send --test-to you@gmail.com   # send ONE email to yourself
```

### 3. Send
```
python3 send_emails.py --send                 # today's cities from schedule.txt
python3 send_emails.py --send --city Pune     # one city now
python3 send_emails.py --generic --send       # second round: info@/hello@ inboxes, asks them to forward to HR
```
Emails go out 45 seconds apart. Everyone emailed is recorded in `sent_log.csv` and never emailed again, so you can safely re-run after an interruption.

| Flag | Effect |
|---|---|
| `--send` | Actually send (without it: preview only) |
| `--test-to ADDR` | Send one email to `ADDR` only (not logged) |
| `--generic` | Email companies with no HR address, at a named person or `info@`/`contact@`/`hello@`. Never `sales@`, `support@`, `legal@` and similar |
| `--city X` / `--country X` | Only these cities/countries (comma-separated), or `all` |
| `--day D` | Use weekday `D` from `schedule.txt` |
| `--abroad` | Use `companies_abroad.csv` and the abroad templates |
| `--delay N` | Seconds between emails (default 45) |
| `--label` | Only label already-sent emails in Gmail; sends nothing |

### Companies abroad
```
python3 scrape_emails.py companies_abroad.csv contacts_abroad.csv
python3 send_emails.py --abroad --country Germany            # preview
python3 send_emails.py --abroad --send --country Germany
```
Send in the recipients' morning (check the time zone).

### 4. Check responses
```
python3 check_replies.py
```
Reads Gmail (read-only) and updates `responses.csv` with `replied`, `auto_reply`, `bounced` or `no_response` for every email sent. It only counts mail from the addresses you emailed (or their company domain, in the same thread) that arrived after you sent. Add your own comments in the `notes` column; they're kept between runs.

## Good practice

- Keep it under about 100 emails a day. Gmail may pause sending or flag your account if you send too many at once.
- Send on weekday mornings in the recipient's time zone.
- Don't follow up with bounced addresses. Follow up once, about a week later, with those who haven't replied.
- Many large companies only accept applications through their careers portal. Apply there too.
- Respect each site's terms of use when scraping, and only email addresses that companies publish for contact.

## Security

- Your Gmail app password lives only in `.env`, which is git-ignored. If it ever leaks, revoke it at https://myaccount.google.com/apppasswords.
- `contacts*.csv`, `sent_log.csv`, `responses.csv` and PDFs are git-ignored as well, because they contain your personal activity.
