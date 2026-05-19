from __future__ import annotations

from src import server


def test_gmail_create_draft_composes_without_sending(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    service = object()

    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(
        server.gmail_client,
        "create_message",
        lambda **kwargs: calls.append(("create_message", kwargs)) or {"raw": "encoded"},
    )
    monkeypatch.setattr(
        server.gmail_client,
        "create_draft",
        lambda svc, message: calls.append(("create_draft", (svc, message))) or {"id": "draft-1"},
    )
    monkeypatch.setattr(
        server.gmail_client,
        "send_message",
        lambda svc, message: calls.append(("send_message", (svc, message))) or {"id": "msg-1", "threadId": "thread-1"},
    )

    result = server.gmail_create_draft("a@example.com", "Subject", "Body")

    assert result["status"] == "draft_created"
    assert result["draft_id"] == "draft-1"
    assert result["confirm_token"].startswith("gmail:")
    assert result["next_actions"][0]["tool"] == "gmail_send_draft"
    assert [name for name, _ in calls] == ["create_message", "create_draft"]


def test_gmail_create_reply_draft_composes_without_sending(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    service = object()
    original = {
        "from": "sender@example.com",
        "to": "me@example.com",
        "cc": "",
        "subject": "Question",
        "threadId": "thread-1",
        "message_id": "<message-1@example.com>",
        "references": "",
    }

    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(server.gmail_client, "get_message", lambda svc, message_id: original)
    monkeypatch.setattr(
        server.gmail_client,
        "create_message",
        lambda **kwargs: calls.append(("create_message", kwargs)) or {"raw": "encoded", "threadId": "thread-1"},
    )
    monkeypatch.setattr(
        server.gmail_client,
        "create_draft",
        lambda svc, message: calls.append(("create_draft", (svc, message))) or {"id": "draft-reply-1"},
    )
    monkeypatch.setattr(
        server.gmail_client,
        "send_message",
        lambda svc, message: calls.append(("send_message", (svc, message))) or {"id": "msg-1", "threadId": "thread-1"},
    )

    result = server.gmail_create_reply_draft("message-1", "Thanks")

    assert result["status"] == "draft_created"
    assert result["draft_id"] == "draft-reply-1"
    assert result["thread_id"] == "thread-1"
    assert result["confirm_token"].startswith("gmail:")
    assert result["next_actions"][0]["tool"] == "gmail_send_draft"
    assert [name for name, _ in calls] == ["create_message", "create_draft"]


def test_gmail_send_draft_requires_confirm_token_before_sending(monkeypatch) -> None:
    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: (_ for _ in ()).throw(AssertionError("no send")))

    result = server.gmail_send_draft("draft-1")

    assert result["status"] == "confirmation_required"
    assert result["draft_id"] == "draft-1"
    assert result["confirm_token"].startswith("gmail:")
    assert result["next_actions"][0]["arguments"] == {
        "draft_id": "draft-1",
        "confirm_token": result["confirm_token"],
    }


def test_gmail_send_draft_sends_existing_draft_with_confirm_token(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    service = object()

    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(
        server.gmail_client,
        "send_draft",
        lambda svc, draft_id: calls.append(("send_draft", (svc, draft_id))) or {"id": "msg-1", "threadId": "thread-1"},
    )

    result = server.gmail_send_draft("draft-1", confirm_token=server._draft_confirm_token("draft-1"))

    assert "Draft sent successfully!" in result
    assert calls == [("send_draft", (service, "draft-1"))]


def test_gmail_send_draft_error_uses_structured_envelope(monkeypatch) -> None:
    service = object()

    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(
        server.gmail_client,
        "send_draft",
        lambda svc, draft_id: (_ for _ in ()).throw(RuntimeError("draft not found")),
    )

    result = server.gmail_send_draft(
        "missing-draft",
        confirm_token=server._draft_confirm_token("missing-draft"),
    )

    assert result["status"] == "error"
    assert result["error_class"] == "RuntimeError"
    assert result["message"] == "draft not found"
    assert "draft_id" in result["names_correction"]
    assert result["suggested_tool_calls"][0]["name"] == "gmail_create_draft"


def test_gmail_label_error_uses_structured_envelope(monkeypatch) -> None:
    service = object()

    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(server.gmail_client, "get_label_id", lambda svc, label: None)

    result = server.gmail_list_inbox(label="Missing")

    assert result == {
        "status": "error",
        "error_class": "LabelNotFound",
        "message": "Label not found: Missing",
        "names_correction": {"label": "Run gmail_list_labels and use one of the returned names."},
        "suggested_tool_calls": [{"name": "gmail_list_labels", "args": {}}],
    }


def test_gmail_delete_email_returns_restore_token_and_restore_untrashes(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    service = object()

    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(
        server.gmail_client,
        "trash_message",
        lambda svc, message_id: calls.append(("trash_message", (svc, message_id))) or {"labelIds": ["TRASH"]},
    )
    monkeypatch.setattr(
        server.gmail_client,
        "untrash_message",
        lambda svc, message_id: calls.append(("untrash_message", (svc, message_id))) or {"labelIds": ["INBOX"]},
    )

    result = server.gmail_delete_email("msg-1")

    assert result["status"] == "ok"
    assert result["undo_tool"] == "gmail_restore_email"
    assert result["restore_token"]
    assert calls == [("trash_message", (service, "msg-1"))]

    restored = server.gmail_restore_email(result["restore_token"])

    assert restored["status"] == "ok"
    assert restored["operation"] == "gmail_trash"
    assert restored["labels"] == ["INBOX"]
    assert calls == [
        ("trash_message", (service, "msg-1")),
        ("untrash_message", (service, "msg-1")),
    ]


def test_gmail_manage_labels_returns_restore_token_and_undo_swaps_labels(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    service = object()

    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(
        server.gmail_client,
        "get_label_id",
        lambda svc, label: {"Work": "Label_1", "STARRED": "STARRED"}.get(label),
    )

    def modify_labels(svc, message_id, add_labels=None, remove_labels=None):
        calls.append(
            (
                "modify_labels",
                {
                    "service": svc,
                    "message_id": message_id,
                    "add_labels": add_labels,
                    "remove_labels": remove_labels,
                },
            )
        )
        return {"labelIds": ["Label_1"]}

    monkeypatch.setattr(server.gmail_client, "modify_labels", modify_labels)

    result = server.gmail_manage_labels("msg-1", add_labels="Work", remove_labels="STARRED")

    assert result["status"] == "ok"
    assert result["undo_tool"] == "gmail_restore_email"
    assert result["restore_token"]
    assert calls == [
        (
            "modify_labels",
            {
                "service": service,
                "message_id": "msg-1",
                "add_labels": ["Label_1"],
                "remove_labels": ["STARRED"],
            },
        )
    ]

    restored = server.gmail_restore_email(result["restore_token"])

    assert restored["status"] == "ok"
    assert restored["operation"] == "gmail_manage_labels"
    assert calls[-1] == (
        "modify_labels",
        {
            "service": service,
            "message_id": "msg-1",
            "add_labels": ["STARRED"],
            "remove_labels": ["Label_1"],
        },
    )


def test_gmail_manage_labels_bulk_reports_partial_failures(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    service = object()

    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(
        server.gmail_client,
        "get_label_id",
        lambda svc, label: {"Work": "Label_1", "STARRED": "STARRED"}.get(label),
    )

    def modify_labels(svc, message_id, add_labels=None, remove_labels=None):
        calls.append(
            (
                "modify_labels",
                {
                    "service": svc,
                    "message_id": message_id,
                    "add_labels": add_labels,
                    "remove_labels": remove_labels,
                },
            )
        )
        return {"labelIds": add_labels or []}

    monkeypatch.setattr(server.gmail_client, "modify_labels", modify_labels)

    result = server.gmail_manage_labels_bulk(
        [
            {"message_id": "msg-1", "add_labels": "Work", "remove_labels": "STARRED"},
            {"message_id": "msg-2", "add_labels": "Missing"},
        ]
    )

    assert result["status"] == "partial_error"
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["results"][0]["status"] == "ok"
    assert result["results"][0]["result"]["undo_tool"] == "gmail_restore_email"
    assert result["results"][0]["result"]["restore_token"]
    assert result["results"][1]["status"] == "error"
    assert result["results"][1]["error_class"] == "LabelNotFound"
    assert calls == [
        (
            "modify_labels",
            {
                "service": service,
                "message_id": "msg-1",
                "add_labels": ["Label_1"],
                "remove_labels": ["STARRED"],
            },
        )
    ]
