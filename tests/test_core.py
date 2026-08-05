"""Offline tests for exchange-mcp core logic (no Exchange server required)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from exchangelib import FileAttachment, Q
from exchangelib.attachments import AttachmentId
from exchangelib.errors import ErrorAccessDenied, ErrorItemNotFound, TransportError
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout
from exchangelib.version import EXCHANGE_2013, Version

from exchange_mcp.config import Settings, get_settings
from exchange_mcp.errors import APIError
from exchange_mcp.ews.email import _validate_inline_images
from exchange_mcp.exchange_client import EWSExchangeBackend, ExchangeClient
from exchange_mcp.models import (
    CalendarEvent,
    CompleteTaskRequest,
    CreateEventRequest,
    CreateTaskRequest,
    DeleteContactRequest,
    EmailAddress,
    FindFreeSlotsRequest,
    InlineImage,
    ListEmailsRequest,
    ListEventsRequest,
    ListTasksRequest,
    MarkEmailRequest,
    SearchEmailsRequest,
    SendEmailRequest,
    SendResult,
    UpdateContactRequest,
    UpdateEventRequest,
    UpdateTaskRequest,
)
from exchange_mcp.server import build_mcp_server


@pytest.fixture()
def settings(monkeypatch):
    monkeypatch.setenv("EXCHANGE_SERVER", "https://mail.example.com")
    monkeypatch.setenv("EXCHANGE_USERNAME", "DOMAIN\\user")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")
    monkeypatch.setenv("EXCHANGE_EMAIL_ADDRESS", "u@example.com")
    get_settings.cache_clear()
    return Settings()


@pytest.fixture()
def backend(settings):
    backend = EWSExchangeBackend(settings)
    backend._account = SimpleNamespace(
        default_timezone=UTC, calendar="cal", contacts="contacts", drafts="drafts",
        protocol=None, tasks="tasks",
    )
    return backend


class FakeItem:
    def __init__(self):
        self.subject = "s"
        self.saved_with = None
        self.deleted = None
        self.trashed = False
        self.required_attendees = [
            SimpleNamespace(mailbox=SimpleNamespace(email_address="keep@x.com")),
            SimpleNamespace(mailbox=SimpleNamespace(email_address="drop@x.com")),
        ]

    def save(self, update_fields=None, send_meeting_invitations=None):
        self.saved_with = (update_fields, send_meeting_invitations)

    def delete(self, **kwargs):
        self.deleted = kwargs

    def move_to_trash(self):
        self.trashed = True


class FakeQS:
    def __init__(self):
        self.filters = []
        self.only_fields = None

    def order_by(self, *_):
        return self

    def only(self, *fields):
        self.only_fields = fields
        return self

    def filter(self, *args, **kwargs):
        self.filters.append((args, kwargs))
        return self

    def __getitem__(self, _):
        return []


class FakeFolder:
    def __init__(self):
        self.qs = FakeQS()

    def all(self):
        return self.qs

    def filter(self, *args, **kwargs):
        return self.qs.filter(*args, **kwargs)


# --- request models -------------------------------------------------------


def test_update_contact_request_all_optional():
    request = UpdateContactRequest(id="x", phone="123")
    assert request.display_name is None and request.phone == "123"


def test_create_event_all_day_clamp_midnight_exclusive():
    request = CreateEventRequest(
        subject="s",
        start=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        end=datetime(2026, 8, 1, 17, 0, tzinfo=UTC),
        is_all_day=True,
    )
    assert request.start == datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    assert request.end == datetime(2026, 8, 2, 0, 0, tzinfo=UTC)


# --- MCP schema / server wiring -------------------------------------------


def _tools(server):
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def test_optional_params_not_required_in_schema(settings):
    tools = _tools(build_mcp_server(settings=settings, client=object()))
    assert tools["send_email"].inputSchema["required"] == ["to", "subject", "body"]
    assert tools["create_event"].inputSchema["required"] == ["subject", "start", "end"]
    assert tools["update_event"].inputSchema["required"] == ["id"]
    assert tools["update_contact"].inputSchema["required"] == ["id"]


def test_build_mcp_server_resolves_settings(settings):
    server = build_mcp_server()  # must not crash without explicit settings
    assert "send_email" in _tools(server)


def test_send_email_without_optionals_reaches_backend(settings):
    class FakeClient:
        def __getattr__(self, _):
            return lambda request=None: SendResult(id="fake", status="sent")

    FakeClient.settings = settings
    server = build_mcp_server(settings=settings, client=FakeClient())
    result, _ = asyncio.run(
        server.call_tool("send_email", {"to": ["a@b.com"], "subject": "s", "body": "b"})
    )
    assert json.loads(result[0].text)["status"] == "sent"


# --- AQS search ------------------------------------------------------------


@pytest.mark.parametrize(
    "req",
    [
        ListEmailsRequest(from_address="Boss@corp.com"),
        ListEmailsRequest(
            from_address="b@c.com",
            subject='quarterly "report"',
            since=date(2026, 1, 1),
            before=date(2026, 2, 1),
            unread_only=True,
            has_attachments=True,
        ),
        ListEmailsRequest(from_address="b@c.com", since=date(2026, 1, 1)),
        ListEmailsRequest(from_address="b@c.com", before=date(2026, 2, 1), has_attachments=False),
    ],
)
def test_aqs_list_query_compiles(req):
    Q(EWSExchangeBackend._aqs_list_query(req)).clean(Version(EXCHANGE_2013))


def test_aqs_list_query_content():
    request = ListEmailsRequest(
        from_address="b@c.com",
        subject="report",
        since=date(2026, 1, 1),
        before=date(2026, 2, 1),
        unread_only=True,
        has_attachments=True,
    )
    aqs = EWSExchangeBackend._aqs_list_query(request)
    assert aqs == (
        'from:"b@c.com" AND subject:"report" AND received:2026-01-01..2026-02-01'
        " AND isread:false AND hasattachment:true"
    )


def test_list_emails_from_address_uses_aqs(backend):
    folder = FakeFolder()
    backend._resolve_folder = lambda _=None: folder
    backend.list_emails(ListEmailsRequest(from_address="a@b.com", unread_only=True))
    assert folder.qs.filters[0][0] == ('from:"a@b.com" AND isread:false',)


def test_list_emails_restriction_path(backend):
    folder = FakeFolder()
    backend._resolve_folder = lambda _=None: folder
    backend.list_emails(ListEmailsRequest(subject="x", unread_only=True, since=date(2026, 1, 1)))
    _, kwargs = folder.qs.filters[0]
    assert kwargs["subject__icontains"] == "x"
    assert kwargs["is_read"] is False
    assert "datetime_received__gte" in kwargs


def test_search_emails_passes_query_through(backend):
    folder = FakeFolder()
    backend._resolve_folder = lambda _=None: folder
    backend.search_emails(SearchEmailsRequest(query="from:boss@corp.com AND hasattachment:true", folder="inbox"))
    assert folder.qs.filters[0][0] == ("from:boss@corp.com AND hasattachment:true",)


def test_list_emails_restricts_fields(backend):
    folder = FakeFolder()
    backend._resolve_folder = lambda _=None: folder
    backend.list_emails(ListEmailsRequest())
    assert folder.qs.only_fields is not None
    assert "text_body" in folder.qs.only_fields
    assert "body" not in folder.qs.only_fields
    assert "mime_content" not in folder.qs.only_fields


def test_search_emails_restricts_fields(backend):
    folder = FakeFolder()
    backend._resolve_folder = lambda _=None: folder
    backend.search_emails(SearchEmailsRequest(query="q", folder="inbox"))
    assert folder.qs.only_fields is not None
    assert "body" not in folder.qs.only_fields


def test_list_tasks_restricts_fields(backend):
    folder = FakeTasksFolder()
    backend._account.tasks = folder
    backend.list_tasks(ListTasksRequest())
    assert folder.qs.only_fields is not None
    assert "body" not in folder.qs.only_fields


def test_search_emails_maps_errors(backend):
    class BoomFolder(FakeFolder):
        def filter(self, *args, **kwargs):
            raise ErrorAccessDenied("Access is denied.")

    backend._resolve_folder = lambda _=None: BoomFolder()
    with pytest.raises(APIError) as excinfo:
        backend.search_emails(SearchEmailsRequest(query="q", folder="inbox"))
    assert excinfo.value.code == "permission_denied"


# --- update_fields name fixes ----------------------------------------------


def test_mark_email_uses_ews_field_names(backend):
    item = FakeItem()
    backend._fetch_item = lambda _id, folder=None: item
    backend.mark_email(MarkEmailRequest(id="x", read=True, importance="high", flag="flagged"))
    assert item.saved_with[0] == ["is_read", "importance", "categories"]


def test_mark_email_noop_skips_save(backend):
    item = FakeItem()
    backend._fetch_item = lambda _id, folder=None: item
    result = backend.mark_email(MarkEmailRequest(id="x"))
    assert result.updated_fields == [] and item.saved_with is None


def test_update_event_uses_ews_field_names(backend):
    item = FakeItem()
    backend._fetch_item = lambda _id, folder=None: item
    backend.update_event(
        UpdateEventRequest(id="x", reminder_minutes=30, add_attendees=["new@x.com"], remove_attendees=["drop@x.com"])
    )
    assert item.saved_with[0] == ["reminder_minutes_before_start", "required_attendees"]
    assert item.reminder_minutes_before_start == 30
    assert [a.mailbox.email_address for a in item.required_attendees] == ["keep@x.com", "new@x.com"]


def test_update_contact_uses_ews_field_names(backend):
    item = FakeItem()
    backend._fetch_item = lambda _id, folder=None: item
    backend.update_contact(UpdateContactRequest(id="x", first_name="F", email="e@x.com"))
    assert item.saved_with[0] == ["given_name", "email_addresses"]
    assert item.given_name == "F"


def test_delete_contact_moves_to_trash(backend):
    item = FakeItem()
    backend._fetch_item = lambda _id, folder=None: item
    backend.delete_contact(DeleteContactRequest(id="x"))
    assert item.trashed and item.deleted is None


# --- error mapping -----------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ErrorAccessDenied("Access is denied."), "permission_denied"),
        (ErrorItemNotFound("The specified object was not found in the store."), "not_found"),
        (TransportError("connection timed out"), "timeout"),
        (TransportError("connection reset"), "exchange_unavailable"),
        (ReadTimeout("HTTPSConnectionPool(host='mail.example.com', port=443): Read timed out. (read timeout=30)"), "timeout"),
        (RequestsConnectionError("Connection aborted."), "exchange_unavailable"),
    ],
)
def test_map_exception(backend, exc, code):
    assert backend._map_exception(exc, item_id="i1").code == code


@pytest.mark.parametrize(
    "exc",
    [
        ReadTimeout("Read timed out. (read timeout=30)"),
        RequestsConnectionError("Connection aborted."),
        TransportError("connection timed out"),
        TransportError("connection reset by peer"),
    ],
)
def test_map_exception_invalidates_account_on_connection_failures(backend, exc):
    assert backend._account is not None
    backend._map_exception(exc)
    assert backend._account is None


def test_map_exception_keeps_account_on_logical_errors(backend):
    backend._map_exception(ErrorAccessDenied("Access is denied."))
    assert backend._account is not None


def test_exchange_version_wiring(backend):
    backend.settings.exchange_version = "EXCHANGE_2016"
    assert backend._exchange_version().api_version == "Exchange2016"
    backend.settings.exchange_version = "exchange2016"  # case-insensitive
    assert backend._exchange_version().api_version == "Exchange2016"
    backend.settings.exchange_version = "EXCHANGE_2099"
    with pytest.raises(APIError) as excinfo:
        backend._exchange_version()
    assert excinfo.value.code == "validation_error"
    backend.settings.exchange_version = None
    assert backend._exchange_version() is None


# --- availability ------------------------------------------------------------


def _event(start, end):
    return CalendarEvent(
        id="x", subject="s", start=start, end=end, organizer=EmailAddress(email="o@x.com")
    )


def test_get_my_availability_computes_free_slots(backend):
    backend.list_events = lambda _request: [
        _event(datetime(2026, 8, 3, 10, 0, tzinfo=UTC), datetime(2026, 8, 3, 11, 0, tzinfo=UTC)),
        _event(datetime(2026, 8, 3, 13, 0, tzinfo=UTC), datetime(2026, 8, 3, 14, 0, tzinfo=UTC)),
    ]
    request = ListEventsRequest(
        start=datetime(2026, 8, 3, 9, 0, tzinfo=UTC), end=datetime(2026, 8, 3, 15, 0, tzinfo=UTC)
    )
    result = backend.get_my_availability(request)
    assert len(result.busy_slots) == 2
    assert [(slot.start.hour, slot.end.hour) for slot in result.free_slots] == [(9, 10), (11, 13), (14, 15)]


def test_find_free_slots_checks_all_attendees(backend):
    class FakeProtocol:
        def get_free_busy_info(self, accounts, **_):
            assert accounts == [("a@x.com", "Required", False), ("b@x.com", "Required", False)]
            return [SimpleNamespace(merged="00"), SimpleNamespace(merged="01")]

    backend._account.protocol = FakeProtocol()
    request = FindFreeSlotsRequest(
        attendees=["a@x.com", "b@x.com"],
        duration=30,
        start=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
        end=datetime(2026, 8, 3, 10, 0, tzinfo=UTC),
    )
    slots = backend.find_free_slots(request)
    # second slot has attendee b busy -> only the first slot is free for everyone
    assert [(slot.start.hour, slot.start.minute) for slot in slots] == [(9, 0)]


def test_find_free_slots_work_hours(backend):
    class FakeProtocol:
        def get_free_busy_info(self, **_):
            return [SimpleNamespace(merged="000")]

    backend._account.protocol = FakeProtocol()
    request = FindFreeSlotsRequest(
        attendees=["a@x.com"],
        duration=30,
        start=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
        end=datetime(2026, 8, 3, 10, 30, tzinfo=UTC),
        work_hours={"start": "09:30", "end": "18:00"},
    )
    slots = backend.find_free_slots(request)
    assert [(slot.start.hour, slot.start.minute) for slot in slots] == [(9, 30), (10, 0)]


# --- attachments --------------------------------------------------------------


def _item_with_attachment(attachment):
    return SimpleNamespace(attachments=[attachment])


def test_get_attachment_sanitizes_filename(backend, tmp_path):
    attachment = FileAttachment(name="../../evil.txt", content=b"hello")
    attachment.attachment_id = AttachmentId(id="att-1")
    backend._fetch_item = lambda _id, folder=None: _item_with_attachment(attachment)
    from exchange_mcp.models import GetAttachmentRequest

    result = backend.get_attachment(
        GetAttachmentRequest(email_id="m1", attachment_id="att-1", save_path=tmp_path)
    )
    assert result.filename == "evil.txt"
    assert (tmp_path / "evil.txt").read_bytes() == b"hello"


def test_get_attachment_size_cap(backend, tmp_path):
    backend.settings.attachment_max_size_mb = 1
    attachment = FileAttachment(name="big.bin", content=b"x" * (2 * 1024 * 1024))
    attachment.attachment_id = AttachmentId(id="att-1")
    backend._fetch_item = lambda _id, folder=None: _item_with_attachment(attachment)
    from exchange_mcp.models import GetAttachmentRequest

    with pytest.raises(APIError) as excinfo:
        backend.get_attachment(GetAttachmentRequest(email_id="m1", attachment_id="att-1", save_path=tmp_path))
    assert excinfo.value.code == "validation_error"


def test_get_attachment_rejects_embedded_item(backend, tmp_path):
    attachment = SimpleNamespace(
        attachment_id=SimpleNamespace(id="att-1"), name="msg", size=None, content_type="message/rfc822"
    )
    backend._fetch_item = lambda _id, folder=None: _item_with_attachment(attachment)
    from exchange_mcp.models import GetAttachmentRequest

    with pytest.raises(APIError) as excinfo:
        backend.get_attachment(GetAttachmentRequest(email_id="m1", attachment_id="att-1", save_path=tmp_path))
    assert "not a file" in excinfo.value.message


# --- config --------------------------------------------------------------------


def test_log_level_case_insensitive(monkeypatch):
    monkeypatch.setenv("EXCHANGE_SERVER", "https://mail.example.com")
    monkeypatch.setenv("EXCHANGE_USERNAME", "u@x.com")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "x")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert Settings().log_level == "DEBUG"


def test_exchange_client_requires_backend(settings):
    client = ExchangeClient(settings=settings, backend=object())
    assert client.backend is not None


# --- inline images -------------------------------------------------------------


def test_make_message_attaches_inline_images(backend, tmp_path):
    from exchangelib import FileAttachment

    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n")

    class CollectingMessage:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self._attached = []

        def attach(self, attachment):
            self._attached.append(attachment)

    import exchange_mcp.exchange_client as ec

    OrigMessage = ec.Message
    ec.Message = CollectingMessage
    try:
        request = SendEmailRequest(
            to=["a@b.com"],
            subject="s",
            body='<img src="cid:logo">',
            body_type="html",
            inline_images=[InlineImage(path=logo, content_id="logo")],
        )
        message = backend._make_message(request)
    finally:
        ec.Message = OrigMessage

    inline = [a for a in message._attached if isinstance(a, FileAttachment)]
    assert len(inline) == 1
    assert inline[0].is_inline is True
    assert inline[0].content_id == "logo"
    assert inline[0].content_type == "image/png"
    assert inline[0].name == "logo.png"


def test_validate_inline_images_missing_path(tmp_path):
    missing = tmp_path / "nope.png"
    with pytest.raises(APIError) as excinfo:
        _validate_inline_images([InlineImage(path=missing, content_id="logo")], max_size_mb=10)
    assert excinfo.value.code == "validation_error"
    assert "inline_images" in str(excinfo.value.details)


def test_validate_inline_images_size_cap(tmp_path):
    image = tmp_path / "big.png"
    image.write_bytes(b"x" * (11 * 1024 * 1024))
    with pytest.raises(APIError) as excinfo:
        _validate_inline_images([InlineImage(path=image, content_id="logo")], max_size_mb=10)
    assert excinfo.value.code == "validation_error"


def test_send_email_optional_params_not_required_in_schema(settings):
    tools = _tools(build_mcp_server(settings=settings, client=object()))
    assert "inline_images" not in tools["send_email"].inputSchema["required"]
    assert "inline_images" not in tools["create_draft"].inputSchema["required"]


# --- tasks ---------------------------------------------------------------------


class FakeTask:
    def __init__(self):
        self.id = "t1"
        self.subject = "original"
        self.body = None
        self.status = "NotStarted"
        self.percent_complete = 0
        self.due_date = None
        self.start_date = None
        self.complete_date = None
        self.reminder_is_set = False
        self.reminder_minutes_before_start = 0
        self.categories = []
        self.importance = "Normal"
        self.companies = []
        self.contacts = []
        self.billing_information = None
        self.owner = None
        self.last_modified_time = None
        self.is_complete = False
        self.has_attachments = False
        self.saved_with = None
        self.deleted = None
        self.trashed = False
        self.text_body = None

    def save(self, update_fields=None):
        self.saved_with = update_fields

    def delete(self, **kwargs):
        self.deleted = kwargs

    def move_to_trash(self):
        self.trashed = True


class FakeTaskQS:
    def __init__(self):
        self.filters = []
        self.only_fields = None

    def all(self):
        return self

    def only(self, *fields):
        self.only_fields = fields
        return self

    def order_by(self, *_):
        return self

    def filter(self, **kwargs):
        self.filters.append(kwargs)
        return self

    def __getitem__(self, _):
        return []


class FakeTasksFolder:
    def __init__(self):
        self.qs = FakeTaskQS()

    def all(self):
        return self.qs.all()


def test_create_task_request_date_validation():
    with pytest.raises(ValueError):
        CreateTaskRequest(
            subject="s",
            start_date=date(2026, 8, 5),
            due_date=date(2026, 8, 1),
        )


def test_update_task_request_date_validation():
    with pytest.raises(ValueError):
        UpdateTaskRequest(
            id="x",
            start_date=date(2026, 8, 5),
            due_date=date(2026, 8, 1),
        )


def test_list_tasks_filters(backend):
    folder = FakeTasksFolder()
    backend._account.tasks = folder
    backend.list_tasks(ListTasksRequest(status="in_progress", incomplete_only=True, category="work"))
    filters = folder.qs.filters[0]
    assert filters["status"] == "InProgress"
    assert filters["is_complete"] is False
    assert filters["categories__contains"] == "work"


def test_update_task_percent_complete_transitions_status(backend):
    item = FakeTask()
    backend._fetch_item = lambda _id, folder=None: item
    backend.update_task(UpdateTaskRequest(id="x", percent_complete=50))
    assert item.percent_complete == 50
    assert item.status == "InProgress"
    assert "percent_complete" in item.saved_with
    assert "status" in item.saved_with


def test_update_task_100_marks_completed(backend):
    item = FakeTask()
    backend._fetch_item = lambda _id, folder=None: item
    backend.update_task(UpdateTaskRequest(id="x", percent_complete=100))
    assert item.status == "Completed"
    assert "complete_date" not in item.saved_with  # server-computed, not written
    assert "status" in item.saved_with


def test_complete_task_sets_fields(backend):
    item = FakeTask()
    backend._fetch_item = lambda _id, folder=None: item
    result = backend.complete_task(CompleteTaskRequest(id="x"))
    assert item.status == "Completed"
    assert item.percent_complete == 100
    assert result.status == "completed"
    assert set(result.updated_fields) == {"status", "percent_complete"}


def test_delete_task_soft_delete_by_default(backend):
    item = FakeTask()
    backend._fetch_item = lambda _id, folder=None: item
    from exchange_mcp.models import DeleteTaskRequest

    backend.delete_task(DeleteTaskRequest(id="x"))
    assert item.trashed and item.deleted is None


def test_delete_task_hard_delete(backend):
    item = FakeTask()
    backend._fetch_item = lambda _id, folder=None: item
    from exchange_mcp.models import DeleteTaskRequest

    backend.delete_task(DeleteTaskRequest(id="x", hard_delete=True))
    assert item.deleted is not None and not item.trashed


def test_task_tools_schema(settings):
    tools = _tools(build_mcp_server(settings=settings, client=object()))
    assert tools["create_task"].inputSchema["required"] == ["subject"]
    assert tools["update_task"].inputSchema["required"] == ["id"]
    assert tools["complete_task"].inputSchema["required"] == ["id"]
    assert tools["delete_task"].inputSchema["required"] == ["id"]
    assert tools["list_tasks"].inputSchema.get("required", []) == []

