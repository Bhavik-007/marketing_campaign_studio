# Marketing Campaign Studio

## 1. Application Overview

Marketing Campaign Studio is a local Streamlit prototype for preparing marketing campaign content using Ollama. It brings together campaign file intake, creative prompt preparation, email drafting, optional Elasticsearch storage/search, and a recipient CSV review screen.

### Main Capabilities

- **Campaign inputs and file parsing**
  - Upload multiple campaign source files or paste a campaign brief.
  - Extract text from PDF, DOCX, XLSX/XLS, TXT, MD, and CSV files.
  - Upload PNG, JPG/JPEG, or WEBP files as visual references. OCR/image understanding is not configured, so text is not extracted from images.
  - Save parsed campaign content to Elasticsearch and search previously saved campaign documents.

- **Creative Studio**
  - Generate a structured creative brief and image-generation prompt using Ollama.
  - Choose content-to-image or image-to-image mode and provide creative direction, brand constraints, and additional instructions.
  - In image-to-image mode, upload and preview a reference image.
  - The current prototype generates the prompt only; it does not render the final image.

- **Email drafting and sending**
  - Draft a customer-ready marketing email with Ollama using the campaign source content.
  - Review and edit the subject and body before sending.
  - Optionally embed a campaign image inline in the email or attach it as a file.
  - Real email sending requires SMTP configuration and `SEND_EMAIL=true` in `.env`, plus explicit approval in the app. Sending is limited to the configured `EMAIL_TO` address.

- **Recipient eligibility review**
  - Upload a customer CSV and preview its contents.
  - Review campaign eligibility considerations such as audience/segment, valid email addresses, opt-outs, duplicates, and human approval.
  - This screen is a review prototype only; it does not automatically enforce eligibility rules or send bulk emails.

- **Elasticsearch and application logging**
  - Uses Elasticsearch at the configured URL and index to store and search parsed campaign-source documents.
  - Creates the configured index if it does not exist.
  - Elasticsearch is optional: other app features can continue if it is unavailable.
  - Writes runtime events to `marketing_campaign_studio.log`. Do not treat this as SQL Server logging; SQL Server integration is not connected in this version.

## 2. Technology Stack

- Python
- Streamlit for the user interface
- Ollama for local text generation
- Elasticsearch for campaign document storage and search
- SMTP for optional email delivery
- pandas for CSV and spreadsheet processing
- `requests` for Ollama and Elasticsearch API calls
- `python-dotenv` for loading `.env` configuration
- `pypdf` for PDF text extraction
- `python-docx` for DOCX text extraction
- Pillow (`PIL`) for image preview and inline-email image preparation
- An Excel reader supported by pandas, such as `openpyxl`, for XLSX files

## 3. Application Flow

1. Upload campaign files or paste campaign content.
2. Prepare the campaign brief; extracted content can be saved to Elasticsearch.
3. Search saved campaign documents when needed.
4. Use Ollama to create a creative brief and image-generation prompt.
5. Use Ollama to draft an email, then review and edit the subject and body.
6. If configured and explicitly approved, send the email to the single configured recipient, with an optional inline image or attachment.
7. Upload a customer CSV in Recipient Review to inspect candidate recipients and review eligibility rules.

## 4. Configuration

Create or update a `.env` file in the project folder. Use your own valid local settings and credentials.

```dotenv
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1

ELASTICSEARCH_URL=http://localhost:9200
ELASTICSEARCH_INDEX=marketing-documents-v1
ELASTICSEARCH_TIMEOUT=5

EMAIL_TO=your-recipient@example.com
EMAIL_FROM=your-sender@example.com
SEND_EMAIL=false
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-sender@example.com
SMTP_PASSWORD=
SMTP_SECURE=false
```

- Keep `SEND_EMAIL=false` while testing. Set it to `true` only when you are ready to allow real email sending.
- For Gmail, use a Google App Password when required; do not use or share your normal account password.
- Do not commit `.env` or credentials to source control.
- The app defaults to Ollama at `http://localhost:11434`, model `llama3.1`, Elasticsearch at `http://localhost:9200`, and index `marketing-documents-v1` if the corresponding variables are not set.

## 5. Current Scope and Limitations

- Actual image generation is not integrated; the app prepares a prompt only.
- OCR and image understanding are not configured for uploaded source images.
- SQL Server integration is not connected in this version.
- Recipient eligibility rules are displayed for review but are not automatically enforced.
- The Recipient Review tab does not send bulk emails. The separate email-send flow sends only to the configured `EMAIL_TO` address.
- Elasticsearch is optional; if it is unavailable, its save/search features will fail, while other app features are intended to remain available.

## 6. How to Run the Application

1. Open PowerShell or the VS Code terminal.
2. Navigate to the project folder:

   ```powershell
   cd C:\Bhavik\Live_VS_code\marketing_campaign_studio
   ```

3. Activate the virtual environment:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

4. Ensure Ollama is running and the configured model is available. Check installed models with:

   ```powershell
   ollama list
   ```

5. Ensure Elasticsearch is running if you want to use campaign document storage and search.
6. Install project dependencies if they have not already been installed:

   ```powershell
   pip install -r requirements.txt
   ```

7. Start the Streamlit app:

   ```powershell
   docker start elasticsearch
   docker start kibana
   streamlit run app.py
   ```

8. Open the local URL shown in the terminal, usually:

   http://localhost:8501

   ELASTICSEARCH_URL=http://localhost:9200
   Kibana: `http://localhost:5601`


### Stop the Application

Press `Ctrl + C` in the terminal where Streamlit is running.

## 7. Current implementation addendum

This section documents additions made after the original prototype notes above. It is additive; earlier sections are retained for project history.

### Enterprise workflow UI

- The Streamlit interface now uses a branded light/dark theme, distinct accent treatment for each stage, and a live workflow-readiness summary.
- The four user-facing stages are **Brief**, **Creative studio**, **Email review**, and **Delivery**.
- A guided workflow panel helps first-time users understand the recommended sequence.
- The sidebar shows delivery and SQL-tracking readiness. Ollama endpoint and model selection are backend configuration only and are no longer exposed as editable frontend fields.

### SQL Server-backed bulk delivery

- Bulk delivery validates recipients, creates a tracked campaign, queues valid recipients, records approval, and stores recipient delivery status in SQL Server.
- The app supports either a single `SQLSERVER_CONNECTION_STRING` or the existing split configuration values:

  ```dotenv
  SQL_SERVER=your-server
  SQL_DATABASE=Marketing
  SQL_AUTH_MODE=SQL
  SQL_USERNAME=your-user
  SQL_PASSWORD=your-password
  SQL_DRIVER=ODBC Driver 18 for SQL Server
  SQL_ENCRYPT=yes
  SQL_TRUST_SERVER_CERTIFICATE=no
  ```

- Use [sql/01_create_email_tracking_tables.sql](sql/01_create_email_tracking_tables.sql) as the reference, idempotent table-creation script. It creates `dbo.EmailCampaigns` and `dbo.EmailRecipients` only when they do not already exist.
- In normal mode, addresses previously marked `SENT` are excluded. **Test delivery mode** is an explicit opt-in that allows controlled repeat sends under a new campaign ID; use it only with inboxes you control.
- Every bulk send requires an explicit approval checkbox. If a campaign has completed, select **Start a new campaign** to create a new tracked delivery.

### Campaign image delivery

- The reviewed image choice from **Email review** is applied to every eligible bulk recipient.
- Choose **Inline image in email** to embed the campaign image in the email body, or **Image attachment** to send it as an attachment.

### Sample data and document-upload tests

The `sample_data/` directory contains safe inputs for exercising the workflow:

- `bulk_campaign_customers.csv` - customer-list upload and bulk-delivery validation.
- `campaign_brief_fresh_start.md` and `campaign_product_facts.csv` - upload both files together to test campaign-source extraction, creative prompting, and email drafting.
- `campaign_upload_instructions.txt` - concise test sequence for the source-document flow.
- `campaign_pdf_with_image_and_link.pdf` - one-page PDF source document with embedded product imagery and a clickable `example.com` sample purchase link.
- `fresh_start_product_mockup.png` - the product visual used by the PDF campaign sample.

The PDF's visible purchase URL can be extracted as campaign context. The current email sender preserves URLs in generated email content; a dedicated HTML link-rendering enhancement can be added when guaranteed clickable call-to-action links are required across every mail client.

### Delivery safety

- Keep `SEND_EMAIL=false` unless you are intentionally testing delivery.
- When enabling delivery, start with addresses and inboxes you control.
- Do not enable **Test delivery mode** for real customer campaigns.
