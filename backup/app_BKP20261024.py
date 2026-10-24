import io
import smtplib
import ssl
from email.message import EmailMessage
import json
import os
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Marketing Campaign Studio",
    page_icon=":material/campaign:",
    layout="wide",
    initial_sidebar_state="expanded",
)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

EMAIL_TO = os.getenv("EMAIL_TO", "").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()
SEND_EMAIL = os.getenv("SEND_EMAIL", "false").strip().lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_SECURE = os.getenv("SMTP_SECURE", "false").strip().lower() == "true"


def get_sqlserver_connection_string():
    """Use a complete connection string or build one from the existing .env fields."""
    connection_string = os.getenv("SQLSERVER_CONNECTION_STRING", "").strip()
    if connection_string:
        return connection_string

    server = os.getenv("SQL_SERVER", "").strip()
    database = os.getenv("SQL_DATABASE", "").strip()
    driver = os.getenv("SQL_DRIVER", "ODBC Driver 18 for SQL Server").strip()
    auth_mode = os.getenv("SQL_AUTH_MODE", "SQL").strip().upper()
    encrypt = os.getenv("SQL_ENCRYPT", "yes").strip()
    trust_certificate = os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "no").strip()

    if not all([server, database, driver]):
        return ""

    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
        f"Encrypt={encrypt}",
        f"TrustServerCertificate={trust_certificate}",
    ]
    if auth_mode == "WINDOWS":
        parts.append("Trusted_Connection=yes")
    else:
        username = os.getenv("SQL_USERNAME", "").strip()
        password = os.getenv("SQL_PASSWORD", "")
        if not username or not password:
            return ""
        parts.extend([f"UID={username}", f"PWD={password}"])
    return ";".join(parts)


SQLSERVER_CONNECTION_STRING = get_sqlserver_connection_string()

# Elasticsearch is optional: if it is unavailable, existing app features continue to work.
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200").strip().rstrip("/")
ELASTICSEARCH_INDEX = os.getenv("ELASTICSEARCH_INDEX", "marketing-documents-v1").strip()
ELASTICSEARCH_TIMEOUT = float(os.getenv("ELASTICSEARCH_TIMEOUT", "5"))

logging.basicConfig(
    filename="marketing_campaign_studio.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("marketing_campaign_studio")


def log_event(level, message, **details):
    """Write app events to the local log and include useful details without secrets."""
    suffix = f" | details={json.dumps(details, ensure_ascii=False, default=str)}" if details else ""
    getattr(logger, level, logger.info)(message + suffix)


def elasticsearch_request(method, path, **kwargs):
    """Call Elasticsearch REST API and raise a readable error on failures."""
    url = f"{ELASTICSEARCH_URL}/{path.lstrip('/')}"
    response = requests.request(method, url, timeout=ELASTICSEARCH_TIMEOUT, **kwargs)
    if not response.ok:
        excerpt = response.text[:500]
        raise RuntimeError(f"Elasticsearch returned HTTP {response.status_code}: {excerpt}")
    return response


def check_elasticsearch():
    response = elasticsearch_request("GET", "_cluster/health")
    return response.json()


def ensure_elasticsearch_index():
    """Create the configured index if it does not already exist."""
    response = requests.head(
        f"{ELASTICSEARCH_URL}/{ELASTICSEARCH_INDEX}",
        timeout=ELASTICSEARCH_TIMEOUT,
    )
    if response.status_code == 200:
        return False
    if response.status_code != 404:
        raise RuntimeError(
            f"Could not check index '{ELASTICSEARCH_INDEX}': HTTP {response.status_code}"
        )
    mapping = {
        "mappings": {
            "properties": {
                "campaign_id": {"type": "keyword"},
                "document_type": {"type": "keyword"},
                "source_files": {"type": "keyword"},
                "extracted_text": {"type": "text"},
                "created_at": {"type": "date"},
                "status": {"type": "keyword"},
            }
        }
    }
    elasticsearch_request("PUT", ELASTICSEARCH_INDEX, json=mapping)
    return True


def index_campaign_document(source_text, source_files=None):
    """Persist parsed campaign content; callers handle failures without blocking the UI."""
    if not source_text or not source_text.strip():
        raise ValueError("There is no campaign content to save to Elasticsearch.")
    created_index = ensure_elasticsearch_index()
    document = {
        "campaign_id": str(uuid.uuid4()),
        "document_type": "campaign_source",
        "source_files": source_files or [],
        "extracted_text": source_text.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "parsed",
    }
    response = elasticsearch_request(
        "POST", f"{ELASTICSEARCH_INDEX}/_doc?refresh=wait_for", json=document
    )
    result = response.json()
    log_event(
        "info", "Campaign document indexed in Elasticsearch",
        index=ELASTICSEARCH_INDEX, document_id=result.get("_id"),
        created_index=created_index, source_file_count=len(document["source_files"]),
    )
    return result, created_index


def search_campaign_documents(query, size=10):
    """Search previously indexed campaign source documents."""
    body = {
        "size": size,
        "sort": [{"created_at": {"order": "desc", "unmapped_type": "date"}}],
        "query": {
            "multi_match": {
                "query": query,
                "fields": ["extracted_text", "source_files"],
                "type": "best_fields",
            }
        },
    }
    response = elasticsearch_request("POST", f"{ELASTICSEARCH_INDEX}/_search", json=body)
    return response.json().get("hits", {}).get("hits", [])


with st.container(border=True):
    title_col, status_col = st.columns([4, 1], vertical_alignment="center")
    with title_col:
        st.title("Marketing Campaign Studio", anchor=False)
        st.caption(
            "From source materials to reviewed, approval-gated campaign delivery — powered by local Ollama."
        )
    with status_col:
        st.badge("Workspace ready", icon=":material/verified:", color="green")

st.caption("Campaign workflow")
step_cols = st.columns(4)
for column, step, label in zip(
    step_cols,
    ("01", "02", "03", "04"),
    ("Brief", "Creative", "Review", "Delivery"),
):
    with column:
        st.metric(label, step, border=True)

with st.sidebar:
    st.header("Workspace settings", divider="gray")
    st.caption("Local model and service connections")
    ollama_url = st.text_input("Ollama URL", value=OLLAMA_URL)
    ollama_model = st.text_input("Ollama model", value=OLLAMA_MODEL)
    st.caption("Check installed models with `ollama list`.")
    if st.button("Test Ollama connection", icon=":material/network_check:", width="stretch"):
        try:
            r = requests.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=5)
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
            st.success("Ollama is reachable.")
            st.write("Installed models:", ", ".join(models) if models else "No models listed")
        except Exception as e:
            st.error(f"Could not connect to Ollama: {e}")


    st.divider()
    st.subheader("Campaign library")
    st.caption(f"Index: `{ELASTICSEARCH_INDEX}`")
    if st.button("Test Elasticsearch connection", icon=":material/storage:", width="stretch"):
        try:
            health = check_elasticsearch()
            log_event("info", "Elasticsearch connection test succeeded", status=health.get("status"))
            st.success(
                f"Elasticsearch is reachable. Cluster status: {health.get('status', 'unknown')}."
            )
        except Exception as exc:
            log_event("exception", "Elasticsearch connection test failed", error=str(exc))
            st.error(f"Elasticsearch connection failed: {exc}")
            st.info("Campaign upload, Ollama drafting, and SMTP sending remain available; only Elasticsearch features are unavailable.")

def extract_text(uploaded_file):
    name = uploaded_file.name.lower()
    data = uploaded_file.getvalue()
    try:
        if name.endswith(".txt") or name.endswith(".md") or name.endswith(".csv"):
            return data.decode("utf-8", errors="replace")
        if name.endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if name.endswith(".docx"):
            from docx import Document
            doc = Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs)
        if name.endswith(".xlsx") or name.endswith(".xls"):
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
            parts = []
            for sheet_name, df in sheets.items():
                parts.append(f"Sheet: {sheet_name}\n{df.head(100).to_csv(index=False)}")
            return "\n\n".join(parts)
        if name.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return "[Image file uploaded. OCR/image understanding is not configured in this starter prototype.]"
    except Exception as e:
        log_event("exception", "Text extraction failed", file_name=uploaded_file.name, error=str(e))
        return f"[Could not extract text from {uploaded_file.name}: {e}]"
    return f"[Unsupported file type: {uploaded_file.name}]"

def ollama_generate(prompt, base_url, model):
    url = f"{base_url.rstrip('/')}/api/generate"
    response = requests.post(
        url,
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=180,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip()

def send_campaign_email(
    to_address,
    from_address,
    subject,
    body,
    image_bytes=None,
    image_name=None,
    image_mode="none",
):
    """Send one approved email, optionally embedding a campaign image inline."""
    if not SEND_EMAIL:
        raise RuntimeError("Email sending is disabled. Set SEND_EMAIL=true in .env and restart Streamlit.")
    if not all([to_address, from_address, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD]):
        raise RuntimeError(
            "Email settings are incomplete. Set EMAIL_FROM, SMTP_HOST, SMTP_PORT, "
            "SMTP_USER, and SMTP_PASSWORD in .env."
        )

    message = EmailMessage()
    message["To"] = to_address
    message["From"] = from_address
    message["Subject"] = subject.strip() or "(No subject)"

    if image_mode == "inline" and image_bytes:
        # Normalize to PNG so common email clients can display the CID image reliably.
        try:
            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as source_image:
                converted = source_image.convert("RGBA")
                image_buffer = io.BytesIO()
                converted.save(image_buffer, format="PNG")
                inline_image_bytes = image_buffer.getvalue()
        except Exception as exc:
            raise RuntimeError(f"Could not prepare the inline image: {exc}") from exc

        import html

        # Convert the reviewed plain-text body into well-spaced HTML paragraphs.
        # Also remove common Markdown markers if they were pasted into the draft.
        body_text = body.replace("\\r\\n", "\\n").replace("\\r", "\\n").strip()
        body_text = body_text.replace("**", "").replace("__", "")
        paragraphs = [
            paragraph.strip()
            for paragraph in body_text.split("\\n\\n")
            if paragraph.strip()
        ]
        if not paragraphs:
            paragraphs = [body_text] if body_text else []

        html_paragraphs = "".join(
            "<p style='margin:0 0 16px 0;'>"
            + html.escape(paragraph).replace("\\n", "<br>")
            + "</p>"
            for paragraph in paragraphs
        )

        message.set_content(body_text)
        message.add_alternative(
            "<!doctype html>"
            "<html><body style='margin:0;padding:0;background-color:#f3f5f7;'>"
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            "border='0' style='background-color:#f3f5f7;'><tr><td align='center' "
            "style='padding:24px 12px;'>"
            "<table role='presentation' width='600' cellpadding='0' cellspacing='0' "
            "border='0' style='width:100%;max-width:600px;background:#ffffff;"
            "border-collapse:collapse;'><tr><td style='padding:32px 30px 18px 30px;"
            "font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;"
            "color:#242424;'>"
            f"{html_paragraphs}"
            "</td></tr><tr><td style='padding:0 30px 28px 30px;'>"
            "<img src='cid:campaign-image' alt='Campaign creative' width='540' "
            "style='display:block;width:100%;max-width:540px;height:auto;"
            "border:0;outline:none;text-decoration:none;'>"
            "</td></tr></table></td></tr></table>"
            "</body></html>",
            subtype="html",
        )
        html_part = message.get_payload()[-1]
        html_part.add_related(
            inline_image_bytes,
            maintype="image",
            subtype="png",
            cid="<campaign-image>",
            filename="campaign-image.png",
            disposition="inline",
        )
    else:
        message.set_content(body)
        if image_mode == "attachment" and image_bytes:
            filename = image_name or "campaign-image.png"
            suffix = Path(filename).suffix.lower()
            mime_subtype = {
                ".png": "png",
                ".jpg": "jpeg",
                ".jpeg": "jpeg",
                ".webp": "webp",
            }.get(suffix)
            if not mime_subtype:
                raise RuntimeError("Unsupported image type. Please use PNG, JPG, JPEG, or WEBP.")
            message.add_attachment(
                image_bytes,
                maintype="image",
                subtype=mime_subtype,
                filename=filename,
            )

    context = ssl.create_default_context()
    if SMTP_SECURE:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)





def get_sqlserver_connection():
    """Open a SQL Server connection using a connection string stored in .env."""
    if not SQLSERVER_CONNECTION_STRING:
        raise RuntimeError(
            "SQL Server is not configured. Set SQLSERVER_CONNECTION_STRING or the SQL_SERVER, "
            "SQL_DATABASE, SQL_AUTH_MODE, SQL_DRIVER, and authentication fields in .env, then restart Streamlit."
        )
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError(
            "The pyodbc package is missing. Install it with: pip install pyodbc"
        ) from exc
    return pyodbc.connect(SQLSERVER_CONNECTION_STRING, timeout=10)


def ensure_email_tracking_tables():
    """Validate connectivity and the two minimal tables created by the user."""
    # The tables are created separately in SQL Server. This function deliberately
    # does not create a different schema or add columns to the user's tables.
    with get_sqlserver_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT OBJECT_ID('dbo.EmailCampaigns', 'U'), OBJECT_ID('dbo.EmailRecipients', 'U')")
        row = cursor.fetchone()
        if not row or row[0] is None or row[1] is None:
            raise RuntimeError(
                "Required tables dbo.EmailCampaigns and dbo.EmailRecipients were not found "
                "in the configured database."
            )
        conn.commit()


def upsert_campaign_record(campaign_id, campaign_name, subject_template, body_template):
    """Create a campaign row once; the minimal schema stores only its subject/status."""
    with get_sqlserver_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Subject, Status FROM dbo.EmailCampaigns WHERE CampaignID = ?",
            campaign_id,
        )
        existing = cursor.fetchone()
        if existing:
            existing_subject, existing_status = str(existing[0] or ""), str(existing[1] or "")
            if existing_status.upper() in {"SENDING", "SENT", "PARTIAL"}:
                if existing_subject != subject_template:
                    raise RuntimeError(
                        "This campaign ID already exists in a sending/completed state. "
                        "Use a new campaign ID for a genuinely new campaign."
                    )
                conn.commit()
                return
            cursor.execute(
                "UPDATE dbo.EmailCampaigns SET Subject = ?, Status = 'PENDING' WHERE CampaignID = ?",
                subject_template[:500], campaign_id,
            )
        else:
            cursor.execute(
                """INSERT INTO dbo.EmailCampaigns (CampaignID, Subject, Status)
                   VALUES (?, ?, 'PENDING')""",
                campaign_id, subject_template[:500],
            )
        conn.commit()


def queue_recipient(campaign_id, customer_id, email, allow_repeat_send=False):
    """Queue an address once per campaign; repeat delivery needs an explicit test-mode opt-in."""
    with get_sqlserver_connection() as conn:
        cursor = conn.cursor()
        if not allow_repeat_send:
            cursor.execute(
                "SELECT TOP 1 CampaignID FROM dbo.EmailRecipients WHERE LOWER(Email) = LOWER(?) AND Status = 'SENT'",
                email,
            )
            prior_send = cursor.fetchone()
            if prior_send:
                return False, f"already sent in campaign {prior_send[0]}"
        try:
            cursor.execute(
                """INSERT INTO dbo.EmailRecipients (CampaignID, Email, Status)
                   VALUES (?, ?, 'PENDING')""",
                campaign_id, email,
            )
            conn.commit()
            return True, "queued"
        except Exception as exc:
            conn.rollback()
            # A duplicate campaign/email is expected on a repeated queue operation.
            with get_sqlserver_connection() as check_conn:
                check_cursor = check_conn.cursor()
                check_cursor.execute(
                    "SELECT Status FROM dbo.EmailRecipients WHERE CampaignID = ? AND LOWER(Email) = LOWER(?)",
                    campaign_id, email,
                )
                existing = check_cursor.fetchone()
            if existing:
                return False, str(existing[0])
            raise exc


def update_recipient_status(campaign_id, email, status, error_message=None):
    """Persist recipient delivery status using only columns in dbo.EmailRecipients."""
    with get_sqlserver_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE dbo.EmailRecipients
               SET Status = ?,
                   SentAt = CASE WHEN ? = 'SENT' THEN SYSDATETIME() ELSE SentAt END,
                   ErrorMessage = ?
               WHERE CampaignID = ? AND LOWER(Email) = LOWER(?)""",
            status, status, (error_message or "")[:1000] or None, campaign_id, email,
        )
        conn.commit()


def approve_campaign(campaign_id, approved_by):
    """Record approval timestamp before delivery; approver name is retained in app logs."""
    with get_sqlserver_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE dbo.EmailCampaigns
               SET ApprovedAt = SYSDATETIME(), Status = 'SENDING'
               WHERE CampaignID = ? AND Status IN ('PENDING', 'READY', 'DRAFT')""",
            campaign_id,
        )
        changed = cursor.rowcount
        conn.commit()
        if changed != 1:
            raise RuntimeError(
                "Campaign was not in an approvable state. Check its SQL Server status; "
                "use a new campaign ID for a distinct campaign."
            )
        log_event("info", "Campaign approved by human", campaign_id=campaign_id, approved_by=approved_by)


def finish_campaign(campaign_id):
    """Set overall status based on final recipient outcomes."""
    with get_sqlserver_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT
                   SUM(CASE WHEN Status IN ('PENDING', 'SENDING') THEN 1 ELSE 0 END),
                   SUM(CASE WHEN Status = 'FAILED' THEN 1 ELSE 0 END)
               FROM dbo.EmailRecipients WHERE CampaignID = ?""",
            campaign_id,
        )
        result = cursor.fetchone()
        pending = int(result[0] or 0)
        failed = int(result[1] or 0)
        if pending:
            campaign_status = "SENDING"
        elif failed:
            campaign_status = "PARTIAL"
        else:
            campaign_status = "SENT"
        cursor.execute(
            "UPDATE dbo.EmailCampaigns SET Status = ? WHERE CampaignID = ?",
            campaign_status, campaign_id,
        )
        conn.commit()


def get_previously_sent_emails():
    """Return normalized addresses already marked SENT across all campaigns."""
    with get_sqlserver_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT LOWER(Email) FROM dbo.EmailRecipients WHERE Status = 'SENT'")
        return {str(row[0]).strip().lower() for row in cursor.fetchall() if row[0]}


def personalize_template(template, row):
    """Replace only simple {column_name} fields with values from a customer row."""
    import re
    values = {str(k): "" if pd.isna(v) else str(v) for k, v in row.items()}
    return re.sub(
        r"\{([A-Za-z0-9_ -]+)\}",
        lambda match: values.get(match.group(1), values.get(match.group(1).strip(), match.group(0))),
        template
    )


def normalize_customer_file(uploaded_file):
    """Load CSV/XLSX and normalize headers for predictable downstream checks."""
    filename = uploaded_file.name.lower()
    if filename.endswith(".csv"):
        try:
            frame = pd.read_csv(uploaded_file)
        except UnicodeDecodeError:
            uploaded_file.seek(0)
            frame = pd.read_csv(uploaded_file, encoding="latin-1")
    elif filename.endswith(".xlsx"):
        frame = pd.read_excel(uploaded_file)
    else:
        raise ValueError("Please upload a .csv or .xlsx customer file.")
    frame.columns = [
        re.sub(r"[^a-z0-9]+", "_", str(col).strip().lower()).strip("_")
        for col in frame.columns
    ]
    return frame


def find_customer_column(columns, candidates):
    """Return the first recognized normalized column name, if one is present."""
    available = set(columns)
    return next((column for column in candidates if column in available), None)


def prepare_bulk_recipients(customers, allow_repeat_delivery=False):
    """Apply the delivery safeguards without asking users to map routine columns."""
    frame = customers.copy()
    email_column = find_customer_column(
        frame.columns,
        ("email", "email_address", "emailaddress", "customer_email", "contact_email"),
    )
    consent_column = find_customer_column(
        frame.columns,
        ("marketing_consent", "marketing_opt_in", "consent", "opted_in", "subscribed"),
    )
    opt_out_column = find_customer_column(
        frame.columns,
        ("unsubscribed", "opted_out", "opt_out", "do_not_contact"),
    )
    if not email_column:
        raise ValueError(
            "An email column is required. Name it email, email_address, customer_email, or contact_email."
        )
    if not consent_column:
        raise ValueError(
            "A marketing-consent column is required for bulk delivery. Name it marketing_consent, "
            "marketing_opt_in, consent, opted_in, or subscribed."
        )

    frame["_email_normalized"] = frame[email_column].fillna("").astype(str).str.strip().str.lower()
    frame["_email_valid"] = frame["_email_normalized"].map(email_is_valid)
    consent_values = {"yes", "y", "true", "1", "consented", "opted_in", "subscribed"}
    frame["_consent_ok"] = (
        frame[consent_column].fillna("").astype(str).str.strip().str.lower().isin(consent_values)
    )
    if opt_out_column:
        opted_out_values = {"yes", "y", "true", "1", "unsubscribed", "opted_out"}
        frame["_not_unsubscribed"] = ~(
            frame[opt_out_column].fillna("").astype(str).str.strip().str.lower().isin(opted_out_values)
        )
    else:
        frame["_not_unsubscribed"] = True
    frame["_not_duplicate_in_file"] = ~frame["_email_normalized"].duplicated(keep="first")
    frame["_not_previously_sent"] = True
    if SQLSERVER_CONNECTION_STRING and not allow_repeat_delivery:
        previously_sent = get_previously_sent_emails()
        frame["_not_previously_sent"] = ~frame["_email_normalized"].isin(previously_sent)

    frame["_eligible"] = (
        frame["_email_valid"]
        & frame["_consent_ok"]
        & frame["_not_unsubscribed"]
        & frame["_not_duplicate_in_file"]
        & frame["_not_previously_sent"]
    )
    frame["_exclusion_reason"] = ""
    for mask, reason in (
        (~frame["_email_valid"], "Invalid email"),
        (~frame["_consent_ok"], "No/unknown marketing consent"),
        (~frame["_not_unsubscribed"], "Unsubscribed / opted out"),
        (~frame["_not_duplicate_in_file"], "Duplicate email in uploaded file"),
        (~frame["_not_previously_sent"], "Already sent in a previous campaign"),
    ):
        frame.loc[mask, "_exclusion_reason"] = frame.loc[mask, "_exclusion_reason"].apply(
            lambda current: f"{current}; {reason}" if current else reason
        )
    return email_column, frame[frame["_eligible"]].copy(), frame[~frame["_eligible"]].copy()


def email_is_valid(value):
    """Basic syntax check; this does not prove that a mailbox exists."""
    import re
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(value or "").strip()))



tab_upload, tab_creative, tab_email, tab_send = st.tabs([
    ":material/upload_file: Brief", ":material/palette: Creative studio",
    ":material/drafts: Email review", ":material/send: Delivery"
])

with tab_upload:
    st.subheader("Build the campaign brief", anchor=False)
    st.caption("Add source files or paste a brief. We will preserve the source material for the creative and review stages.")
    intake_col, paste_col = st.columns(2, gap="large")
    with intake_col:
        with st.container(border=True):
            st.markdown("##### Upload source files")
            files = st.file_uploader(
                "Campaign files",
                type=["pdf", "docx", "xlsx", "xls", "txt", "md", "csv", "png", "jpg", "jpeg", "webp"],
                accept_multiple_files=True,
                key="campaign_files",
                help="PDF, DOCX, XLSX, TXT, MD, CSV, and reference-image files are supported.",
            )
    with paste_col:
        with st.container(border=True):
            st.markdown("##### Paste campaign content")
            pasted_brief = st.text_area(
                "Campaign brief",
                height=176,
                placeholder="Campaign objective, product/service, messaging, audience, CTA, creative requirements...",
            )
    if files:
        extracted = []
        source_names = []
        extraction_failures = []
        for f in files:
            try:
                txt = extract_text(f)
                if txt.startswith(f"[Could not extract text from {f.name}:"):
                    extraction_failures.append(f"{f.name}: text extraction failed; see application log")
                    continue
                extracted.append(f"### FILE: {f.name}\n{txt}")
                source_names.append(f.name)
                log_event("info", "Campaign file parsed", file_name=f.name, size_bytes=f.size)
            except Exception as exc:
                extraction_failures.append(f"{f.name}: {exc}")
                log_event("exception", "Campaign file parsing failed", file_name=f.name, error=str(exc))
        if extracted:
            st.session_state["source_text"] = "\n\n".join(extracted)
            st.session_state["source_files"] = source_names
            st.success(f"{len(extracted)} of {len(files)} campaign file(s) loaded successfully.")
        if extraction_failures:
            st.error("Some campaign files could not be processed: " + "; ".join(extraction_failures))
        for f in files:
            st.write(f"📄 {f.name} · {f.size / 1024:.1f} KB")
    if pasted_brief.strip():
        st.session_state["source_text"] = pasted_brief.strip()
        st.session_state["source_files"] = st.session_state.get("source_files", [])
    if st.button("Prepare campaign brief", type="primary", icon=":material/arrow_forward:"):
        source = st.session_state.get("source_text", "")
        if not source and not pasted_brief.strip():
            log_event("warning", "Prepare campaign brief failed: no source content")
            st.warning("Upload a file or paste campaign content first.")
        else:
            st.session_state["source_text"] = pasted_brief.strip() or source
            st.success("Campaign content is ready for the next steps.")
            log_event("info", "Campaign brief prepared", characters=len(st.session_state["source_text"]))
            st.text_area("Extracted / provided content", value=st.session_state["source_text"], height=220)
            try:
                with st.spinner("Saving campaign document to Elasticsearch..."):
                    result, created_index = index_campaign_document(
                        st.session_state["source_text"],
                        st.session_state.get("source_files", []),
                    )
                if created_index:
                    st.success(f"Elasticsearch index `{ELASTICSEARCH_INDEX}` was created.")
                st.success(f"Campaign data saved to Elasticsearch. Document ID: {result.get('_id', 'saved')}.")
                log_event("info", "Campaign save to Elasticsearch succeeded", document_id=result.get("_id"))
            except Exception as exc:
                log_event("exception", "Campaign save to Elasticsearch failed", error=str(exc))
                st.error(f"Elasticsearch save failed: {exc}")
                st.warning("Your campaign content is still available in this app session. Other features will continue to work; Elasticsearch persistence can be retried after the service is fixed.")

    st.divider()
    st.markdown("#### Search saved campaign documents")
    search_text = st.text_input("Search Elasticsearch", placeholder="e.g. product promotion, audience, KPI", key="es_campaign_search")
    if st.button("Search saved campaigns", key="search_es_campaigns"):
        if not search_text.strip():
            st.warning("Enter a search phrase first.")
        else:
            try:
                with st.spinner("Searching Elasticsearch..."):
                    hits = search_campaign_documents(search_text.strip())
                if hits:
                    st.success(f"Found {len(hits)} matching campaign document(s).")
                    log_event("info", "Elasticsearch campaign search succeeded", query=search_text, result_count=len(hits))
                    for hit in hits:
                        source = hit.get("_source", {})
                        with st.expander(f"{source.get('created_at', 'Unknown date')} · {', '.join(source.get('source_files', [])) or 'Pasted campaign brief'}"):
                            st.caption(f"Document ID: {hit.get('_id', 'unknown')}")
                            st.text(source.get("extracted_text", ""))
                else:
                    st.info("No matching campaign documents found.")
                    log_event("info", "Elasticsearch campaign search returned no results", query=search_text)
            except Exception as exc:
                log_event("exception", "Elasticsearch campaign search failed", query=search_text, error=str(exc))
                st.error(f"Elasticsearch search failed: {exc}")
                st.info("Check that Elasticsearch is running and the configured index is accessible. Existing drafting and email features are unaffected.")

with tab_creative:
    st.subheader("Creative Studio")
    st.write("Create a campaign image prompt. In Image-to-image mode, upload a reference image and confirm that its preview appears before continuing.")

    mode = st.segmented_control(
        "Generation mode",
        ["Content-to-image", "Image-to-image"],
        default="Content-to-image",
        key="generation_mode",
    )

    reference_image = None
    if mode == "Image-to-image":
        st.markdown("#### 1. Upload a reference image")
        upload_col, preview_col = st.columns([1, 1], gap="large")

        with upload_col:
            reference_image = st.file_uploader(
                "Choose a PNG, JPG, JPEG, or WEBP image",
                type=["png", "jpg", "jpeg", "webp"],
                key="reference_image",
                help="Select an image from your computer. The preview should appear on the right.",
            )

            if reference_image is not None:
                st.success(f"Image uploaded: {reference_image.name}")
                st.caption(
                    f"File size: {reference_image.size / 1024:.1f} KB · "
                    f"Type: {reference_image.type or 'image'}"
                )
            else:
                st.info("Upload an image to see its preview here.")

        with preview_col:
            if reference_image is not None:
                try:
                    from PIL import Image

                    image_bytes = reference_image.getvalue()
                    with Image.open(io.BytesIO(image_bytes)) as opened_image:
                        preview_image = opened_image.copy()
                        image_width, image_height = preview_image.size

                    st.image(
                        preview_image,
                        caption=f"Reference image preview · {image_width} × {image_height}px",
                        width="stretch",
                        alt="Uploaded reference image",
                    )
                except Exception as exc:
                    st.error(f"The file was uploaded but could not be displayed as an image: {exc}")
            else:
                with st.container(border=True, height=220):
                    st.info("Your uploaded reference image will appear here.", icon=":material/image:")
    else:
        st.info("Content-to-image mode creates a prompt from campaign content; no reference image is required.")

    st.divider()
    st.markdown("#### 2. Define the creative direction")
    campaign_type = st.selectbox(
        "Campaign type",
        ["Product promotion", "Seasonal campaign", "Customer retention", "Workshop promotion", "Product education", "Other"],
    )
    visual_style = st.selectbox(
        "Visual direction",
        ["Premium commercial photography", "Clean minimal product ad", "Lifestyle / in-use scene", "Bold promotional banner", "Technical / educational"],
    )
    brand_notes = st.text_input("Brand/product constraints", placeholder="e.g., preserve exact product packaging; no invented claims")
    extra_instructions = st.text_area("Additional creative instructions", placeholder="Preferred setting, colors, composition, aspect ratio, items to avoid...")
    if st.button("Create image prompt with Ollama", type="primary", key="make_prompt"):
        source = st.session_state.get("source_text", "")
        if not source:
            st.warning("First add campaign content in the Campaign inputs tab.")
        else:
            prompt = f"""
You are a marketing creative strategist. Use only the campaign facts in the source material.
Do not invent product claims, prices, offers, dates, or brand facts.
Return these headings:
1. CAMPAIGN SUMMARY
2. KEY MESSAGE
3. TARGET AUDIENCE
4. IMAGE GENERATION PROMPT
5. NEGATIVE PROMPT
6. ITEMS REQUIRING HUMAN REVIEW

Mode: {mode}
Campaign type: {campaign_type}
Visual direction: {visual_style}
Brand/product constraints: {brand_notes or "None provided"}
Additional instructions: {extra_instructions or "None provided"}
Reference image uploaded: {"Yes" if reference_image else "No"}

Source material:
{source[:18000]}
"""
            with st.spinner("Asking Ollama to prepare the creative brief..."):
                try:
                    st.session_state["creative_output"] = ollama_generate(prompt, ollama_url, ollama_model)
                    st.success("Creative prompt prepared.")
                    log_event("info", "Creative prompt generation succeeded", model=ollama_model)
                except Exception as e:
                    log_event("exception", "Creative prompt generation failed", model=ollama_model, error=str(e))
                    st.error(f"Ollama request failed: {e}")
    if st.session_state.get("creative_output"):
        st.markdown(st.session_state["creative_output"])
        st.info("This starter version creates the image prompt only. Connect an image-generation model/service to render the actual image.")
        st.download_button(
            "Download creative brief",
            data=st.session_state["creative_output"],
            file_name="creative_brief.txt",
            mime="text/plain",
        )

with tab_email:
    st.subheader("Draft and send a campaign email")
    st.caption("Email delivery is limited to the single EMAIL_TO address configured in your .env file. You can embed a campaign image inside the email body or attach it separately.")

    email_tone = st.selectbox(
        "Email tone",
        ["Professional and helpful", "Warm and conversational", "Concise and promotional", "Educational"],
    )
    cta = st.text_input("Call to action", placeholder="e.g., Learn more, Contact your workshop, View the offer")
    email_image_choice = st.segmented_control(
        "Creative placement",
        ["Inline image in email", "Image attachment", "No image for this draft"],
        default="Inline image in email",
    )

    email_attachment = None
    if email_image_choice in ("Image attachment", "Inline image in email"):
        email_attachment = st.file_uploader(
            "Attach campaign image (optional)",
            type=["png", "jpg", "jpeg", "webp"],
            key="email_campaign_image",
            help="Upload a campaign creative. Choose inline placement to display it inside the email body, or attachment to include it as a downloadable file.",
        )
        if email_attachment is not None:
            try:
                from PIL import Image
                image_bytes_for_preview = email_attachment.getvalue()
                with Image.open(io.BytesIO(image_bytes_for_preview)) as preview:
                    preview_image = preview.copy()
                    width, height = preview_image.size
                st.image(
                    preview_image,
                    caption=f"Campaign image preview · {email_attachment.name} · {width} × {height}px",
                    width=350,
                )
                st.success(f"Image ready ({email_attachment.size / 1024:.1f} KB).")
            except Exception as exc:
                st.error(f"The selected attachment is not a valid image: {exc}")
                email_attachment = None

    if st.button("Draft email with Ollama", type="primary", key="draft_email"):
        source_text = st.session_state.get("source_text", "")
        creative = st.session_state.get("creative_output", "")
        if not source_text:
            st.warning("Add campaign content first.")
        else:
            prompt = f"""
Write a polished, customer-ready marketing email using only facts explicitly supported by the campaign source.
Do not invent names, prices, discounts, URLs, product benefits, claims, or deadlines.
Do not include drafting instructions, explanations, approval notes, fact-check notes, personalization-field lists, labels such as "Call to action", or markdown formatting such as **bold**.
Do not leave template placeholders such as [Name] or [Number]. If a detail is missing, omit it rather than inventing it.
Integrate a clear call to action naturally into the email body, using only a source-supported action. If no specific CTA is supported, use a neutral CTA such as "Learn more about the campaign."
Return ONLY this exact structure:
Subject: one concise subject line

Body:
A short, ready-to-send email body. Use normal paragraphs. Do not include the subject again or any other sections.
Do not include an image placeholder in the text; the campaign image will be inserted by the application when inline placement is selected.

Tone: {email_tone}
CTA guidance: {cta or "Use a neutral, source-supported call to action."}
Creative placement: {email_image_choice}
Campaign source:
{source_text[:14000]}
Creative brief, if available:
{creative[:7000]}
"""
            with st.spinner("Drafting email with Ollama..."):
                try:
                    st.session_state["email_output"] = ollama_generate(prompt, ollama_url, ollama_model)
                    st.session_state.pop("email_subject", None)
                    st.session_state.pop("email_body", None)
                    st.success("Email draft prepared. Review and edit it before sending.")
                    log_event("info", "Email draft generation succeeded", model=ollama_model)
                except Exception as e:
                    log_event("exception", "Email draft generation failed", model=ollama_model, error=str(e))
                    st.error(f"Ollama request failed: {e}")

    if st.session_state.get("email_output"):
        default_draft = st.session_state["email_output"]
        # Separate the subject from the body and remove common model-generated
        # section labels so internal drafting notes are not sent to customers.
        default_subject = "Marketing campaign"
        default_body = default_draft.strip()
        lines = default_draft.splitlines()
        subject_index = None
        body_start = None

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.lower().startswith("subject:"):
                subject_index = idx
                default_subject = stripped.split(":", 1)[1].strip() or default_subject
            if stripped.lower() in ("body:", "email body:"):
                body_start = idx + 1
                break

        if body_start is not None:
            body_lines = lines[body_start:]
            default_body = "\n".join(body_lines).strip()
        elif subject_index is not None:
            body_lines = lines[subject_index + 1:]
            while body_lines and not body_lines[0].strip():
                body_lines.pop(0)
            default_body = "\n".join(body_lines).strip()

        # Remove internal drafting metadata even if the model places it on the
        # same line as the customer-facing copy.
        import re
        meta_patterns = [
            r"\*{0,2}\s*call to action\s*:\*{0,2}",
            r"\*{0,2}\s*personalization fields needed\s*:\*{0,2}",
            r"\*{0,2}\s*fact[- ]check(?: / approval)? notes?\s*:\*{0,2}",
            r"\*{0,2}\s*approval notes\s*:\*{0,2}",
            r"\*{0,2}\s*creative placement\s*:\*{0,2}",
            r"\*{0,2}\s*campaign source\s*:\*{0,2}",
            r"\*{0,2}\s*creative brief\s*:\*{0,2}",
            r"\*{0,2}\s*tone\s*:\*{0,2}",
        ]
        for pattern in meta_patterns:
            default_body = re.split(pattern, default_body, maxsplit=1, flags=re.IGNORECASE)[0]

        # Remove common placeholders and image-instruction text.
        default_body = re.sub(r"\[(?:Name|Number|Inline Image|Image[^\]]*)\]", "", default_body, flags=re.IGNORECASE)
        default_body = re.sub(r"\*{1,2}", "", default_body)
        default_body = re.sub(r"(?im)^\s*(?:subject|body|email body)\s*:\s*", "", default_body)
        default_body = re.sub(r"(?i)\[inline image:[^\]]*\]", "", default_body)
        default_body = re.sub(r"\n{3,}", "\n\n", default_body).strip()

        st.markdown("#### Review the email")
        email_subject = st.text_input("Email subject", value=st.session_state.get("email_subject", default_subject), key="email_subject")
        email_body = st.text_area(
            "Email body (editable)",
            value=st.session_state.get("email_body", default_body),
            height=300,
            key="email_body",
        )

        if EMAIL_TO:
            st.write(f"**Recipient:** {EMAIL_TO}")
        else:
            st.warning("EMAIL_TO is not configured in .env.")
        if EMAIL_FROM:
            st.write(f"**From:** {EMAIL_FROM}")
        if SEND_EMAIL:
            st.success("SEND_EMAIL=true — sending is enabled in configuration.")
        else:
            st.warning("Sending is currently disabled. Set SEND_EMAIL=true in .env and restart Streamlit.")

        st.warning("This sends a real email to the configured EMAIL_TO address. Review the recipient, subject, and body before sending.")
        confirmed = st.checkbox(
            f"I approve sending this email to {EMAIL_TO or 'the configured recipient'}",
            key="confirm_email_send",
        )
        if st.button("Send email now", type="primary", key="send_email_now"):
            if not confirmed:
                st.error("Please confirm that you approve sending this email.")
            elif not EMAIL_TO or not EMAIL_FROM:
                st.error("Set EMAIL_TO and EMAIL_FROM in .env, then restart Streamlit.")
            elif not SEND_EMAIL:
                st.error("Sending is disabled. Set SEND_EMAIL=true in .env and restart Streamlit.")
            elif not email_subject.strip() or not email_body.strip():
                st.error("Email subject and body cannot be empty.")
            else:
                try:
                    with st.spinner(f"Sending email to {EMAIL_TO}..."):
                        send_campaign_email(
                            EMAIL_TO,
                            EMAIL_FROM,
                            email_subject,
                            email_body,
                            image_bytes=email_attachment.getvalue() if email_attachment is not None else None,
                            image_name=email_attachment.name if email_attachment is not None else None,
                            image_mode=(
                                "inline" if email_image_choice == "Inline image in email"
                                else "attachment" if email_image_choice == "Image attachment"
                                else "none"
                            ),
                        )
                    st.success(f"Email sent successfully to {EMAIL_TO}.")
                    log_event("info", "Campaign email sent successfully", recipient=EMAIL_TO, subject=email_subject)
                except Exception as e:
                    log_event("exception", "Campaign email send failed", recipient=EMAIL_TO, error=str(e))
                    st.error(f"Email sending failed: {e}")
                    st.info("For Gmail, use a Google App Password (with 2-Step Verification enabled), not your normal account password.")

if False:  # Legacy multi-step bulk delivery interface retained temporarily for reference.
    st.subheader("Bulk campaign delivery")
    st.caption(
        "Upload CSV/XLSX customers, review eligibility, save the campaign to SQL Server, "
        "and explicitly approve before sending. Each recipient receives an individual email."
    )

    with st.expander("SQL Server setup", expanded=False):
        st.write(
            "Configure SQLSERVER_CONNECTION_STRING in your .env file. The app uses your existing "
            "dbo.EmailCampaigns and dbo.EmailRecipients tables and does not create or alter them."
        )
        st.code(
            'SQLSERVER_CONNECTION_STRING="DRIVER={ODBC Driver 18 for SQL Server};'
            'SERVER=YOUR_SERVER;DATABASE=YOUR_DATABASE;UID=YOUR_USER;PWD=YOUR_PASSWORD;'
            'Encrypt=yes;TrustServerCertificate=yes"',
            language="text"
        )
        if st.button("Test SQL Server table connection", key="init_sql_tracking"):
            try:
                ensure_email_tracking_tables()
                st.success("SQL Server connection succeeded and tracking tables are ready.")
            except Exception as exc:
                st.error(f"SQL Server setup failed: {exc}")

    customer_file = st.file_uploader(
        "Upload customer list (.csv or .xlsx)",
        type=["csv", "xlsx"],
        key="bulk_customer_file"
    )

    if customer_file is not None:
        try:
            customers = normalize_customer_file(customer_file)
            st.session_state["bulk_customers"] = customers
            st.session_state["bulk_customer_filename"] = customer_file.name
            st.success(f"Loaded {len(customers)} row(s) from {customer_file.name}.")
            st.write("Customer file preview")
            st.dataframe(customers.head(100), width="stretch")

            columns = list(customers.columns)
            st.markdown("#### Map customer columns")
            email_options = ["-- Select --"] + columns
            email_col = st.selectbox("Email column", email_options, key="bulk_email_col")
            customer_id_col = st.selectbox(
                "Customer ID column (optional)",
                ["-- None --"] + columns,
                key="bulk_customer_id_col"
            )
            consent_col = st.selectbox(
                "Marketing consent column",
                ["-- Select --"] + columns,
                key="bulk_consent_col",
                help="Required. Values such as yes/true/1/consented are treated as consent."
            )
            unsubscribe_col = st.selectbox(
                "Unsubscribe / opt-out column (optional)",
                ["-- None --"] + columns,
                key="bulk_unsubscribe_col"
            )
            segment_col = st.selectbox(
                "Customer segment column (optional)",
                ["-- None --"] + columns,
                key="bulk_segment_col"
            )
            target_segment = st.text_input(
                "Required segment value (optional)",
                key="bulk_target_segment",
                placeholder="e.g. Premium — leave blank to include all segments"
            )

            st.markdown("#### Campaign setup")
            campaign_id = st.text_input(
                "Stable campaign ID (must be unique for each distinct campaign)",
                key="bulk_campaign_id",
                placeholder="e.g. Diwali_2026_Premium_v1",
                help="Do not change this ID to retry a campaign. Sent recipients with this ID will be skipped."
            ).strip()
            campaign_name = st.text_input("Campaign name", key="bulk_campaign_name")
            subject_template = st.text_input(
                "Email subject template",
                key="bulk_subject_template",
                placeholder="Hello {first_name}, discover our latest update"
            )
            body_template = st.text_area(
                "Email body template",
                key="bulk_body_template",
                height=220,
                placeholder="Hello {first_name},\n\nWrite your approved campaign email here..."
            )
            st.caption(
                "Personalization uses placeholders matching normalized column names, for example "
                "{first_name} or {customer_id}. Missing columns remain unchanged so they can be spotted in review."
            )
            approved_by = st.text_input("Approver name / team ID", key="bulk_approver")

            if email_col != "-- Select --" and consent_col != "-- Select --":
                frame = customers.copy()
                frame["_email_normalized"] = frame[email_col].fillna("").astype(str).str.strip().str.lower()
                frame["_email_valid"] = frame["_email_normalized"].map(email_is_valid)

                consent_values = {"yes", "y", "true", "1", "consented", "opted_in", "subscribed"}
                frame["_consent_ok"] = frame[consent_col].fillna("").astype(str).str.strip().str.lower().isin(consent_values)

                if unsubscribe_col != "-- None --":
                    opted_out_values = {"yes", "y", "true", "1", "unsubscribed", "opted_out"}
                    frame["_not_unsubscribed"] = ~frame[unsubscribe_col].fillna("").astype(str).str.strip().str.lower().isin(opted_out_values)
                else:
                    frame["_not_unsubscribed"] = True

                if segment_col != "-- None --" and target_segment.strip():
                    frame["_segment_ok"] = frame[segment_col].fillna("").astype(str).str.strip().str.casefold() == target_segment.strip().casefold()
                else:
                    frame["_segment_ok"] = True

                frame["_not_duplicate_in_file"] = ~frame["_email_normalized"].duplicated(keep="first")
                # SQL Server is the source of truth for cross-campaign repeat prevention.
                # If it is configured, exclude every address already marked SENT before review.
                frame["_not_previously_sent"] = True
                if SQLSERVER_CONNECTION_STRING:
                    try:
                        previously_sent = get_previously_sent_emails()
                        frame["_not_previously_sent"] = ~frame["_email_normalized"].isin(previously_sent)
                    except Exception as sql_exc:
                        st.error(f"Could not check previous send history in SQL Server: {sql_exc}")
                        frame["_not_previously_sent"] = False
                frame["_eligible"] = (
                    frame["_email_valid"] & frame["_consent_ok"] &
                    frame["_not_unsubscribed"] & frame["_segment_ok"] &
                    frame["_not_duplicate_in_file"] & frame["_not_previously_sent"]
                )
                frame["_exclusion_reason"] = ""
                for mask, reason in [
                    (~frame["_email_valid"], "Invalid email"),
                    (~frame["_consent_ok"], "No/unknown marketing consent"),
                    (~frame["_not_unsubscribed"], "Unsubscribed / opted out"),
                    (~frame["_segment_ok"], "Outside target segment"),
                    (~frame["_not_duplicate_in_file"], "Duplicate email in uploaded file"),
                    (~frame["_not_previously_sent"], "Already sent in a previous campaign / this campaign"),
                ]:
                    frame.loc[mask, "_exclusion_reason"] = frame.loc[mask, "_exclusion_reason"].apply(
                        lambda current: (current + "; " if current else "") + reason
                    )

                eligible = frame[frame["_eligible"]].copy()
                excluded = frame[~frame["_eligible"]].copy()
                metric_cols = st.columns(4)
                metric_cols[0].metric("Uploaded rows", len(frame))
                metric_cols[1].metric("Eligible", len(eligible))
                metric_cols[2].metric("Excluded", len(excluded))
                metric_cols[3].metric("Duplicate rows", int((~frame["_not_duplicate_in_file"]).sum()))
                with st.expander("Review excluded records"):
                    st.dataframe(excluded, width="stretch")

                if len(eligible) > 0:
                    sample_row = eligible.iloc[0].drop(labels=[
                        c for c in eligible.columns if c.startswith("_")
                    ])
                    st.markdown("#### Sample personalized email")
                    st.write("**To:**", sample_row[email_col])
                    st.write("**Subject:**", personalize_template(subject_template, sample_row))
                    st.text(personalize_template(body_template, sample_row))

                if st.button("Save campaign and queue eligible recipients", type="primary", key="bulk_queue"):
                    try:
                        if not campaign_id or not campaign_name.strip():
                            raise ValueError("Campaign ID and campaign name are required.")
                        if not subject_template.strip() or not body_template.strip():
                            raise ValueError("Email subject and body are required.")
                        if not EMAIL_FROM:
                            raise ValueError("EMAIL_FROM is not configured in .env.")
                        if not SEND_EMAIL:
                            st.warning("Email sending is disabled in .env. Queueing can still be tested.")
                        ensure_email_tracking_tables()
                        upsert_campaign_record(campaign_id, campaign_name, subject_template, body_template)
                        queued_count, skipped_count = 0, 0
                        for _, row in eligible.iterrows():
                            email = str(row["_email_normalized"]).strip().lower()
                            customer_id = (
                                str(row[customer_id_col]).strip()
                                if customer_id_col != "-- None --" and pd.notna(row[customer_id_col])
                                else None
                            )
                            was_queued, status = queue_recipient(campaign_id, customer_id, email)
                            if was_queued:
                                queued_count += 1
                            else:
                                skipped_count += 1
                        st.session_state["bulk_ready_campaign_id"] = campaign_id
                        st.success(
                            f"Campaign saved. {queued_count} new recipient(s) queued; "
                            f"{skipped_count} existing recipient record(s) skipped."
                        )
                        log_event(
                            "info", "Bulk campaign queued", campaign_id=campaign_id,
                            queued=queued_count, skipped=skipped_count, eligible=len(eligible)
                        )
                    except Exception as exc:
                        log_event("exception", "Bulk campaign queue failed", error=str(exc))
                        st.error(f"Could not queue campaign: {exc}")

                st.divider()
                st.markdown("#### Human approval and send")
                ready_id = st.session_state.get("bulk_ready_campaign_id", "")
                if ready_id:
                    st.write(f"Campaign ready for review: **{ready_id}**")
                    st.warning(
                        "This action sends real emails. Verify the recipient list, consent, subject, "
                        "body, sender configuration and SMTP limits before approving."
                    )
                    human_confirm = st.checkbox(
                        f"I reviewed this campaign and approve sending all pending recipients for {ready_id}.",
                        key="bulk_human_approval"
                    )
                    if st.button("Approve and send pending emails", type="primary", key="bulk_send"):
                        if not human_confirm:
                            st.error("Explicit human approval is required.")
                        elif not approved_by.strip():
                            st.error("Enter the approver name / team ID.")
                        elif not SEND_EMAIL:
                            st.error("Sending is disabled. Set SEND_EMAIL=true in .env and restart Streamlit.")
                        else:
                            sent_count, failed_count, skipped_count = 0, 0, 0
                            try:
                                ensure_email_tracking_tables()
                                # Save the reviewed content and record approval before sending.
                                upsert_campaign_record(ready_id, campaign_name, subject_template, body_template)
                                approve_campaign(ready_id, approved_by.strip())
                                with get_sqlserver_connection() as conn:
                                    cursor = conn.cursor()
                                    cursor.execute(
                                        """
                                        SELECT Email FROM dbo.EmailRecipients
                                        WHERE CampaignID = ? AND Status = 'PENDING'
                                        ORDER BY ID
                                        """,
                                        ready_id
                                    )
                                    pending_recipients = cursor.fetchall()

                                progress = st.progress(0.0)
                                status_area = st.empty()
                                total = len(pending_recipients)
                                for idx, recipient in enumerate(pending_recipients, start=1):
                                    email = recipient[0]
                                    # Reload the current file row for personalization; fail closed if missing.
                                    matched = eligible[eligible["_email_normalized"] == str(email).lower()]
                                    if matched.empty:
                                        update_recipient_status(
                                            ready_id, email, "FAILED",
                                            "Recipient not present in the currently reviewed eligible upload."
                                        )
                                        failed_count += 1
                                        continue
                                    row = matched.iloc[0].drop(labels=[
                                        c for c in matched.columns if c.startswith("_")
                                    ])
                                    personalized_subject = personalize_template(subject_template, row)
                                    personalized_body = personalize_template(body_template, row)
                                    # Refuse unresolved placeholders rather than sending malformed copy.
                                    if re.search(r"\{[A-Za-z0-9_ -]+\}", personalized_subject + "\n" + personalized_body):
                                        update_recipient_status(
                                            ready_id, email, "FAILED",
                                            "Unresolved personalization placeholder; review template and upload columns."
                                        )
                                        failed_count += 1
                                        continue
                                    try:
                                        update_recipient_status(ready_id, email, "SENDING")
                                        send_campaign_email(
                                            email,
                                            EMAIL_FROM,
                                            personalized_subject,
                                            personalized_body
                                        )
                                        update_recipient_status(ready_id, email, "SENT")
                                        sent_count += 1
                                    except Exception as exc:
                                        # Do not auto-retry: SMTP failures can be ambiguous if the server accepted the message.
                                        update_recipient_status(ready_id, email, "FAILED", str(exc))
                                        failed_count += 1
                                    progress.progress(idx / max(total, 1))
                                    status_area.write(
                                        f"Processed {idx}/{total} · sent {sent_count} · failed {failed_count}"
                                    )
                                finish_campaign(ready_id)
                                st.success(
                                    f"Send run finished. Sent: {sent_count}; failed: {failed_count}; "
                                    f"pending batch size: {total}. Review SQL Server tracking for audit details."
                                )
                                log_event(
                                    "info", "Bulk campaign send finished", campaign_id=ready_id,
                                    sent=sent_count, failed=failed_count, attempted=total
                                )
                            except Exception as exc:
                                log_event("exception", "Bulk campaign send interrupted", campaign_id=ready_id, error=str(exc))
                                st.error(f"Bulk send stopped: {exc}")
                                st.warning(
                                    "Do not blindly rerun after an interrupted send. Check recipient statuses in SQL Server first."
                                )
                else:
                    st.info("Save and queue the campaign first. Sending remains unavailable until a campaign is queued.")
            else:
                st.info("Select an email column and a marketing-consent column to calculate eligibility.")
        except Exception as exc:
            st.error(f"Could not process customer file: {exc}")
            log_event("exception", "Bulk customer file processing failed", error=str(exc))


with tab_send:
    st.subheader("Send to a customer list", anchor=False)
    st.caption(
        "Upload a customer file for validation. The studio automatically recognizes standard email, "
        "consent, and opt-out columns, then uses the reviewed email from the Email review stage."
    )

    customer_file = st.file_uploader(
        "Customer list (.csv or .xlsx)",
        type=["csv", "xlsx"],
        key="streamlined_bulk_customer_file",
        help="Required columns: email and marketing_consent. Optional: unsubscribed.",
    )

    if customer_file is None:
        st.info("Upload a customer file to preview and validate the recipients.", icon=":material/upload_file:")
    else:
        try:
            customers = normalize_customer_file(customer_file)
            st.markdown("##### Customer file preview")
            st.dataframe(
                customers.head(100),
                width="stretch",
                alt="Preview of the uploaded customer file",
            )
            st.caption(f"Showing up to 100 of {len(customers)} uploaded rows from {customer_file.name}.")

            test_delivery_mode = st.toggle(
                "Test delivery mode",
                help=(
                    "Allows these recipients to be sent again under a new test campaign ID. "
                    "Keep this off for live campaigns."
                ),
                key="streamlined_bulk_test_mode",
            )
            if test_delivery_mode:
                st.warning(
                    "Test delivery mode is on. A new campaign can send again to these addresses; "
                    "use only inboxes you control.",
                    icon=":material/science:",
                )

            email_column, eligible, excluded = prepare_bulk_recipients(
                customers, allow_repeat_delivery=test_delivery_mode
            )
            metric_cols = st.columns(3)
            metric_cols[0].metric("Uploaded", len(customers), border=True)
            metric_cols[1].metric("Ready to send", len(eligible), border=True)
            metric_cols[2].metric("Held for review", len(excluded), border=True)
            if not excluded.empty:
                with st.expander("View validation results"):
                    st.dataframe(
                        excluded[[email_column, "_exclusion_reason"]],
                        width="stretch",
                        alt="Recipients excluded by delivery validation",
                    )

            st.markdown("##### Email ready for delivery")
            bulk_subject = st.session_state.get("email_subject", "").strip()
            bulk_body = st.session_state.get("email_body", "").strip()
            if not bulk_subject or not bulk_body:
                st.warning(
                    "Create and review an email in the Email review stage before sending to this list.",
                    icon=":material/drafts:",
                )
            elif eligible.empty:
                st.warning("No recipients passed validation. Resolve the held records before sending.")
            else:
                with st.container(border=True):
                    st.caption("Subject")
                    st.write(bulk_subject)
                    st.caption("Body preview")
                    st.write(bulk_body)
                    st.caption("To edit the copy or creative, return to Email review.")

                st.session_state.setdefault(
                    "streamlined_bulk_campaign_id",
                    f"BULK-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8].upper()}",
                )
                campaign_id = st.session_state["streamlined_bulk_campaign_id"]
                campaign_complete = st.session_state.get("streamlined_bulk_complete", False)
                if campaign_complete:
                    st.info(
                        f"Campaign {campaign_id} is complete. Start a new campaign to send a new, "
                        "separately tracked delivery.",
                        icon=":material/task_alt:",
                    )
                    if st.button(
                        "Start a new campaign",
                        icon=":material/add:",
                        key="streamlined_bulk_new_campaign",
                    ):
                        for state_key in (
                            "streamlined_bulk_campaign_id",
                            "streamlined_bulk_complete",
                            "streamlined_bulk_approval",
                        ):
                            st.session_state.pop(state_key, None)
                        st.rerun()
                if not SEND_EMAIL:
                    st.warning("Email sending is disabled. Set SEND_EMAIL=true and restart before delivery.")
                approval = st.checkbox(
                    f"I approve sending this reviewed email to {len(eligible)} validated recipient(s).",
                    key="streamlined_bulk_approval",
                )
                if st.button(
                    "Send campaign",
                    type="primary",
                    icon=":material/send:",
                    key="streamlined_bulk_send",
                    disabled=not SEND_EMAIL or campaign_complete,
                ):
                    if not approval:
                        st.error("Confirm approval before sending.")
                    else:
                        sent_count, failed_count = 0, 0
                        try:
                            ensure_email_tracking_tables()
                            upsert_campaign_record(campaign_id, "Bulk delivery", bulk_subject, bulk_body)
                            queued_count = 0
                            for email in eligible["_email_normalized"]:
                                was_queued, _ = queue_recipient(
                                    campaign_id,
                                    None,
                                    email,
                                    allow_repeat_send=test_delivery_mode,
                                )
                                queued_count += int(was_queued)
                            if not queued_count:
                                raise RuntimeError(
                                    "No new recipients were queued. This file may already have been sent with this campaign."
                                )

                            approve_campaign(campaign_id, "Bulk delivery confirmation")
                            with get_sqlserver_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute(
                                    "SELECT Email FROM dbo.EmailRecipients "
                                    "WHERE CampaignID = ? AND Status = 'PENDING' ORDER BY ID",
                                    campaign_id,
                                )
                                pending_recipients = cursor.fetchall()

                            bulk_image_bytes = (
                                email_attachment.getvalue() if email_attachment is not None else None
                            )
                            bulk_image_name = email_attachment.name if email_attachment is not None else None
                            bulk_image_mode = (
                                "inline" if email_image_choice == "Inline image in email"
                                else "attachment" if email_image_choice == "Image attachment"
                                else "none"
                            )
                            progress = st.progress(0.0)
                            status_area = st.empty()
                            for index, recipient in enumerate(pending_recipients, start=1):
                                email = recipient[0]
                                try:
                                    update_recipient_status(campaign_id, email, "SENDING")
                                    send_campaign_email(
                                        email,
                                        EMAIL_FROM,
                                        bulk_subject,
                                        bulk_body,
                                        image_bytes=bulk_image_bytes,
                                        image_name=bulk_image_name,
                                        image_mode=bulk_image_mode,
                                    )
                                    update_recipient_status(campaign_id, email, "SENT")
                                    sent_count += 1
                                except Exception as exc:
                                    update_recipient_status(campaign_id, email, "FAILED", str(exc))
                                    failed_count += 1
                                progress.progress(index / len(pending_recipients))
                                status_area.caption(
                                    f"Processed {index}/{len(pending_recipients)} · "
                                    f"sent {sent_count} · failed {failed_count}"
                                )
                            finish_campaign(campaign_id)
                            st.session_state["streamlined_bulk_complete"] = True
                            st.success(
                                f"Campaign {campaign_id} completed. Sent: {sent_count}; failed: {failed_count}."
                            )
                            log_event(
                                "info", "Simplified bulk delivery completed", campaign_id=campaign_id,
                                sent=sent_count, failed=failed_count,
                            )
                        except Exception as exc:
                            log_event(
                                "exception", "Simplified bulk delivery failed", campaign_id=campaign_id,
                                error=str(exc),
                            )
                            st.error(f"Bulk delivery stopped: {exc}")
        except Exception as exc:
            log_event("exception", "Bulk customer file validation failed", error=str(exc))
            st.error(f"Could not validate the customer file: {exc}")

st.divider()
st.caption("Application status: local Ollama text generation + file text extraction + creative/email drafting + optional Elasticsearch persistence/search + SQL Server campaign/recipient status tracking for bulk email. Bulk delivery requires explicit human approval and SEND_EMAIL=true. Runtime events are written to marketing_campaign_studio.log.")
