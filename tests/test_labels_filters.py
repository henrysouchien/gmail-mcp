from __future__ import annotations

import pickle
from types import SimpleNamespace

from googleapiclient.errors import HttpError

from src import gmail_client, server


class _Response:
    def __init__(self, status: int):
        self.status = status
        self.reason = "fake error"


class _FakeHttpError(HttpError):
    def __init__(self, status: int, content: bytes | str = b"fake error"):
        if isinstance(content, str):
            content = content.encode("utf-8")
        super().__init__(_Response(status), content)


class _Execute:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class _LabelsResource:
    def __init__(
        self,
        calls,
        *,
        list_results=None,
        create_result=None,
        create_error=None,
    ):
        self.calls = calls
        self.list_results = list(list_results or [{"labels": []}])
        self.create_result = create_result or {"id": "Label_new", "name": "New"}
        self.create_error = create_error

    def list(self, **kwargs):
        self.calls.append(("labels.list", kwargs))
        result = self.list_results.pop(0) if len(self.list_results) > 1 else self.list_results[0]
        return _Execute(result)

    def create(self, **kwargs):
        self.calls.append(("labels.create", kwargs))
        return _Execute(self.create_result, self.create_error)


class _FiltersResource:
    def __init__(
        self,
        calls,
        *,
        list_result=None,
        create_result=None,
        get_result=None,
        get_error=None,
        delete_error=None,
    ):
        self.calls = calls
        self.list_result = list_result if list_result is not None else {}
        self.create_result = create_result or {"id": "filter-new"}
        self.get_result = get_result or {
            "id": "filter-1",
            "criteria": {"from": "sender@example.com"},
            "action": {"addLabelIds": ["STARRED"]},
        }
        self.get_error = get_error
        self.delete_error = delete_error

    def create(self, **kwargs):
        self.calls.append(("filters.create", kwargs))
        return _Execute(self.create_result)

    def list(self, **kwargs):
        self.calls.append(("filters.list", kwargs))
        return _Execute(self.list_result)

    def get(self, **kwargs):
        self.calls.append(("filters.get", kwargs))
        return _Execute(self.get_result, self.get_error)

    def delete(self, **kwargs):
        self.calls.append(("filters.delete", kwargs))
        return _Execute(None, self.delete_error)


class _SettingsResource:
    def __init__(self, filters):
        self._filters = filters

    def filters(self):
        return self._filters


class _UsersResource:
    def __init__(self, labels=None, filters=None):
        self._labels = labels
        self._settings = _SettingsResource(filters) if filters else None

    def labels(self):
        return self._labels

    def settings(self):
        return self._settings


class _FakeService:
    def __init__(self, *, labels=None, filters=None):
        self._users = _UsersResource(labels, filters)

    def users(self):
        return self._users


def _enable_filter_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        server.gmail_client,
        "token_scopes",
        lambda: [gmail_client.SETTINGS_SCOPE],
    )


def test_create_label_low_level_exact_payload() -> None:
    calls = []
    labels = _LabelsResource(
        calls,
        create_result={"id": "Label_1", "name": "Nested/Label"},
    )
    service = _FakeService(labels=labels)

    result = gmail_client.create_label(
        service,
        "Nested/Label",
        "hide",
        "labelShowIfUnread",
    )

    assert result == {"id": "Label_1", "name": "Nested/Label"}
    assert calls == [
        (
            "labels.create",
            {
                "userId": "me",
                "body": {
                    "name": "Nested/Label",
                    "messageListVisibility": "hide",
                    "labelListVisibility": "labelShowIfUnread",
                },
            },
        )
    ]


def test_resolve_or_create_label_existing_missing_and_409_race() -> None:
    existing_calls = []
    existing_service = _FakeService(
        labels=_LabelsResource(
            existing_calls,
            list_results=[
                {
                    "labels": [
                        {"id": "Label_1", "name": "Projects", "type": "user"}
                    ]
                }
            ],
        )
    )
    assert gmail_client.resolve_or_create_label(existing_service, "projects") == {
        "id": "Label_1",
        "name": "Projects",
        "created": False,
    }
    assert [name for name, _ in existing_calls] == ["labels.list"]

    missing_calls = []
    missing_service = _FakeService(
        labels=_LabelsResource(
            missing_calls,
            list_results=[{"labels": []}],
            create_result={"id": "Label_2", "name": "Parent/Child"},
        )
    )
    assert gmail_client.resolve_or_create_label(
        missing_service,
        "Parent/Child",
        "hide",
        "labelHide",
    ) == {"id": "Label_2", "name": "Parent/Child", "created": True}
    assert missing_calls[-1] == (
        "labels.create",
        {
            "userId": "me",
            "body": {
                "name": "Parent/Child",
                "messageListVisibility": "hide",
                "labelListVisibility": "labelHide",
            },
        },
    )

    race_calls = []
    race_service = _FakeService(
        labels=_LabelsResource(
            race_calls,
            list_results=[
                {"labels": []},
                {"labels": [{"id": "Label_3", "name": "Raced", "type": "user"}]},
            ],
            create_error=_FakeHttpError(409),
        )
    )
    assert gmail_client.resolve_or_create_label(race_service, "raced") == {
        "id": "Label_3",
        "name": "Raced",
        "created": False,
    }
    assert [name for name, _ in race_calls] == [
        "labels.list",
        "labels.create",
        "labels.list",
    ]


def test_gmail_create_label_validation_and_existing_visibility_message(monkeypatch) -> None:
    auth_calls = []
    monkeypatch.setattr(
        server.gmail_client,
        "authenticate",
        lambda: auth_calls.append("authenticate") or object(),
    )

    empty = server.gmail_create_label("   ")
    bad_message = server.gmail_create_label("Work", message_list_visibility="sometimes")
    bad_label = server.gmail_create_label("Work", label_list_visibility="sometimes")

    assert empty["error_class"] == "InvalidLabelName"
    assert bad_message["error_class"] == "InvalidVisibility"
    assert bad_label["error_class"] == "InvalidVisibility"
    assert auth_calls == []

    monkeypatch.setattr(
        server.gmail_client,
        "resolve_or_create_label",
        lambda service, name, message_visibility, label_visibility: {
            "id": "Label_1",
            "name": "Work",
            "created": False,
        },
    )
    result = server.gmail_create_label(" work ")
    assert result["status"] == "ok"
    assert result["name"] == "Work"
    assert result["visibility_applied"] is False
    assert "not applied" in result["message"]


def test_gmail_create_filter_happy_path_dedup_and_convenience_actions(monkeypatch) -> None:
    service = object()
    created_labels = []
    filter_calls = []
    _enable_filter_scope(monkeypatch)
    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)

    def get_label_id(_service, name):
        return {
            "existing": "Label_existing",
            "custom": "Label_remove",
            "inbox": "INBOX",
            "unread": "UNREAD",
            "spam": "SPAM",
            "important": "IMPORTANT",
            "starred": "STARRED",
        }.get(name.lower())

    monkeypatch.setattr(server.gmail_client, "get_label_id", get_label_id)

    def resolve(_service, name, *visibility):
        created = not created_labels
        created_labels.append(name)
        return {"id": "Label_new", "name": "New", "created": created}

    monkeypatch.setattr(server.gmail_client, "resolve_or_create_label", resolve)
    monkeypatch.setattr(server.gmail_client, "list_filters", lambda _service: [])
    monkeypatch.setattr(
        server.gmail_client,
        "create_filter",
        lambda _service, criteria, action: filter_calls.append((criteria, action))
        or {"id": "filter-1"},
    )

    result = server.gmail_create_filter(
        from_address="sender@example.com",
        to_address="me@example.com",
        subject="Notice",
        query="larger:1000",
        negated_query="label:ignore",
        has_attachment=True,
        exclude_chats=True,
        add_labels=" New, new , Existing,existing ",
        remove_labels=" Custom,custom ",
        skip_inbox=True,
        mark_read=True,
        never_spam=True,
        mark_important=True,
        star=True,
    )

    expected_criteria = {
        "from": "sender@example.com",
        "to": "me@example.com",
        "subject": "Notice",
        "query": "larger:1000",
        "negatedQuery": "label:ignore",
        "hasAttachment": True,
        "excludeChats": True,
    }
    expected_action = {
        "addLabelIds": ["Label_existing", "IMPORTANT", "STARRED", "Label_new"],
        "removeLabelIds": ["Label_remove", "INBOX", "UNREAD", "SPAM"],
    }
    assert result["status"] == "ok"
    assert result["filter_id"] == "filter-1"
    assert result["created"] is True
    assert result["criteria"] == expected_criteria
    assert result["action"] == expected_action
    assert result["resolved_labels"] == [
        {"name": "New", "id": "Label_new", "created": True},
        {"name": "New", "id": "Label_new", "created": False},
    ]
    assert created_labels == ["New", "new"]
    assert filter_calls == [(expected_criteria, expected_action)]


def test_gmail_create_filter_rejects_empty_criteria_or_action_without_mutation(monkeypatch) -> None:
    service = object()
    mutations = []
    _enable_filter_scope(monkeypatch)
    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(server.gmail_client, "get_label_id", lambda _service, name: None)
    monkeypatch.setattr(
        server.gmail_client,
        "resolve_or_create_label",
        lambda *args: mutations.append("label"),
    )
    monkeypatch.setattr(
        server.gmail_client,
        "create_filter",
        lambda *args: mutations.append("filter"),
    )

    no_criteria = server.gmail_create_filter(add_labels="New")
    no_action = server.gmail_create_filter(from_address="sender@example.com")

    assert no_criteria["error_class"] == "EmptyFilter"
    assert no_action["error_class"] == "EmptyFilter"
    assert mutations == []


def test_gmail_create_filter_missing_remove_label_creates_nothing(monkeypatch) -> None:
    mutations = []
    _enable_filter_scope(monkeypatch)
    monkeypatch.setattr(server.gmail_client, "authenticate", object)
    monkeypatch.setattr(server.gmail_client, "get_label_id", lambda service, name: None)
    monkeypatch.setattr(
        server.gmail_client,
        "resolve_or_create_label",
        lambda *args: mutations.append("label"),
    )
    monkeypatch.setattr(
        server.gmail_client,
        "create_filter",
        lambda *args: mutations.append("filter"),
    )

    result = server.gmail_create_filter(
        from_address="sender@example.com",
        add_labels="New",
        remove_labels="Missing",
    )

    assert result["error_class"] == "LabelNotFound"
    assert mutations == []


def test_gmail_create_filter_conflicting_system_and_user_labels_create_nothing(monkeypatch) -> None:
    mutations = []
    service = object()
    _enable_filter_scope(monkeypatch)
    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)

    def get_label_id(_service, name):
        return {"inbox": "INBOX", "projects": "Label_projects"}.get(name.lower())

    monkeypatch.setattr(server.gmail_client, "get_label_id", get_label_id)
    monkeypatch.setattr(
        server.gmail_client,
        "resolve_or_create_label",
        lambda *args: mutations.append("label"),
    )
    monkeypatch.setattr(
        server.gmail_client,
        "create_filter",
        lambda *args: mutations.append("filter"),
    )

    system = server.gmail_create_filter(
        from_address="sender@example.com",
        add_labels="INBOX",
        skip_inbox=True,
    )
    user = server.gmail_create_filter(
        from_address="sender@example.com",
        add_labels="projects",
        remove_labels="Projects",
    )

    assert system["error_class"] == "ConflictingLabels"
    assert user["error_class"] == "ConflictingLabels"
    assert mutations == []


def test_gmail_create_filter_scope_missing_precedes_mutation(monkeypatch) -> None:
    mutations = []
    monkeypatch.setattr(server.gmail_client, "authenticate", object)
    monkeypatch.setattr(server.gmail_client, "token_scopes", lambda: [])
    monkeypatch.setattr(
        server.gmail_client,
        "resolve_or_create_label",
        lambda *args: mutations.append("label"),
    )
    monkeypatch.setattr(
        server.gmail_client,
        "create_filter",
        lambda *args: mutations.append("filter"),
    )

    result = server.gmail_create_filter(
        from_address="sender@example.com",
        add_labels="New",
    )

    assert result["error_class"] == "ReauthRequired"
    assert str(gmail_client.TOKEN_FILE) in result["message"]
    assert mutations == []


def test_gmail_create_filter_duplicate_returns_existing_without_create(monkeypatch) -> None:
    service = object()
    create_calls = []
    _enable_filter_scope(monkeypatch)
    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(
        server.gmail_client,
        "get_label_id",
        lambda _service, name: {"work": "Label_1"}.get(name.lower()),
    )
    monkeypatch.setattr(
        server.gmail_client,
        "list_filters",
        lambda _service: [
            {
                "id": "filter-existing",
                "criteria": {"from": "sender@example.com"},
                "action": {"addLabelIds": ["STARRED", "Label_1"]},
            }
        ],
    )
    monkeypatch.setattr(
        server.gmail_client,
        "create_filter",
        lambda *args: create_calls.append(args),
    )

    result = server.gmail_create_filter(
        from_address="sender@example.com",
        add_labels="Work",
        star=True,
    )

    assert result["status"] == "ok"
    assert result["filter_id"] == "filter-existing"
    assert result["created"] is False
    assert create_calls == []


def test_gmail_list_filters_annotation_and_missing_scope(monkeypatch) -> None:
    service = object()
    _enable_filter_scope(monkeypatch)
    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(
        server.gmail_client,
        "get_labels",
        lambda _service: [
            {"id": "Label_1", "name": "Work"},
            {"id": "INBOX", "name": "INBOX"},
        ],
    )
    monkeypatch.setattr(
        server.gmail_client,
        "list_filters",
        lambda _service: [
            {
                "id": "filter-1",
                "criteria": {"subject": "Report"},
                "action": {
                    "addLabelIds": ["Label_1", "UNKNOWN"],
                    "removeLabelIds": ["INBOX"],
                },
            }
        ],
    )

    result = server.gmail_list_filters()
    assert result == {
        "status": "ok",
        "count": 1,
        "filters": [
            {
                "id": "filter-1",
                "criteria": {"subject": "Report"},
                "action": {
                    "addLabelIds": ["Label_1", "UNKNOWN"],
                    "removeLabelIds": ["INBOX"],
                },
                "action_label_names": {
                    "addLabelIds": ["Work", "UNKNOWN"],
                    "removeLabelIds": ["INBOX"],
                },
            }
        ],
    }

    monkeypatch.setattr(server.gmail_client, "token_scopes", lambda: [])
    missing = server.gmail_list_filters()
    assert missing["error_class"] == "ReauthRequired"


def test_gmail_delete_filter_dry_run_real_404_and_scope_missing(monkeypatch) -> None:
    service = object()
    definition = {
        "id": "filter-1",
        "criteria": {"from": "sender@example.com"},
        "action": {"addLabelIds": ["STARRED"]},
    }
    delete_calls = []
    _enable_filter_scope(monkeypatch)
    monkeypatch.setattr(server.gmail_client, "authenticate", lambda: service)
    monkeypatch.setattr(
        server.gmail_client,
        "get_filter",
        lambda _service, filter_id: definition,
    )
    monkeypatch.setattr(
        server.gmail_client,
        "delete_filter",
        lambda _service, filter_id: delete_calls.append(filter_id),
    )

    preview = server.gmail_delete_filter("filter-1", dry_run=True)
    assert preview["dry_run"] is True
    assert preview["filter"] == definition
    assert preview["restore_recipe"] == {
        "criteria": definition["criteria"],
        "action": definition["action"],
    }
    assert delete_calls == []

    deleted = server.gmail_delete_filter("filter-1")
    assert deleted["deleted_filter"] == definition
    assert deleted["restore_recipe"] == preview["restore_recipe"]
    assert delete_calls == ["filter-1"]

    monkeypatch.setattr(
        server.gmail_client,
        "get_filter",
        lambda *_args: (_ for _ in ()).throw(_FakeHttpError(404)),
    )
    missing = server.gmail_delete_filter("missing")
    assert missing["error_class"] == "FilterNotFound"
    assert missing["suggested_tool_calls"] == [
        {"name": "gmail_list_filters", "args": {}}
    ]

    monkeypatch.setattr(server.gmail_client, "token_scopes", lambda: [])
    scope_missing = server.gmail_delete_filter("filter-1")
    assert scope_missing["error_class"] == "ReauthRequired"


def test_filter_exception_envelope_only_reclassifies_scope_errors() -> None:
    scope_error = _FakeHttpError(
        403,
        b'{"error":{"status":"PERMISSION_DENIED","message":"insufficient scope"}}',
    )
    quota_error = _FakeHttpError(
        403,
        b'{"error":{"status":"RESOURCE_EXHAUSTED","message":"rateLimitExceeded"}}',
    )

    reauth = server._filter_exception_envelope(scope_error)
    quota = server._filter_exception_envelope(quota_error)

    assert reauth["error_class"] == "ReauthRequired"
    assert quota["error_class"] == "_FakeHttpError"
    assert "rateLimitExceeded" in quota["message"]


def test_token_scopes_uses_effective_token_file_and_scope_is_requested(
    monkeypatch,
    tmp_path,
) -> None:
    token_file = tmp_path / "effective-token.pickle"
    with token_file.open("wb") as handle:
        pickle.dump(SimpleNamespace(scopes=["scope-a", gmail_client.SETTINGS_SCOPE]), handle)
    monkeypatch.setattr(gmail_client, "TOKEN_FILE", token_file)

    assert gmail_client.token_scopes() == ["scope-a", gmail_client.SETTINGS_SCOPE]
    assert gmail_client.SETTINGS_SCOPE in gmail_client.SCOPES


def test_low_level_filter_helper_contracts() -> None:
    calls = []
    filters = _FiltersResource(
        calls,
        list_result={"filter": [{"id": "filter-1"}]},
        create_result={"id": "filter-new"},
        get_result={"id": "filter-1", "criteria": {}, "action": {}},
    )
    service = _FakeService(filters=filters)
    criteria = {"from": "sender@example.com"}
    action = {"addLabelIds": ["STARRED"]}

    assert gmail_client.create_filter(service, criteria, action) == {
        "id": "filter-new"
    }
    assert gmail_client.list_filters(service) == [{"id": "filter-1"}]
    filters.list_result = {}
    assert gmail_client.list_filters(service) == []
    assert gmail_client.get_filter(service, "filter-1") == {
        "id": "filter-1",
        "criteria": {},
        "action": {},
    }
    assert gmail_client.delete_filter(service, "filter-1") is None

    assert calls == [
        (
            "filters.create",
            {
                "userId": "me",
                "body": {"criteria": criteria, "action": action},
            },
        ),
        ("filters.list", {"userId": "me"}),
        ("filters.list", {"userId": "me"}),
        ("filters.get", {"userId": "me", "id": "filter-1"}),
        ("filters.delete", {"userId": "me", "id": "filter-1"}),
    ]
