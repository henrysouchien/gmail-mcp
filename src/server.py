"""
MCP Server for Gmail.
Provides tools for reading, sending, and managing emails.
"""

from typing import Optional
from mcp.server.fastmcp import FastMCP

from . import gmail_client

# Create the MCP server
mcp = FastMCP("gmail-mcp")


@mcp.tool()
def gmail_list_labels() -> str:
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
        return f"Error: {str(e)}"


@mcp.tool()
def gmail_list_inbox(
    max_results: int = 20,
    label: Optional[str] = None,
    unread_only: bool = False
) -> str:
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
            return f"Label '{label_name}' not found."

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
        return f"Error: {str(e)}"


@mcp.tool()
def gmail_search_emails(query: str, max_results: int = 20) -> str:
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
        return f"Error: {str(e)}"


@mcp.tool()
def gmail_read_email(message_id: str) -> str:
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
        return f"Error: {str(e)}"


@mcp.tool()
def gmail_send_email(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None
) -> str:
    """
    Compose and send a new email.

    Args:
        to: Recipient email address(es), comma-separated for multiple
        subject: Email subject line
        body: Email body text (plain text)
        cc: CC recipients, comma-separated (optional)
        bcc: BCC recipients, comma-separated (optional)
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

        result = gmail_client.send_message(service, message)

        return f"Email sent successfully!\nMessage ID: {result['id']}\nThread ID: {result['threadId']}"
    except Exception as e:
        return f"Error sending email: {str(e)}"


@mcp.tool()
def gmail_reply_email(
    message_id: str,
    body: str,
    reply_all: bool = False
) -> str:
    """
    Reply to an existing email thread.

    Args:
        message_id: The ID of the message to reply to
        body: Reply body text (plain text)
        reply_all: If True, reply to all recipients (default: False, reply only to sender)
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

        result = gmail_client.send_message(service, message)

        return f"Reply sent successfully!\nMessage ID: {result['id']}\nThread ID: {result['threadId']}"
    except Exception as e:
        return f"Error sending reply: {str(e)}"


@mcp.tool()
def gmail_manage_labels(
    message_id: str,
    add_labels: Optional[str] = None,
    remove_labels: Optional[str] = None
) -> str:
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
                    return f"Label not found: {label_name}"

        if remove_labels:
            for label_name in remove_labels.split(','):
                label_name = label_name.strip()
                label_id = gmail_client.get_label_id(service, label_name)
                if label_id:
                    remove_ids.append(label_id)
                else:
                    return f"Label not found: {label_name}"

        if not add_ids and not remove_ids:
            return "No labels specified to add or remove."

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

        return f"Labels updated successfully!\n{'; '.join(actions)}\nCurrent labels: {', '.join(result.get('labelIds', []))}"
    except Exception as e:
        return f"Error managing labels: {str(e)}"


@mcp.tool()
def gmail_delete_email(message_id: str, permanent: bool = False) -> str:
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
            return f"Message {message_id} permanently deleted. This cannot be undone."
        else:
            result = gmail_client.trash_message(service, message_id)
            return f"Message {message_id} moved to trash.\nLabels: {', '.join(result.get('labelIds', []))}"
    except Exception as e:
        return f"Error deleting message: {str(e)}"


# Main entry point
def main():
    mcp.run()


if __name__ == "__main__":
    main()
