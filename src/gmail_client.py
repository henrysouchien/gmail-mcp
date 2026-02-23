"""
Gmail API client for MCP server.
Handles authentication and API operations.
"""

import base64
import pickle
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Gmail API scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',   # Read emails/labels
    'https://www.googleapis.com/auth/gmail.send',       # Send emails
    'https://www.googleapis.com/auth/gmail.modify',     # Modify labels, trash
    'https://www.googleapis.com/auth/gmail.labels',     # Manage labels
]

# Paths relative to this module's parent directory
BASE_DIR = Path(__file__).parent.parent
CREDENTIALS_FILE = BASE_DIR / 'gmail_credentials.json'
TOKEN_FILE = BASE_DIR / 'gmail_token.pickle'


def authenticate():
    """Authenticate with Gmail API and return service object."""
    creds = None

    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"Credentials file not found at {CREDENTIALS_FILE}. "
                    "Please download OAuth credentials from Google Cloud Console "
                    "and save as gmail_credentials.json in the gmail-mcp folder."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)

    return build('gmail', 'v1', credentials=creds)


def get_labels(service) -> list[dict]:
    """Get all labels (folders) in the mailbox."""
    results = service.users().labels().list(userId='me').execute()
    return results.get('labels', [])


def get_label_id(service, label_name: str) -> Optional[str]:
    """Get label ID from label name. Handles both system and user labels."""
    # System labels have name == id (e.g., INBOX, SENT, TRASH)
    system_labels = ['INBOX', 'SENT', 'DRAFT', 'TRASH', 'SPAM', 'STARRED',
                     'IMPORTANT', 'UNREAD', 'CATEGORY_PERSONAL',
                     'CATEGORY_SOCIAL', 'CATEGORY_PROMOTIONS',
                     'CATEGORY_UPDATES', 'CATEGORY_FORUMS']

    if label_name.upper() in system_labels:
        return label_name.upper()

    # For user labels, look up by name
    labels = get_labels(service)
    for label in labels:
        if label['name'].lower() == label_name.lower():
            return label['id']

    return None


def list_messages(
    service,
    max_results: int = 20,
    label_ids: Optional[list[str]] = None,
    query: Optional[str] = None
) -> list[dict]:
    """List messages with optional filters."""
    kwargs = {
        'userId': 'me',
        'maxResults': max_results,
    }
    if label_ids:
        kwargs['labelIds'] = label_ids
    if query:
        kwargs['q'] = query

    results = service.users().messages().list(**kwargs).execute()
    messages = results.get('messages', [])

    # Fetch basic metadata for each message
    detailed_messages = []
    for msg in messages:
        msg_data = service.users().messages().get(
            userId='me',
            id=msg['id'],
            format='metadata',
            metadataHeaders=['From', 'To', 'Subject', 'Date']
        ).execute()

        headers = {h['name']: h['value'] for h in msg_data.get('payload', {}).get('headers', [])}
        detailed_messages.append({
            'id': msg['id'],
            'threadId': msg_data.get('threadId'),
            'snippet': msg_data.get('snippet', ''),
            'from': headers.get('From', ''),
            'to': headers.get('To', ''),
            'subject': headers.get('Subject', ''),
            'date': headers.get('Date', ''),
            'labelIds': msg_data.get('labelIds', []),
        })

    return detailed_messages


def get_message(service, message_id: str) -> dict:
    """Get full message content including body."""
    msg = service.users().messages().get(
        userId='me',
        id=message_id,
        format='full'
    ).execute()

    headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}

    body = extract_body(msg.get('payload', {}))

    return {
        'id': msg['id'],
        'threadId': msg.get('threadId'),
        'from': headers.get('From', ''),
        'to': headers.get('To', ''),
        'cc': headers.get('Cc', ''),
        'bcc': headers.get('Bcc', ''),
        'subject': headers.get('Subject', ''),
        'date': headers.get('Date', ''),
        'message_id': headers.get('Message-ID', ''),
        'references': headers.get('References', ''),
        'in_reply_to': headers.get('In-Reply-To', ''),
        'labelIds': msg.get('labelIds', []),
        'body': body,
    }


def extract_body(payload: dict) -> str:
    """
    Recursively extract email body from MIME payload.
    Prefers plain text, falls back to stripped HTML.
    """
    mime_type = payload.get('mimeType', '')

    # Direct body data
    if 'body' in payload and payload['body'].get('data'):
        data = payload['body']['data']
        decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')

        if mime_type == 'text/plain':
            return decoded
        elif mime_type == 'text/html':
            return strip_html(decoded)

    # Multipart - recurse into parts
    if 'parts' in payload:
        plain_text = None
        html_text = None

        for part in payload['parts']:
            part_mime = part.get('mimeType', '')

            if part_mime == 'text/plain':
                if 'body' in part and part['body'].get('data'):
                    data = part['body']['data']
                    plain_text = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            elif part_mime == 'text/html':
                if 'body' in part and part['body'].get('data'):
                    data = part['body']['data']
                    html_text = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            elif part_mime.startswith('multipart/'):
                # Recurse into nested multipart
                nested = extract_body(part)
                if nested:
                    return nested

        # Prefer plain text over HTML
        if plain_text:
            return plain_text
        if html_text:
            return strip_html(html_text)

    return ""


def strip_html(html: str) -> str:
    """Strip HTML tags and decode entities for readable text."""
    # Remove style and script tags with content
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Replace br and p tags with newlines
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</p>', '\n\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</div>', '\n', html, flags=re.IGNORECASE)

    # Remove remaining tags
    html = re.sub(r'<[^>]+>', '', html)

    # Decode common HTML entities
    html = html.replace('&nbsp;', ' ')
    html = html.replace('&amp;', '&')
    html = html.replace('&lt;', '<')
    html = html.replace('&gt;', '>')
    html = html.replace('&quot;', '"')
    html = html.replace('&#39;', "'")

    # Clean up whitespace
    html = re.sub(r'\n\s*\n\s*\n+', '\n\n', html)
    html = re.sub(r'[ \t]+', ' ', html)

    return html.strip()


def create_message(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    thread_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> dict:
    """Create a message ready for sending."""
    message = MIMEMultipart('alternative')
    message['To'] = to
    message['Subject'] = subject

    if cc:
        message['Cc'] = cc
    if bcc:
        message['Bcc'] = bcc
    if in_reply_to:
        message['In-Reply-To'] = in_reply_to
    if references:
        message['References'] = references

    # Add plain text body
    text_part = MIMEText(body, 'plain')
    message.attach(text_part)

    # Encode the message
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')

    result = {'raw': raw}
    if thread_id:
        result['threadId'] = thread_id

    return result


def send_message(service, message: dict) -> dict:
    """Send an email message."""
    return service.users().messages().send(
        userId='me',
        body=message
    ).execute()


def modify_labels(
    service,
    message_id: str,
    add_labels: Optional[list[str]] = None,
    remove_labels: Optional[list[str]] = None
) -> dict:
    """Add or remove labels from a message."""
    body = {}
    if add_labels:
        body['addLabelIds'] = add_labels
    if remove_labels:
        body['removeLabelIds'] = remove_labels

    return service.users().messages().modify(
        userId='me',
        id=message_id,
        body=body
    ).execute()


def trash_message(service, message_id: str) -> dict:
    """Move a message to trash."""
    return service.users().messages().trash(
        userId='me',
        id=message_id
    ).execute()


def delete_message(service, message_id: str) -> dict:
    """Permanently delete a message (cannot be undone)."""
    return service.users().messages().delete(
        userId='me',
        id=message_id
    ).execute()


def get_thread_messages(service, thread_id: str) -> list[dict]:
    """Get all messages in a thread for building References header."""
    thread = service.users().threads().get(
        userId='me',
        id=thread_id,
        format='metadata',
        metadataHeaders=['Message-ID']
    ).execute()

    messages = []
    for msg in thread.get('messages', []):
        headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
        messages.append({
            'id': msg['id'],
            'message_id': headers.get('Message-ID', ''),
        })

    return messages


# Quick test when run directly
if __name__ == "__main__":
    print("Testing Gmail connection...")
    service = authenticate()
    print("Authenticated successfully")

    # List labels
    labels = get_labels(service)
    print(f"Found {len(labels)} labels")
    for label in labels[:5]:
        print(f"  - {label['name']}")

    # List recent messages
    messages = list_messages(service, max_results=3)
    print(f"\nRecent messages:")
    for msg in messages:
        print(f"  - {msg['subject'][:50]}...")
