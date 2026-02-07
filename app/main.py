import csv
import io
import os
import re
import sqlite3
import time
from datetime import datetime, date
from pathlib import Path
from random import uniform
from typing import Dict, List, Optional, Tuple

import pdfplumber
import requests
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"
DB_PATH = DATA_DIR / "app.db"

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
ROLE_PREFIXES = {"info", "office", "admin", "support", "hello", "contact", "sales", "team"}

DEFAULT_SIGNATURE = "Genesis Executive Home Services"
DEFAULT_FROM_NAME = "Genesis Executive Home Services"
DEFAULT_REPLY_TO = "ops@genesisehs.com"

DEFAULT_TEMPLATES = [
    {
        "name": "Commercial Outreach",
        "subject": "Facilities maintenance support for {{company_name}}",
        "body": (
            "Hi {{contact_name}},\n\n"
            "I lead Facilities Maintenance & Building Services with Genesis Executive Home Services. "
            "We support commercial properties across Cobb, Paulding, Douglas, Barrow, Fulton, and Bartow. "
            "We carry $1M per occurrence / $2M aggregate GL coverage. "
            "Would you share your vendor onboarding process or the right contact to start?\n\n"
            "Thanks for your time,\n"
            "Genesis Executive Home Services"
        ),
    },
    {
        "name": "HOA & Residential Outreach",
        "subject": "Vendor onboarding for {{company_name}}",
        "body": (
            "Hi {{contact_name}},\n\n"
            "Genesis Executive Home Services provides Facilities Maintenance & Building Services for "
            "HOA and residential communities in Cobb, Paulding, Douglas, Barrow, Fulton, and Bartow. "
            "We carry $1M per occurrence / $2M aggregate GL coverage. "
            "Could you point me to your vendor onboarding process or the right person to connect with?\n\n"
            "Appreciate your guidance,\n"
            "Genesis Executive Home Services"
        ),
    },
]

UNSUBSCRIBE_LINE = "If you’d like me to stop emailing you, reply ‘unsubscribe’ and I’ll remove you."

app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    cursor = conn.cursor()
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT,
            counties_served TEXT,
            contact_name TEXT,
            title TEXT,
            email TEXT,
            phone TEXT,
            source_url TEXT,
            status TEXT DEFAULT 'Not Sent',
            skip_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_company_contact ON contacts(company_name, contact_name);

        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS campaign_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            contact_id INTEGER NOT NULL,
            status TEXT DEFAULT 'Not Sent',
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
            FOREIGN KEY(contact_id) REFERENCES contacts(id)
        );

        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS suppression_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS email_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            contact_id INTEGER NOT NULL,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            subject TEXT,
            template_version INTEGER,
            provider_message_id TEXT,
            status TEXT,
            error_message TEXT,
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
            FOREIGN KEY(contact_id) REFERENCES contacts(id)
        );
        """
    )
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM templates")
    if cursor.fetchone()[0] == 0:
        for template in DEFAULT_TEMPLATES:
            cursor.execute(
                "INSERT INTO templates (name, subject, body, version) VALUES (?, ?, ?, 1)",
                (template["name"], template["subject"], template["body"]),
            )
        conn.commit()
    conn.close()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    has_email: Optional[str] = None,
    county: Optional[str] = None,
    company: Optional[str] = None,
    status: Optional[str] = None,
) -> HTMLResponse:
    conn = get_db()
    query = "SELECT * FROM contacts"
    filters = []
    params: List[str] = []

    if has_email == "1":
        filters.append("email IS NOT NULL AND email != ''")
    elif has_email == "0":
        filters.append("(email IS NULL OR email = '')")

    if county:
        filters.append("counties_served LIKE ?")
        params.append(f"%{county}%")

    if company:
        filters.append("company_name LIKE ?")
        params.append(f"%{company}%")

    if status:
        filters.append("status = ?")
        params.append(status)

    if filters:
        query += " WHERE " + " AND ".join(filters)

    query += " ORDER BY created_at DESC"
    contacts = conn.execute(query, params).fetchall()
    counties = conn.execute("SELECT DISTINCT counties_served FROM contacts").fetchall()
    status_values = ["Not Sent", "Queued", "Sent", "Failed", "Skipped"]
    campaigns = conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
    conn.close()

    county_options = sorted(
        {county for row in counties for county in row["counties_served"].split(",") if row["counties_served"]}
    )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "contacts": contacts,
            "county_options": [c.strip() for c in county_options if c.strip()],
            "status_values": status_values,
            "campaigns": campaigns,
        },
    )


@app.post("/upload")
def upload_contacts(file: UploadFile = File(...)) -> RedirectResponse:
    data = file.file.read()
    filename = file.filename or ""
    contacts = []

    if filename.lower().endswith(".pdf"):
        contacts = parse_pdf(data)
    else:
        contacts = parse_csv(data)

    normalized = normalize_contacts(contacts)
    store_contacts(normalized)

    return RedirectResponse(url="/", status_code=303)


@app.post("/campaigns")
def create_campaign(
    name: str = Form(...),
    notes: str = Form(""),
    contact_ids: Optional[List[int]] = Form(None),
) -> RedirectResponse:
    if not contact_ids:
        return RedirectResponse(url="/", status_code=303)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO campaigns (name, notes) VALUES (?, ?)", (name, notes))
    campaign_id = cursor.lastrowid
    for contact_id in contact_ids:
        cursor.execute(
            "INSERT INTO campaign_contacts (campaign_id, contact_id, status) VALUES (?, ?, 'Not Sent')",
            (campaign_id, contact_id),
        )
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)

def campaign_detail(request: Request, campaign_id: int) -> HTMLResponse:
    conn = get_db()
    campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not campaign:
        conn.close()
        return RedirectResponse(url="/", status_code=303)

    contacts = conn.execute(
        """
        SELECT c.*, cc.status as campaign_status
        FROM campaign_contacts cc
        JOIN contacts c ON c.id = cc.contact_id
        WHERE cc.campaign_id = ?
        ORDER BY c.company_name
        """,
        (campaign_id,),
    ).fetchall()

    templates_rows = conn.execute("SELECT * FROM templates ORDER BY created_at DESC").fetchall()
    logs = conn.execute(
        "SELECT * FROM email_logs WHERE campaign_id = ? ORDER BY timestamp DESC LIMIT 50",
        (campaign_id,),
    ).fetchall()
    conn.close()

    return templates.TemplateResponse(
        "campaign.html",
        {
            "request": request,
            "campaign": campaign,
            "contacts": contacts,
            "templates": templates_rows,
            "logs": logs,
            "unsubscribe_line": UNSUBSCRIBE_LINE,
            "default_from": DEFAULT_FROM_NAME,
            "default_reply_to": DEFAULT_REPLY_TO,
            "default_signature": DEFAULT_SIGNATURE,
        },
    )


@app.post("/campaigns/{campaign_id}/send")
def send_campaign(
    campaign_id: int,
    template_id: int = Form(...),
    from_name: str = Form(DEFAULT_FROM_NAME),
    reply_to: str = Form(DEFAULT_REPLY_TO),
    signature: str = Form(DEFAULT_SIGNATURE),
    test_mode: Optional[str] = Form(None),
    test_email: Optional[str] = Form(None),
    max_per_minute: int = Form(10),
    daily_cap: int = Form(150),
    allow_role_inboxes: Optional[str] = Form(None),
) -> RedirectResponse:
    conn = get_db()
    template = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
    if not template:
        conn.close()
        return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)

    contacts = conn.execute(
        """
        SELECT c.*, cc.id as campaign_contact_id, cc.status as campaign_status
        FROM campaign_contacts cc
        JOIN contacts c ON c.id = cc.contact_id
        WHERE cc.campaign_id = ? AND cc.status IN ('Not Sent', 'Queued')
        ORDER BY c.company_name
        """,
        (campaign_id,),
    ).fetchall()

    conn.execute(
        "UPDATE campaign_contacts SET status = 'Queued' WHERE campaign_id = ? AND status = 'Not Sent'",
        (campaign_id,),
    )
    conn.commit()

    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    if not sendgrid_key:
        conn.close()
        return RedirectResponse(url=f"/campaigns/{campaign_id}?error=missing_sendgrid", status_code=303)

    sent_today = conn.execute(
        "SELECT COUNT(*) FROM email_logs WHERE date(timestamp) = ? AND status = 'Sent'",
        (date.today().isoformat(),),
    ).fetchone()[0]

    per_email_delay = 60 / max_per_minute if max_per_minute > 0 else 6
    allow_role_inboxes_flag = allow_role_inboxes == "on"
    is_test = test_mode == "on"

    for contact in contacts:
        if sent_today >= daily_cap:
            conn.execute(
                "INSERT INTO email_logs (campaign_id, contact_id, status, error_message) VALUES (?, ?, 'Failed', ?)",
                (campaign_id, contact["id"], "Daily cap reached"),
            )
            conn.execute(
                "UPDATE campaign_contacts SET status = 'Failed' WHERE id = ?",
                (contact["campaign_contact_id"],),
            )
            conn.commit()
            break

        evaluation = evaluate_contact(contact, allow_role_inboxes_flag, conn)
        if evaluation["status"] == "Skipped":
            mark_skipped(conn, campaign_id, contact, evaluation["reason"])
            continue

        recipient_email = test_email if is_test else contact["email"]
        if is_test and not test_email:
            mark_skipped(conn, campaign_id, contact, "Test mode enabled without test email")
            continue

        subject = render_template(template["subject"], contact)
        body = render_template(template["body"], contact)
        message = f"{body}\n\n{signature}\n\n{UNSUBSCRIBE_LINE}"

        success, provider_id, error_message, hard_fail = send_with_retries(
            sendgrid_key,
            recipient_email,
            subject,
            message,
            from_name,
            reply_to,
        )

        status = "Sent" if success else "Failed"
        if is_test:
            status = "Test"

        conn.execute(
            """
            INSERT INTO email_logs (campaign_id, contact_id, subject, template_version, provider_message_id, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                contact["id"],
                subject,
                template["version"],
                provider_id,
                status,
                error_message,
            ),
        )
        conn.execute(
            "UPDATE campaign_contacts SET status = ? WHERE id = ?",
            (status if status != "Test" else "Sent", contact["campaign_contact_id"]),
        )
        conn.commit()

        if hard_fail:
            break

        sent_today += 1
        time.sleep(per_email_delay)
        time.sleep(uniform(2, 6))

    conn.close()
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@app.post("/suppression")
def add_suppression(email: str = Form(...), reason: str = Form("")) -> RedirectResponse:
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO suppression_list (email, reason) VALUES (?, ?)",
        (email.strip().lower(), reason),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/suppression", status_code=303)


@app.post("/suppression/remove")
def remove_suppression(suppression_id: int = Form(...)) -> RedirectResponse:
    conn = get_db()
    conn.execute("DELETE FROM suppression_list WHERE id = ?", (suppression_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/suppression", status_code=303)


@app.get("/suppression", response_class=HTMLResponse)

def suppression_list(request: Request) -> HTMLResponse:
    conn = get_db()
    suppressed = conn.execute("SELECT * FROM suppression_list ORDER BY created_at DESC").fetchall()
    conn.close()
    return templates.TemplateResponse(
        "suppression.html", {"request": request, "suppressed": suppressed}
    )


@app.get("/campaigns/{campaign_id}/export")

def export_campaign(campaign_id: int) -> StreamingResponse:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT c.company_name, c.contact_name, c.email, cc.status, e.status as email_status, e.error_message
        FROM campaign_contacts cc
        JOIN contacts c ON c.id = cc.contact_id
        LEFT JOIN email_logs e ON e.contact_id = c.id AND e.campaign_id = cc.campaign_id
        WHERE cc.campaign_id = ?
        ORDER BY c.company_name
        """,
        (campaign_id,),
    ).fetchall()
    conn.close()

    def generate() -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Company", "Contact", "Email", "Campaign Status", "Email Status", "Error"])
        for row in rows:
            writer.writerow(row)
        return output.getvalue().encode("utf-8")

    return StreamingResponse(
        io.BytesIO(generate()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=campaign_{campaign_id}_results.csv"},
    )


@app.get("/preview")

def preview_email(template_id: int, contact_id: int) -> Dict[str, str]:
    conn = get_db()
    template = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    conn.close()
    if not template or not contact:
        return {"subject": "", "body": ""}
    subject = render_template(template["subject"], contact)
    body = render_template(template["body"], contact)
    return {"subject": subject, "body": body}


@app.post("/templates")

def create_template(
    name: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
) -> RedirectResponse:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(version) FROM templates WHERE name = ?", (name,))
    latest_version = cursor.fetchone()[0] or 0
    cursor.execute(
        "INSERT INTO templates (name, subject, body, version) VALUES (?, ?, ?, ?)",
        (name, subject, body, latest_version + 1),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)


def parse_csv(data: bytes) -> List[Dict[str, str]]:
    decoded = data.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(decoded))
    rows = []
    for row in reader:
        rows.append({k.strip(): (v or "").strip() for k, v in row.items()})
    return rows


def parse_pdf(data: bytes) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                if "|" in line:
                    parts = [segment.strip() for segment in line.split("|")]
                else:
                    parts = [segment.strip() for segment in re.split(r"\s{2,}", line)]
                if len(parts) < 3:
                    continue
                row = map_parts_to_contact(parts)
                if row:
                    rows.append(row)
    return rows


def map_parts_to_contact(parts: List[str]) -> Optional[Dict[str, str]]:
    headers = [
        "Company Name",
        "Counties Served",
        "Contact Name",
        "Title",
        "Email",
        "Phone",
        "Source URL",
    ]
    if len(parts) >= len(headers):
        return dict(zip(headers, parts[: len(headers)]))
    return None


def normalize_contacts(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    normalized = []
    for row in rows:
        normalized.append(
            {
                "company_name": row.get("Company Name", row.get("company_name", "")).strip(),
                "counties_served": row.get("Counties Served", row.get("counties_served", "")).strip(),
                "contact_name": row.get("Contact Name", row.get("contact_name", "")).strip(),
                "title": row.get("Title", row.get("title", "")).strip(),
                "email": row.get("Email", row.get("email", "")).strip().lower(),
                "phone": row.get("Phone", row.get("phone", "")).strip(),
                "source_url": row.get("Source URL", row.get("source_url", "")).strip(),
            }
        )
    return normalized


def store_contacts(contacts: List[Dict[str, str]]) -> None:
    conn = get_db()
    cursor = conn.cursor()
    for contact in contacts:
        email = contact["email"]
        if email and not EMAIL_REGEX.match(email):
            contact["status"] = "Skipped"
            contact["skip_reason"] = "Invalid email"
        else:
            contact["status"] = "Not Sent"
            contact["skip_reason"] = None

        unique_by_email = bool(email)
        try:
            if unique_by_email:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO contacts
                    (company_name, counties_served, contact_name, title, email, phone, source_url, status, skip_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contact["company_name"],
                        contact["counties_served"],
                        contact["contact_name"],
                        contact["title"],
                        email,
                        contact["phone"],
                        contact["source_url"],
                        contact["status"],
                        contact["skip_reason"],
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO contacts
                    (company_name, counties_served, contact_name, title, email, phone, source_url, status, skip_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contact["company_name"],
                        contact["counties_served"],
                        contact["contact_name"],
                        contact["title"],
                        contact["email"],
                        contact["phone"],
                        contact["source_url"],
                        contact["status"],
                        contact["skip_reason"],
                    ),
                )
        except sqlite3.IntegrityError:
            continue

    conn.commit()
    conn.close()


def evaluate_contact(contact: sqlite3.Row, allow_role_inboxes: bool, conn: sqlite3.Connection) -> Dict[str, str]:
    if not contact["email"]:
        return {"status": "Skipped", "reason": "Missing email"}
    if not EMAIL_REGEX.match(contact["email"]):
        return {"status": "Skipped", "reason": "Invalid email"}
    if not allow_role_inboxes:
        prefix = contact["email"].split("@")[0].lower()
        if prefix in ROLE_PREFIXES:
            return {"status": "Skipped", "reason": "Role inbox blocked"}
    suppression = conn.execute(
        "SELECT 1 FROM suppression_list WHERE email = ?", (contact["email"],)
    ).fetchone()
    if suppression:
        return {"status": "Skipped", "reason": "Suppressed"}
    return {"status": "Ready"}


def mark_skipped(conn: sqlite3.Connection, campaign_id: int, contact: sqlite3.Row, reason: str) -> None:
    conn.execute(
        "INSERT INTO email_logs (campaign_id, contact_id, status, error_message) VALUES (?, ?, 'Skipped', ?)",
        (campaign_id, contact["id"], reason),
    )
    conn.execute(
        "UPDATE campaign_contacts SET status = 'Skipped' WHERE id = ?",
        (contact["campaign_contact_id"],),
    )
    conn.commit()


def render_template(template: str, contact: sqlite3.Row) -> str:
    replacements = {
        "{{contact_name}}": contact["contact_name"] or "there",
        "{{company_name}}": contact["company_name"] or "your team",
        "{{counties_served}}": contact["counties_served"] or "your service area",
        "{{title}}": contact["title"] or "",
    }
    result = template
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result


def send_with_retries(
    api_key: str,
    to_email: str,
    subject: str,
    body: str,
    from_name: str,
    reply_to: str,
    max_attempts: int = 3,
) -> Tuple[bool, Optional[str], Optional[str], bool]:
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        response = send_email(api_key, to_email, subject, body, from_name, reply_to)
        if response["success"]:
            return True, response.get("message_id"), None, False
        status = response.get("status")
        error = response.get("error")
        if status in {401, 403}:
            return False, None, error, True
        if status == 429 or (status and status >= 500):
            time.sleep(2 ** attempt)
            continue
        return False, None, error, False
    return False, None, "Max retries exceeded", False


def send_email(
    api_key: str,
    to_email: str,
    subject: str,
    body: str,
    from_name: str,
    reply_to: str,
) -> Dict[str, Optional[str]]:
    payload = {
        "personalizations": [{"to": [{"email": to_email}], "subject": subject}],
        "from": {"email": reply_to, "name": from_name},
        "reply_to": {"email": reply_to},
        "content": [{"type": "text/plain", "value": body}],
    }
    response = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    if response.status_code in {200, 202}:
        return {"success": True, "message_id": response.headers.get("X-Message-Id")}
    return {
        "success": False,
        "status": response.status_code,
        "error": response.text,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
