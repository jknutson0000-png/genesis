# Genesis Bulk Outreach Email App

Internal web app for importing property management contact lists and sending controlled outreach campaigns with compliance controls.

## Features

- CSV and PDF import with normalization + deduping.
- Email validation, skip rules, and suppression list.
- Filters (email present, county, company search, status).
- Campaign creation with template preview and safe sending controls.
- SendGrid integration with test mode, throttling, retries, and jitter.
- Audit logging + CSV export for sent/failed/skipped.

## Tech Stack

- FastAPI + Jinja2
- SQLite
- SendGrid API

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment variables

```bash
export SENDGRID_API_KEY="your_sendgrid_key"
```

### Run locally

```bash
python app/main.py
```

Open [http://localhost:8000](http://localhost:8000).

## Importing Contacts

1. Prepare a CSV or PDF with columns:
   - Company Name | Counties Served | Contact Name | Title | Email | Phone | Source URL
2. Use the **Import Contact List** form and upload the file.
3. The app dedupes by email (or company + contact name when email is missing).

Sample file: [`sample_contacts.csv`](sample_contacts.csv)

## Building a Campaign

1. Filter contacts as needed.
2. Select contacts and create a campaign name + notes.
3. Open the campaign to choose a template and send settings.

## Email Templates

Default templates are seeded for Commercial and HOA/Residential outreach. You can save new versions in the **Template Builder**.

Available variables:

- `{{contact_name}}`
- `{{company_name}}`
- `{{counties_served}}`
- `{{title}}`

## Test Mode

Enable **Test mode** in a campaign and set a test email. The app will send only to the test address while still logging results.

## Safety Controls

- Suppression list prevents sending to opt-out contacts.
- Optional role-inbox blocking (info@, admin@, etc.).
- Rate limiting (max per minute) + daily cap.
- Random jitter between sends.
- Retries for transient errors (429/5xx). Hard fail on auth errors.

## PDF Import Notes

PDF parsing uses text extraction and expects rows separated by `|` or multiple spaces. If parsing fails, export the source list to CSV for best results.

## Database

SQLite file stored at `data/app.db`. Delete it to reset the database.

## Production

For production deployment, set the SendGrid key in your environment, run behind a reverse proxy, and schedule send jobs as needed.
