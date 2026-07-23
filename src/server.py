"""
MCP Server for Gmail.
Provides tools for reading, sending, and managing emails.
"""

import base64
import hashlib
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


_SETTINGS_SCOPE = gmail_client.SETTINGS_SCOPE


def _reauth_required_envelope() -> dict[str, Any]:
    return ToolError(
        error_class="ReauthRequired",
        message=(
            f"Filter management requires the {_SETTINGS_SCOPE} OAuth scope, which the current "
            f"token lacks. Re-authenticate: back up and remove {gmail_client.TOKEN_FILE}, then "
            "re-run the OAuth flow (account hc@henrychien.com) to mint a token with all scopes."
        ),
        suggested_tool_calls=[],
    ).to_envelope()


def _settings_scope_ok() -> bool:
    return _SETTINGS_SCOPE in gmail_client.token_scopes()


def _filter_exception_envelope(exc: Exception) -> dict[str, Any]:
    content = getattr(exc, "content", None)
    text = ""
    if content:
        text = (
            content.decode("utf-8", "replace")
            if isinstance(content, bytes)
            else str(content)
        )
    text = (text + " " + str(exc)).lower()
    if (
        ("insufficient" in text and "scope" in text)
        or "access_token_scope_insufficient" in text
        or "insufficientpermissions" in text
    ):
        return _reauth_required_envelope()
    return _exception_envelope(exc)


def _bulk_result(action: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    failed = sum(1 for item in results if item.get("status") == "error")
    succeeded = len(results) - failed
    return {
        "status": "ok" if failed == 0 else "partial_error",
        "action": action,
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


def _draft_confirm_token(draft_id: str) -> str:
    digest = hashlib.sha256(f"gmail-send-draft:{draft_id}".encode("utf-8")).hexdigest()
    return f"gmail:{digest[:16]}"


def _draft_send_confirmation(draft_id: str) -> dict[str, Any]:
    confirm_token = _draft_confirm_token(draft_id)
    return {
        "status": "confirmation_required",
        "draft_id": draft_id,
        "confirm_token": confirm_token,
        "next_actions": [
            {
                "tool": "gmail_send_draft",
                "arguments": {"draft_id": draft_id, "confirm_token": confirm_token},
                "reason": "Send this draft after reviewing it.",
            }
        ],
    }


def _bulk_error(item: dict[str, Any], result: Any, *, error_class: str | None = None) -> dict[str, Any]:
    message = result.get("message") if isinstance(result, dict) else str(result)
    return {
        "status": "error",
        "item": item,
        "error_class": error_class or (result.get("error_class") if isinstance(result, dict) else "ToolError"),
        "message": message,
        "error_payload": result,
    }


def _bulk_dict_item(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {"value": value}


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

    Sibling tools: use gmail_list_inbox to list messages from a discovered
    label, and gmail_manage_labels/gmail_manage_labels_bulk to change labels.
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

    Discovery: use gmail_list_labels to find valid label names before passing
    a custom label.

    Sibling tools: use gmail_read_email with a returned message_id to inspect a
    message, or gmail_search_email when label browsing is too broad.
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

    Discovery: use gmail_list_inbox or gmail_search_email first and pass the
    returned message_id.
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
        confirmation = _draft_send_confirmation(draft["id"])
        return {
            "status": "draft_created",
            "draft_id": draft["id"],
            "message": "Email draft created. Review it, then call gmail_send_draft with confirm_token to send.",
            "confirm_token": confirmation["confirm_token"],
            "next_actions": confirmation["next_actions"],
        }
    except Exception as e:
        return _exception_envelope(e, suggested_tool_calls=[{"name": "gmail_create_draft", "args": {"to": to, "subject": subject, "body": body}}])


@mcp.tool()
def gmail_send_draft(draft_id: str, confirm_token: str | None = None) -> str | dict:
    """
    Send an existing Gmail draft by draft ID.

    Discovery: create or identify a draft first with `gmail_create_draft` or `gmail_create_reply_draft`;
    those tools return the required `confirm_token`.

    Args:
        draft_id: Gmail draft ID to send
        confirm_token: Confirmation token returned by `gmail_create_draft` or `gmail_create_reply_draft`

    Use this for: the final irreversible send step after draft review.
    NOT for: composing new message content -> see `gmail_create_draft`.
    NOT for: composing a reply draft -> see `gmail_create_reply_draft`.
    """
    try:
        if confirm_token != _draft_confirm_token(draft_id):
            return _draft_send_confirmation(draft_id)

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
        confirmation = _draft_send_confirmation(draft["id"])

        return {
            "status": "draft_created",
            "draft_id": draft["id"],
            "thread_id": original["threadId"],
            "message": "Reply draft created. Review it, then call gmail_send_draft with confirm_token to send.",
            "confirm_token": confirmation["confirm_token"],
            "next_actions": confirmation["next_actions"],
        }
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

    Discovery: use gmail_list_inbox or gmail_search_email first to find the
    message_id, and gmail_list_labels to verify custom label names.

    Sibling tools: use gmail_manage_labels_bulk for multiple messages.
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
def gmail_manage_labels_bulk(items: list[dict[str, Any]]) -> dict:
    """
    Add or remove labels on multiple emails with per-message results.

    Each item accepts message_id, add_labels, and remove_labels using the same label syntax as
    gmail_manage_labels. Successful items include their own restore_token for gmail_restore_email.
    """
    results: list[dict[str, Any]] = []
    for raw_item in items or []:
        item = _bulk_dict_item(raw_item)
        try:
            result = gmail_manage_labels(
                message_id=str(item.get("message_id") or ""),
                add_labels=item.get("add_labels"),
                remove_labels=item.get("remove_labels"),
            )
        except Exception as exc:
            results.append(
                _bulk_error(
                    item,
                    _exception_envelope(
                        exc,
                        suggested_tool_calls=_message_discovery_hint() + [{"name": "gmail_list_labels", "args": {}}],
                    ),
                    error_class=type(exc).__name__,
                )
            )
            continue

        if isinstance(result, dict) and result.get("status") == "ok":
            results.append({"status": "ok", "item": item, "result": result})
        else:
            results.append(_bulk_error(item, result))
    return _bulk_result("gmail_manage_labels_bulk", results)


@mcp.tool()
def gmail_delete_email(message_id: str, permanent: bool = False) -> str | dict:
    """
    Delete an email (move to trash or permanently delete).

    Args:
        message_id: The ID of the message to delete
        permanent: If True, permanently delete (cannot be undone). If False, move to trash (default)

    Discovery: use gmail_list_inbox, gmail_search_email, or gmail_read_email
    first to verify the message_id before deleting.

    Sibling tool: gmail_delete_filter deletes a filter definition, not a message.
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


@mcp.tool()
def gmail_create_label(
    name: str,
    message_list_visibility: str = "show",
    label_list_visibility: str = "labelShow",
) -> dict:
    """
    Idempotently create a Gmail label, including nested names such as Parent/Child.

    Visibility is applied only when a new label is created. Allowed
    message_list_visibility values are show and hide; allowed
    label_list_visibility values are labelShow, labelShowIfUnread, and labelHide.

    Discovery: use gmail_list_labels to inspect names before supplying required name.

    Sibling tools: use gmail_create_filter to apply the label automatically, or
    gmail_list_labels to inspect existing labels.
    """
    stripped_name = name.strip()
    if not stripped_name:
        return ToolError(
            error_class="InvalidLabelName",
            message="Label name must contain at least one non-whitespace character.",
            names_correction={"name": "Provide a non-empty Gmail label name."},
            suggested_tool_calls=[{"name": "gmail_list_labels", "args": {}}],
        ).to_envelope()
    if message_list_visibility not in gmail_client._MESSAGE_VISIBILITY:
        return ToolError(
            error_class="InvalidVisibility",
            message=(
                "message_list_visibility must be one of: "
                + ", ".join(sorted(gmail_client._MESSAGE_VISIBILITY))
            ),
            names_correction={
                "message_list_visibility": "Use show or hide.",
            },
            suggested_tool_calls=[],
        ).to_envelope()
    if label_list_visibility not in gmail_client._LABEL_VISIBILITY:
        return ToolError(
            error_class="InvalidVisibility",
            message=(
                "label_list_visibility must be one of: "
                + ", ".join(sorted(gmail_client._LABEL_VISIBILITY))
            ),
            names_correction={
                "label_list_visibility": (
                    "Use labelShow, labelShowIfUnread, or labelHide."
                ),
            },
            suggested_tool_calls=[],
        ).to_envelope()

    try:
        service = gmail_client.authenticate()
        result = gmail_client.resolve_or_create_label(
            service,
            stripped_name,
            message_list_visibility,
            label_list_visibility,
        )
        if result["created"]:
            message = f"Label {result['name']} created."
        else:
            message = (
                f"Label {result['name']} already existed; requested visibility "
                "was not applied to the pre-existing label."
            )
        return {
            "status": "ok",
            "label_id": result["id"],
            "name": result["name"],
            "created": result["created"],
            "visibility_applied": result["created"],
            "message": message,
        }
    except Exception as e:
        return _exception_envelope(
            e,
            suggested_tool_calls=[{"name": "gmail_list_labels", "args": {}}],
        )


@mcp.tool()
def gmail_create_filter(
    from_address: Optional[str] = None,
    to_address: Optional[str] = None,
    subject: Optional[str] = None,
    query: Optional[str] = None,
    negated_query: Optional[str] = None,
    has_attachment: bool = False,
    exclude_chats: bool = False,
    add_labels: Optional[str] = None,
    remove_labels: Optional[str] = None,
    skip_inbox: bool = False,
    mark_read: bool = False,
    never_spam: bool = False,
    mark_important: bool = False,
    star: bool = False,
) -> dict:
    """
    Create an idempotent Gmail filter from criteria and label actions.

    Label names in add_labels and remove_labels are comma-separated. Missing
    add labels are created after validation; remove labels must already exist.

    Sibling tools: use gmail_create_label to configure label visibility first,
    gmail_list_filters to inspect definitions, and gmail_delete_filter to remove one.
    """
    try:
        service = gmail_client.authenticate()
        if not _settings_scope_ok():
            return _reauth_required_envelope()

        criteria: dict[str, Any] = {}
        for key, value in (
            ("from", from_address),
            ("to", to_address),
            ("subject", subject),
            ("query", query),
            ("negatedQuery", negated_query),
        ):
            if value:
                criteria[key] = value
        if has_attachment:
            criteria["hasAttachment"] = True
        if exclude_chats:
            criteria["excludeChats"] = True

        remove_names = [
            token.strip()
            for token in (remove_labels or "").split(",")
            if token.strip()
        ]
        if skip_inbox:
            remove_names.append("INBOX")
        if mark_read:
            remove_names.append("UNREAD")
        if never_spam:
            remove_names.append("SPAM")

        remove_ids: list[str] = []
        remove_id_names: dict[str, str] = {}
        for label_name in remove_names:
            label_id = gmail_client.get_label_id(service, label_name)
            if not label_id:
                return _label_not_found(label_name)
            remove_ids.append(label_id)
            remove_id_names.setdefault(label_id, label_name)

        add_names = [
            token.strip()
            for token in (add_labels or "").split(",")
            if token.strip()
        ]
        if mark_important:
            add_names.append("IMPORTANT")
        if star:
            add_names.append("STARRED")

        add_ids: list[str] = []
        add_id_names: dict[str, str] = {}
        missing_add_names: list[str] = []
        for label_name in add_names:
            label_id = gmail_client.get_label_id(service, label_name)
            if label_id:
                add_ids.append(label_id)
                add_id_names.setdefault(label_id, label_name)
            else:
                missing_add_names.append(label_name)

        if not criteria or (not add_names and not remove_names):
            return ToolError(
                error_class="EmptyFilter",
                message="A filter requires at least one criterion and one action.",
                names_correction={
                    "criteria": "Provide a sender, recipient, subject, query, or criteria flag.",
                    "action": "Provide a label action or convenience action flag.",
                },
                suggested_tool_calls=[{"name": "gmail_list_filters", "args": {}}],
            ).to_envelope()

        conflict_ids = set(add_ids).intersection(remove_ids)
        if conflict_ids:
            conflict_id = next(
                label_id for label_id in add_ids if label_id in conflict_ids
            )
            offender = add_id_names.get(
                conflict_id,
                remove_id_names.get(conflict_id, conflict_id),
            )
            return ToolError(
                error_class="ConflictingLabels",
                message=(
                    f"Label {offender} resolves to {conflict_id} and cannot be "
                    "both added and removed by the same filter."
                ),
                names_correction={
                    "add_labels": "Remove the label from either add_labels or remove_labels.",
                },
                suggested_tool_calls=[{"name": "gmail_list_labels", "args": {}}],
            ).to_envelope()

        resolved_labels: list[dict[str, Any]] = []
        for label_name in missing_add_names:
            resolved = gmail_client.resolve_or_create_label(service, label_name)
            add_ids.append(resolved["id"])
            resolved_labels.append(
                {
                    "name": resolved["name"],
                    "id": resolved["id"],
                    "created": resolved["created"],
                }
            )

        add_ids = list(dict.fromkeys(add_ids))
        remove_ids = list(dict.fromkeys(remove_ids))
        action: dict[str, list[str]] = {}
        if add_ids:
            action["addLabelIds"] = add_ids
        if remove_ids:
            action["removeLabelIds"] = remove_ids

        def canonical(candidate: dict[str, Any]) -> tuple[dict, dict]:
            candidate_action = candidate.get("action", {})
            return (
                candidate.get("criteria", {}),
                {
                    key: sorted(candidate_action[key])
                    for key in ("addLabelIds", "removeLabelIds")
                    if candidate_action.get(key)
                },
            )

        wanted = canonical({"criteria": criteria, "action": action})
        for existing in gmail_client.list_filters(service):
            if canonical(existing) == wanted:
                return {
                    "status": "ok",
                    "filter_id": existing["id"],
                    "created": False,
                    "criteria": criteria,
                    "action": action,
                    "resolved_labels": resolved_labels,
                    "message": f"Equivalent filter {existing['id']} already exists.",
                }

        made = gmail_client.create_filter(service, criteria, action)
        return {
            "status": "ok",
            "filter_id": made["id"],
            "created": True,
            "criteria": criteria,
            "action": action,
            "resolved_labels": resolved_labels,
            "message": f"Filter {made['id']} created.",
        }
    except Exception as e:
        return _filter_exception_envelope(e)


@mcp.tool()
def gmail_list_filters() -> dict:
    """
    List Gmail filters and annotate their action label IDs with label names.

    Sibling tools: use gmail_create_filter to add a definition and
    gmail_delete_filter to preview or delete a discovered filter. Use
    gmail_list_labels for label names and gmail_list_inbox for matching messages.
    """
    try:
        service = gmail_client.authenticate()
        if not _settings_scope_ok():
            return _reauth_required_envelope()

        raw_filters = gmail_client.list_filters(service)
        labels = gmail_client.get_labels(service)
        label_names = {label["id"]: label["name"] for label in labels}
        filters = []
        for item in raw_filters:
            action = item.get("action", {})
            filters.append(
                {
                    "id": item["id"],
                    "criteria": item.get("criteria", {}),
                    "action": action,
                    "action_label_names": {
                        "addLabelIds": [
                            label_names.get(label_id, label_id)
                            for label_id in action.get("addLabelIds", [])
                        ],
                        "removeLabelIds": [
                            label_names.get(label_id, label_id)
                            for label_id in action.get("removeLabelIds", [])
                        ],
                    },
                }
            )
        return {"status": "ok", "count": len(filters), "filters": filters}
    except Exception as e:
        return _filter_exception_envelope(e)


@mcp.tool()
def gmail_delete_filter(filter_id: str, dry_run: bool = False) -> dict:
    """
    Preview or delete a Gmail filter while preserving its raw restore recipe.

    Discovery: use gmail_list_filters to find and verify the required filter_id.

    The restore recipe must be mapped to gmail_create_filter arguments; raw keys
    such as from and addLabelIds are not accepted directly. Sibling destructive
    tool gmail_delete_email deletes messages, not filter definitions.
    """
    try:
        service = gmail_client.authenticate()
        if not _settings_scope_ok():
            return _reauth_required_envelope()

        try:
            existing = gmail_client.get_filter(service, filter_id)
        except Exception as e:
            if (
                getattr(getattr(e, "resp", None), "status", None) == 404
            ):
                return ToolError(
                    error_class="FilterNotFound",
                    message=f"Filter not found: {filter_id}",
                    names_correction={
                        "filter_id": "Run gmail_list_filters and use a returned filter ID.",
                    },
                    suggested_tool_calls=[{"name": "gmail_list_filters", "args": {}}],
                ).to_envelope()
            raise

        restore_recipe = {
            "criteria": existing.get("criteria", {}),
            "action": existing.get("action", {}),
        }
        if dry_run:
            return {
                "status": "ok",
                "dry_run": True,
                "filter": existing,
                "would_delete": filter_id,
                "restore_recipe": restore_recipe,
            }

        try:
            gmail_client.delete_filter(service, filter_id)
        except Exception as e:
            if (
                getattr(getattr(e, "resp", None), "status", None) == 404
            ):
                return ToolError(
                    error_class="FilterNotFound",
                    message=f"Filter not found: {filter_id}",
                    names_correction={
                        "filter_id": "Run gmail_list_filters and use a returned filter ID.",
                    },
                    suggested_tool_calls=[{"name": "gmail_list_filters", "args": {}}],
                ).to_envelope()
            raise

        return {
            "status": "ok",
            "filter_id": filter_id,
            "deleted_filter": existing,
            "restore_recipe": restore_recipe,
            "message": (
                "Filter deleted. To recreate it, map raw Gmail restore_recipe "
                "fields to gmail_create_filter arguments (for example, from to "
                "from_address and label IDs to label names)."
            ),
        }
    except Exception as e:
        return _filter_exception_envelope(e)


# Main entry point
def main():
    mcp.run()


if __name__ == "__main__":
    main()
