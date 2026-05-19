"""
MCP Server for Gmail.
Provides tools for reading, sending, and managing emails.
"""

import base64
import json
from dataclasses import dataclass
from typing import Any, Optional
from mcp.server.fastmcp import FastMCP

from . import gmail_client

# Create the MCP server
mcp = FastMCP("gmail-mcp")


@dataclass
class ToolError:
    error_class: str
    message: str
    names_correction: dict[str, Any] | None = None
    suggested_tool_calls: list[dict[str, Any]] | None = None

    def to_envelope(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error_class": self.error_class,
            "message": self.message,
            "names_correction": self.names_correction or {},
            "suggested_tool_calls": self.suggested_tool_calls or [],
        }


def _exception_envelope(
    exc: Exception,
    *,
    error_class: str | None = None,
    suggested_tool_calls: list[dict[str, Any]] | None = None,
    names_correction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ToolError(
        error_class=error_class or type(exc).__name__,
        message=str(exc),
        names_correction=names_correction,
        suggested_tool_calls=suggested_tool_calls,
    ).to_envelope()


def _label_not_found(label_name: str) -> dict[str, Any]:
    return ToolError(
        error_class="LabelNotFound",
        message=f"Label not found: {label_name}",
        names_correction={"label": "Run gmail_list_labels and use one of the returned names."},
        suggested_tool_calls=[{"name": "gmail_list_labels", "args": {}}],
    ).to_envelope()


def _message_discovery_hint() -> list[dict[str, Any]]:
    return [
        {
            "name": "gmail_list_inbox",
            "args": {"max_results": 20},
        },
        {
            "name": "gmail_search_emails",
            "args": {"query": "from:example@example.com", "max_results": 10},
        },
    ]


def _make_restore_token(payload: dict[str, Any]) -> str:
    token_payload = {"version": 1, **payload}
    raw = json.dumps(token_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _parse_restore_token(restore_token: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(restore_token.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid restore_token") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Invalid restore_token")
    return payload


@mcp.tool()
def gmail_list_labels() -> str | dict:
    """
    List all Gmail labels (folders) in the mailbox.
    Returns both system labels (INBOX, SENT, etc.) and user-created labels.
    """
    try:
        service = gmail_client.authenticate()
        labels = gmail_client.get_labels(service)

        if not labels:
            return "No labels found."

        system_labels = []
        user_labels = []

        for label in labels:
            label_type = label.get('type', 'user')
            if label_type == 'system':
                system_labels.append(label['name'])
            else:
                user_labels.append(label['name'])

        result = "Gmail Labels:\n\n"

        if system_labels:
            result += "System Labels:\n"
            for name in sorted(system_labels):
                result += f"  - {name}\n"

        if user_labels:
            result += "\nUser Labels:\n"
            for name in sorted(user_labels):
                result += f"  - {name}\n"

        return result
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=[])


@mcp.tool()
def gmail_list_inbox(
    max_results: int = 20,
    label: Optional[str] = None,
    unread_only: bool = False
) -> str | dict:
    """
    List recent messages from inbox or a specific label.

    Args:
        max_results: Maximum number of messages to return (default: 20)
        label: Label/folder to list from (default: INBOX). Examples: "INBOX", "SENT", "STARRED", or custom labels
        unread_only: If True, only show unread messages
    """
    try:
        service = gmail_client.authenticate()

        # Resolve label to ID
        label_name = label or 'INBOX'
        label_id = gmail_client.get_label_id(service, label_name)
        if not label_id:
            return _label_not_found(label_name)

        label_ids = [label_id]
        if unread_only:
            label_ids.append('UNREAD')

        messages = gmail_client.list_messages(
            service,
            max_results=max_results,
            label_ids=label_ids
        )

        if not messages:
            return f"No messages found in '{label_name}'."

        result = f"Messages in '{label_name}' ({len(messages)} shown):\n\n"

        for msg in messages:
            is_unread = 'UNREAD' in msg.get('labelIds', [])
            unread_marker = "[UNREAD] " if is_unread else ""

            result += f"{unread_marker}From: {msg['from']}\n"
            result += f"Subject: {msg['subject']}\n"
            result += f"Date: {msg['date']}\n"
            result += f"ID: {msg['id']}\n"
            result += f"Preview: {msg['snippet'][:100]}...\n"
            result += "-" * 50 + "\n"

        return result
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=[{"name": "gmail_list_labels", "args": {}}])


@mcp.tool()
def gmail_search_emails(query: str, max_results: int = 20) -> str | dict:
    """
    Search emails using Gmail query syntax.

    Args:
        query: Gmail search query. Examples:
            - "from:someone@example.com"
            - "subject:meeting"
            - "is:unread"
            - "has:attachment"
            - "after:2024/01/01"
            - "label:work"
            - "in:sent"
            - Combine: "from:boss@company.com is:unread after:2024/01/01"
        max_results: Maximum number of results to return (default: 20)
    """
    try:
        service = gmail_client.authenticate()

        messages = gmail_client.list_messages(
            service,
            max_results=max_results,
            query=query
        )

        if not messages:
            return f"No messages found for query: {query}"

        result = f"Search results for '{query}' ({len(messages)} found):\n\n"

        for msg in messages:
            is_unread = 'UNREAD' in msg.get('labelIds', [])
            unread_marker = "[UNREAD] " if is_unread else ""

            result += f"{unread_marker}From: {msg['from']}\n"
            result += f"Subject: {msg['subject']}\n"
            result += f"Date: {msg['date']}\n"
            result += f"ID: {msg['id']}\n"
            result += f"Preview: {msg['snippet'][:100]}...\n"
            result += "-" * 50 + "\n"

        return result
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=[])


@mcp.tool()
def gmail_read_email(message_id: str) -> str | dict:
    """
    Read the full content of an email by its message ID.

    Args:
        message_id: The ID of the message to read (from list or search results)
    """
    try:
        service = gmail_client.authenticate()
        msg = gmail_client.get_message(service, message_id)

        result = "=" * 60 + "\n"
        result += f"From: {msg['from']}\n"
        result += f"To: {msg['to']}\n"
        if msg['cc']:
            result += f"Cc: {msg['cc']}\n"
        result += f"Subject: {msg['subject']}\n"
        result += f"Date: {msg['date']}\n"
        result += f"Labels: {', '.join(msg['labelIds'])}\n"
        result += f"Thread ID: {msg['threadId']}\n"
        result += f"Message ID: {msg['message_id']}\n"
        result += "=" * 60 + "\n\n"
        result += msg['body']

        return result
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=_message_discovery_hint())


@mcp.tool()
def gmail_create_draft(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None
) -> str | dict:
    """
    Compose a new email draft without sending it.

    Discovery: run `gmail_list_inbox` first to obtain `to` values.

    Args:
        to: Recipient email address(es), comma-separated for multiple
        subject: Email subject line
        body: Email body text (plain text)
        cc: CC recipients, comma-separated (optional)
        bcc: BCC recipients, comma-separated (optional)

    Use this for: starting a new email conversation safely as a draft.
    NOT for: sending an already-created draft -> see `gmail_send_draft`.
    NOT for: replying in an existing thread with a message ID -> see `gmail_create_reply_draft`.
    NOT for: finding messages or message IDs before acting → see `gmail_search_emails`.
    NOT for: reading existing email content without sending → see `gmail_read_email`.
    """
    try:
        service = gmail_client.authenticate()
        message = gmail_client.create_message(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc
        )
        draft = gmail_client.create_draft(service, message)
        return (
            "Email draft created.\n"
            f"Draft ID: {draft['id']}\n"
            "Review the draft, then call gmail_send_draft(draft_id) to send."
        )
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=[{"name": "gmail_create_draft", "args": {"to": to, "subject": subject, "body": body}}])


@mcp.tool()
def gmail_send_draft(draft_id: str) -> str | dict:
    """
    Send an existing Gmail draft by draft ID.

    Discovery: create or identify a draft first with `gmail_create_draft` or `gmail_create_reply_draft`.

    Args:
        draft_id: Gmail draft ID to send

    Use this for: the final irreversible send step after draft review.
    NOT for: composing new message content -> see `gmail_create_draft`.
    NOT for: composing a reply draft -> see `gmail_create_reply_draft`.
    """
    try:
        service = gmail_client.authenticate()
        result = gmail_client.send_draft(service, draft_id)
        return f"Draft sent successfully!\nMessage ID: {result['id']}\nThread ID: {result.get('threadId', '')}"
    except Exception as e:
        return _exception_envelope(
            e,
            names_correction={"draft_id": "Use the Draft ID returned by gmail_create_draft or gmail_create_reply_draft."},
            suggested_tool_calls=[
                {"name": "gmail_create_draft", "args": {"to": "recipient@example.com", "subject": "Subject", "body": "Body"}}
            ],
        )


@mcp.tool()
def gmail_send_email(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    send_now: bool = False,
) -> str | dict:
    """
    Create a draft for a new email, or send immediately only when send_now=True.

    Discovery: run `gmail_list_inbox` first to obtain `to` values.
    Safety: default behavior creates a draft. Use `gmail_send_draft` after review for the irreversible send step.

    Args:
        to: Recipient email address(es), comma-separated for multiple
        subject: Email subject line
        body: Email body text (plain text)
        cc: CC recipients, comma-separated (optional)
        bcc: BCC recipients, comma-separated (optional)
        send_now: If True, bypass draft review and send immediately. Default False.

    Use this for: migration compatibility when callers still use gmail_send_email.
    NOT for: the preferred two-step flow -> use `gmail_create_draft`, then `gmail_send_draft`.
    NOT for: replying in an existing thread with a message ID → see `gmail_reply_email`.
    NOT for: finding messages or message IDs before acting → see `gmail_search_emails`.
    NOT for: reading existing email content without sending → see `gmail_read_email`.
    """
    try:
        service = gmail_client.authenticate()

        message = gmail_client.create_message(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc
        )

        if not send_now:
            draft = gmail_client.create_draft(service, message)
            return (
                "Email draft created by gmail_send_email compatibility path.\n"
                f"Draft ID: {draft['id']}\n"
                "Review the draft, then call gmail_send_draft(draft_id) to send."
            )

        result = gmail_client.send_message(service, message)

        return f"Email sent successfully!\nMessage ID: {result['id']}\nThread ID: {result['threadId']}"
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=[{"name": "gmail_create_draft", "args": {"to": to, "subject": subject, "body": body}}])


@mcp.tool()
def gmail_create_reply_draft(
    message_id: str,
    body: str,
    reply_all: bool = False
) -> str | dict:
    """
    Create a draft reply in an existing email thread without sending it.

    Discovery: run `gmail_list_inbox` first to obtain `message_id` values.

    Args:
        message_id: The ID of the message to reply to
        body: Reply body text (plain text)
        reply_all: If True, reply to all recipients (default: False, reply only to sender)

    Use this for: safely composing a reply draft within an existing Gmail thread.
    NOT for: sending an already-created draft -> see `gmail_send_draft`.
    NOT for: composing a new standalone email with explicit recipients and subject -> see `gmail_create_draft`.
    NOT for: finding candidate messages or message IDs → see `gmail_search_emails`.
    NOT for: reading message content without replying → see `gmail_read_email`.
    """
    try:
        service = gmail_client.authenticate()

        # Get the original message
        original = gmail_client.get_message(service, message_id)

        # Build reply recipients.
        reply_to = original['from']
        reply_cc = None
        if reply_all:
            to_recipients = [original['from']]
            if original['to']:
                to_recipients.append(original['to'])
            reply_to = ', '.join(filter(None, to_recipients))
            reply_cc = original['cc'] or None

        # Build subject with Re: prefix if not already present
        subject = original['subject']
        if not subject.lower().startswith('re:'):
            subject = f"Re: {subject}"

        # Build References header (chain of Message-IDs)
        references = original.get('references', '')
        if original.get('message_id'):
            if references:
                references = f"{references} {original['message_id']}"
            else:
                references = original['message_id']

        message = gmail_client.create_message(
            to=reply_to,
            subject=subject,
            body=body,
            cc=reply_cc,
            thread_id=original['threadId'],
            in_reply_to=original.get('message_id'),
            references=references
        )

        draft = gmail_client.create_draft(service, message)

        return (
            "Reply draft created.\n"
            f"Draft ID: {draft['id']}\n"
            f"Thread ID: {original['threadId']}\n"
            "Review the draft, then call gmail_send_draft(draft_id) to send."
        )
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=_message_discovery_hint())


@mcp.tool()
def gmail_reply_email(
    message_id: str,
    body: str,
    reply_all: bool = False,
    send_now: bool = False,
) -> str | dict:
    """
    Create a reply draft, or send the reply immediately only when send_now=True.

    Discovery: run `gmail_list_inbox` first to obtain `message_id` values.
    Safety: default behavior creates a draft. Use `gmail_send_draft` after review for the irreversible send step.

    Args:
        message_id: The ID of the message to reply to
        body: Reply body text (plain text)
        reply_all: If True, reply to all recipients (default: False, reply only to sender)
        send_now: If True, bypass draft review and send immediately. Default False.

    Use this for: migration compatibility when callers still use gmail_reply_email.
    NOT for: the preferred two-step flow -> use `gmail_create_reply_draft`, then `gmail_send_draft`.
    NOT for: composing a new standalone email with explicit recipients and subject → see `gmail_send_email`.
    NOT for: finding candidate messages or message IDs → see `gmail_search_emails`.
    NOT for: reading message content without replying → see `gmail_read_email`.
    """
    try:
        service = gmail_client.authenticate()

        # Get the original message
        original = gmail_client.get_message(service, message_id)

        # Build reply recipients.
        reply_to = original['from']
        reply_cc = None
        if reply_all:
            to_recipients = [original['from']]
            if original['to']:
                to_recipients.append(original['to'])
            reply_to = ', '.join(filter(None, to_recipients))
            reply_cc = original['cc'] or None

        # Build subject with Re: prefix if not already present
        subject = original['subject']
        if not subject.lower().startswith('re:'):
            subject = f"Re: {subject}"

        # Build References header (chain of Message-IDs)
        references = original.get('references', '')
        if original.get('message_id'):
            if references:
                references = f"{references} {original['message_id']}"
            else:
                references = original['message_id']

        message = gmail_client.create_message(
            to=reply_to,
            subject=subject,
            body=body,
            cc=reply_cc,
            thread_id=original['threadId'],
            in_reply_to=original.get('message_id'),
            references=references
        )

        if not send_now:
            draft = gmail_client.create_draft(service, message)
            return (
                "Reply draft created by gmail_reply_email compatibility path.\n"
                f"Draft ID: {draft['id']}\n"
                f"Thread ID: {original['threadId']}\n"
                "Review the draft, then call gmail_send_draft(draft_id) to send."
            )

        result = gmail_client.send_message(service, message)

        return f"Reply sent successfully!\nMessage ID: {result['id']}\nThread ID: {result['threadId']}"
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=_message_discovery_hint())


@mcp.tool()
def gmail_manage_labels(
    message_id: str,
    add_labels: Optional[str] = None,
    remove_labels: Optional[str] = None
) -> str | dict:
    """
    Add or remove labels from an email.

    Args:
        message_id: The ID of the message to modify
        add_labels: Comma-separated labels to add (e.g., "STARRED,IMPORTANT" or "Work,Urgent")
        remove_labels: Comma-separated labels to remove (e.g., "INBOX,UNREAD")

    Common labels:
        - STARRED: Star the message
        - IMPORTANT: Mark as important
        - UNREAD: Mark as unread (remove to mark as read)
        - INBOX: In inbox (remove to archive)
        - TRASH: In trash
        - SPAM: In spam
    """
    try:
        service = gmail_client.authenticate()

        # Parse and resolve label names to IDs
        add_ids = []
        remove_ids = []

        if add_labels:
            for label_name in add_labels.split(','):
                label_name = label_name.strip()
                label_id = gmail_client.get_label_id(service, label_name)
                if label_id:
                    add_ids.append(label_id)
                else:
                    return _label_not_found(label_name)

        if remove_labels:
            for label_name in remove_labels.split(','):
                label_name = label_name.strip()
                label_id = gmail_client.get_label_id(service, label_name)
                if label_id:
                    remove_ids.append(label_id)
                else:
                    return _label_not_found(label_name)

        if not add_ids and not remove_ids:
            return ToolError(
                error_class="NoLabelsSpecified",
                message="No labels specified to add or remove.",
                names_correction={"add_labels": "Comma-separated Gmail label names", "remove_labels": "Comma-separated Gmail label names"},
                suggested_tool_calls=[{"name": "gmail_list_labels", "args": {}}],
            ).to_envelope()

        result = gmail_client.modify_labels(
            service,
            message_id,
            add_labels=add_ids if add_ids else None,
            remove_labels=remove_ids if remove_ids else None
        )

        actions = []
        if add_ids:
            actions.append(f"Added: {', '.join(add_ids)}")
        if remove_ids:
            actions.append(f"Removed: {', '.join(remove_ids)}")

        restore_token = _make_restore_token(
            {
                "operation": "gmail_manage_labels",
                "message_id": message_id,
                "undo_add_label_ids": remove_ids,
                "undo_remove_label_ids": add_ids,
            }
        )
        return {
            "status": "ok",
            "message": f"Labels updated successfully: {'; '.join(actions)}",
            "message_id": message_id,
            "current_labels": result.get("labelIds", []),
            "restore_token": restore_token,
            "undo_tool": "gmail_restore_email",
        }
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=_message_discovery_hint() + [{"name": "gmail_list_labels", "args": {}}])


@mcp.tool()
def gmail_delete_email(message_id: str, permanent: bool = False) -> str | dict:
    """
    Delete an email (move to trash or permanently delete).

    Args:
        message_id: The ID of the message to delete
        permanent: If True, permanently delete (cannot be undone). If False, move to trash (default)
    """
    try:
        service = gmail_client.authenticate()

        if permanent:
            gmail_client.delete_message(service, message_id)
            return {
                "status": "ok",
                "message": f"Message {message_id} permanently deleted. This cannot be undone.",
                "message_id": message_id,
                "undo_available": False,
                "restore_token": None,
            }
        else:
            result = gmail_client.trash_message(service, message_id)
            restore_token = _make_restore_token(
                {
                    "operation": "gmail_trash",
                    "message_id": message_id,
                }
            )
            return {
                "status": "ok",
                "message": f"Message {message_id} moved to trash.",
                "message_id": message_id,
                "labels": result.get("labelIds", []),
                "restore_token": restore_token,
                "undo_tool": "gmail_restore_email",
            }
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=_message_discovery_hint())


@mcp.tool()
def gmail_restore_email(restore_token: str) -> dict:
    """
    Undo gmail_delete_email trash moves or gmail_manage_labels changes.

    Args:
        restore_token: Token returned by gmail_delete_email or gmail_manage_labels
    """
    try:
        payload = _parse_restore_token(restore_token)
        service = gmail_client.authenticate()
        operation = payload.get("operation")
        message_id = str(payload.get("message_id") or "")
        if not message_id:
            raise ValueError("restore_token is missing message_id")

        if operation == "gmail_trash":
            result = gmail_client.untrash_message(service, message_id)
            return {
                "status": "ok",
                "operation": operation,
                "message": f"Message {message_id} restored from trash.",
                "message_id": message_id,
                "labels": result.get("labelIds", []),
            }

        if operation == "gmail_manage_labels":
            result = gmail_client.modify_labels(
                service,
                message_id,
                add_labels=list(payload.get("undo_add_label_ids") or []) or None,
                remove_labels=list(payload.get("undo_remove_label_ids") or []) or None,
            )
            return {
                "status": "ok",
                "operation": operation,
                "message": f"Label changes restored for message {message_id}.",
                "message_id": message_id,
                "labels": result.get("labelIds", []),
            }

        raise ValueError("restore_token operation is not supported by gmail_restore_email")
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=_message_discovery_hint())


# Main entry point
def main():
    mcp.run()


if __name__ == "__main__":
    main()
