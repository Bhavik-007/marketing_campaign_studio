import io
import smtplib
import ssl
from email.message import EmailMessage
import json
import os
import logging
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
    page_icon="🎨",
    layout="wide",
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


st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1250px;}
.hero {padding: 1.4rem 1.6rem; border-radius: 18px;
       background: linear-gradient(120deg, #102a43, #245b78); color: white;}
.hero h1 {color: white; margin-bottom: .25rem;}
.hero p {color: #e6f1f7; margin-bottom: 0;}
.small-note {font-size: .85rem; color: #64748b;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <h1>🎨 Marketing Campaign Studio</h1>
  <p>Turn campaign briefs into creative prompts and email drafts — with Ollama running locally.</p>
</div>
""", unsafe_allow_html=True)
st.write("")

with st.sidebar:
    st.header("Settings")
    ollama_url = st.text_input("Ollama URL", value=OLLAMA_URL)
    ollama_model = st.text_input("Ollama model", value=OLLAMA_MODEL)
    st.caption("Check installed models with `ollama list`.")
    if st.button("Test Ollama connection", use_container_width=True):
        try:
            r = requests.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=5)
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
            st.success("Ollama is reachable.")
            st.write("Installed models:", ", ".join(models) if models else "No models listed")
        except Exception as e:
            st.error(f"Could not connect to Ollama: {e}")


    st.divider()
    st.subheader("Elasticsearch")
    st.caption(f"Index: `{ELASTICSEARCH_INDEX}`")
    if st.button("Test Elasticsearch connection", use_container_width=True):
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
            "Email settings are incomplete. Set EMAIL_TO, EMAIL_FROM, SMTP_HOST, SMTP_PORT, "
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



tab_upload, tab_creative, tab_email, tab_send = st.tabs([
    "1 · Campaign inputs", "2 · Creative studio", "3 · Email draft", "4 · Recipient review"
])

with tab_upload:
    st.subheader("Upload campaign source files")
    st.write("Upload a campaign brief or existing creative. Supported text extraction: PDF, DOCX, XLSX, TXT, MD, CSV. Images can be uploaded as visual references.")
    files = st.file_uploader(
        "Campaign files",
        type=["pdf", "docx", "xlsx", "xls", "txt", "md", "csv", "png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="campaign_files",
    )
    pasted_brief = st.text_area(
        "Or paste campaign content here",
        height=160,
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
    if st.button("Prepare campaign brief", type="primary"):
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

    mode = st.radio(
        "Generation mode",
        ["Content-to-image", "Image-to-image"],
        horizontal=True,
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
                        use_container_width=True,
                    )
                except Exception as exc:
                    st.error(f"The file was uploaded but could not be displayed as an image: {exc}")
            else:
                st.markdown(
                    "<div style='height:220px;display:flex;align-items:center;"
                    "justify-content:center;border:1px dashed #9ca3af;border-radius:12px;"
                    "color:#64748b;'>Image preview will appear here</div>",
                    unsafe_allow_html=True,
                )
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
    email_image_choice = st.radio(
        "Creative placement",
        ["Inline image in email", "Image attachment", "No image for this draft"],
        horizontal=True,
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

with tab_send:
    st.subheader("Recipient eligibility review")
    st.warning("Sending is intentionally disabled in this prototype. No emails will be sent.")
    st.write("Upload a customer CSV to inspect candidate recipients. This demo does not decide eligibility automatically; define and validate campaign-specific rules before sending.")
    customer_csv = st.file_uploader("Customer list CSV", type=["csv"], key="customer_csv")
    if customer_csv:
        try:
            customers = pd.read_csv(customer_csv)
            st.write("Customer list preview")
            st.dataframe(customers.head(100), use_container_width=True)
            st.caption(f"{len(customers)} row(s) loaded. Check consent/opt-out, segment, and email validity before any future send workflow.")
        except Exception as e:
            log_event("exception", "Recipient CSV read failed", error=str(e))
            st.error(f"Could not read CSV: {e}")
    st.text_area("Campaign eligibility rules to confirm", value="1. Match campaign audience/segment.\n2. Verify valid email address.\n3. Exclude unsubscribed or opted-out contacts.\n4. Exclude duplicates and ineligible records.\n5. Obtain human approval before sending.", height=150)

st.divider()
st.caption("Application status: local Ollama text generation + file text extraction + creative/email drafting + optional Elasticsearch campaign persistence/search. SMTP email delivery is available when SEND_EMAIL=true and valid SMTP credentials are configured. Actual image generation, SQL Server logging, and the customer-rule engine are not connected in this version. Runtime events are written to marketing_campaign_studio.log.")
