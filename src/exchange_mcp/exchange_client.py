from __future__ import annotations

import logging
import re
import tempfile
import threading
import warnings
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from exchangelib import (
    Account,
    Attendee,
    BASIC,
    CalendarItem,
    Configuration,
    Credentials,
    DELEGATE,
    EWSTimeZone,
    FileAttachment,
    Folder,
    HTMLBody,
    IMPERSONATION,
    Mailbox,
    Message,
    NTLM,
    Task,
)
from exchangelib.ewsdatetime import EWSDateTime
from exchangelib.errors import (
    ErrorItemSavePropertyError,
    ErrorFolderSavePropertyError,
    RateLimitError,
    ResponseMessageError,
    TransportError,
    UnknownTimeZone,
    UnauthorizedError,
)
from exchangelib.indexed_properties import EmailAddress as IndexedEmailAddress
from exchangelib.indexed_properties import PhoneNumber as IndexedPhoneNumber
from exchangelib.items import Contact
from exchangelib.properties import ItemId
from exchangelib.protocol import BaseProtocol, FailFast, FaultTolerance, NoVerifyHTTPAdapter
from exchangelib.services import ResolveNames
from exchangelib import version as ews_version
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout
from urllib3.exceptions import InsecureRequestWarning

from .auth import build_auth_context
from .config import Settings
from .errors import (
    APIError,
    AuthFailedError,
    ConflictError,
    ExchangeUnavailableError,
    NotFoundError,
    PermissionDeniedError,
    TimeoutAPIError,
)
from .models import (
    ActionResult,
    AttachmentResult,
    AvailabilityResult,
    CalendarInfo,
    CalendarEvent,
    CompleteTaskRequest,
    ContactFull,
    ContactSummary,
    CreateEventRequest,
    CreateEventResult,
    CreateContactRequest,
    CreateFolderRequest,
    CreateTaskRequest,
    DeleteContactRequest,
    DeleteEmailRequest,
    DeleteEventRequest,
    DeleteTaskRequest,
    DraftEmailRequest,
    EmailFull,
    EmailSummary,
    EmailAddress,
    FolderActionRequest,
    FolderInfo,
    FindFreeSlotsRequest,
    ForwardEmailRequest,
    FreeSlot,
    GetAttachmentRequest,
    GetContactRequest,
    GetEmailRequest,
    GetEventRequest,
    GetTaskRequest,
    ListEmailsRequest,
    ListEventsRequest,
    ListFoldersRequest,
    ListTasksRequest,
    MailboxInfo,
    MarkEmailRequest,
    PingResult,
    ReplyEmailRequest,
    RespondToInviteRequest,
    SearchContactsRequest,
    SearchEmailsRequest,
    SendDraftRequest,
    SendEmailRequest,
    SendResult,
    Attachment,
    TaskFull,
    TaskSummary,
    UpdateContactRequest,
    UpdateEventRequest,
    UpdateTaskRequest,
)

logger = logging.getLogger(__name__)
_TZ_FROM_MS_ID_ORIGINAL = None
_GUID_TIMEZONE_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_EWS_ID_RE = re.compile(r"^[A-Za-z0-9+/=]{40,}$")

# Restricted field sets for list queries. Without .only(), exchangelib requests
# ALL item fields (full body, MIME content, headers, attachments), which forces
# a heavy GetItem round-trip per page and regularly blows the EWS read timeout
# on slow servers. These sets cover exactly what the summary models need.
_EMAIL_SUMMARY_FIELDS = (
    "subject",
    "author",
    "sender",
    "to_recipients",
    "datetime_received",
    "datetime_sent",
    "datetime_created",
    "is_read",
    "has_attachments",
    "importance",
    "categories",
    "text_body",
)
_EVENT_FIELDS = (
    "subject",
    "start",
    "end",
    "location",
    "organizer",
    "required_attendees",
    "optional_attendees",
    "is_all_day",
    "is_recurring",
    "my_response_type",
    "meeting_workspace_url",
    "net_show_url",
    "text_body",
    "reminder_minutes_before_start",
    "categories",
    "recurrence",
    "importance",
)
_TASK_SUMMARY_FIELDS = (
    "subject",
    "status",
    "percent_complete",
    "due_date",
    "start_date",
    "is_complete",
    "has_attachments",
    "importance",
    "categories",
    "reminder_is_set",
)
_CONTACT_SUMMARY_FIELDS = (
    "display_name",
    "file_as",
    "email_addresses",
    "phone_numbers",
    "company_name",
    "job_title",
    "department",
)


def _aqs_quote(value: str) -> str:
    """Quote a value for embedding in an AQS query string."""
    return '"' + value.replace("\\", "").replace('"', "") + '"'


class ExchangeBackend(Protocol):
    def ping(self) -> PingResult: ...
    def get_mailbox_info(self) -> MailboxInfo: ...
    def list_emails(self, request: ListEmailsRequest) -> list[EmailSummary]: ...
    def get_email(self, request: GetEmailRequest) -> EmailFull: ...
    def search_emails(self, request: SearchEmailsRequest) -> list[EmailSummary]: ...
    def send_email(self, request: SendEmailRequest) -> SendResult: ...
    def reply_email(self, request: ReplyEmailRequest) -> SendResult: ...
    def forward_email(self, request: ForwardEmailRequest) -> SendResult: ...
    def move_email(self, request: FolderActionRequest) -> ActionResult: ...
    def copy_email(self, request: FolderActionRequest) -> ActionResult: ...
    def delete_email(self, request: DeleteEmailRequest) -> ActionResult: ...
    def mark_email(self, request: MarkEmailRequest) -> ActionResult: ...
    def list_folders(self, request: ListFoldersRequest) -> list[FolderInfo]: ...
    def create_folder(self, request: CreateFolderRequest) -> ActionResult: ...
    def create_draft(self, request: DraftEmailRequest) -> ActionResult: ...
    def send_draft(self, request: SendDraftRequest) -> ActionResult: ...
    def get_attachment(self, request: GetAttachmentRequest) -> AttachmentResult: ...
    def list_events(self, request: ListEventsRequest) -> list[CalendarEvent]: ...
    def get_event(self, request: GetEventRequest) -> CalendarEvent: ...
    def create_event(self, request: CreateEventRequest) -> CreateEventResult: ...
    def update_event(self, request: UpdateEventRequest) -> ActionResult: ...
    def delete_event(self, request: DeleteEventRequest) -> ActionResult: ...
    def respond_to_invite(self, request: RespondToInviteRequest) -> ActionResult: ...
    def find_free_slots(self, request: FindFreeSlotsRequest) -> list[FreeSlot]: ...
    def get_my_availability(self, request: ListEventsRequest) -> AvailabilityResult: ...
    def list_calendars(self) -> list[CalendarInfo]: ...
    def search_contacts(self, request: SearchContactsRequest) -> list[ContactSummary]: ...
    def get_contact(self, request: GetContactRequest) -> ContactFull: ...
    def create_contact(self, request: CreateContactRequest) -> ActionResult: ...
    def update_contact(self, request: UpdateContactRequest) -> ActionResult: ...
    def delete_contact(self, request: DeleteContactRequest) -> ActionResult: ...
    def list_tasks(self, request: ListTasksRequest) -> list[TaskSummary]: ...
    def get_task(self, request: GetTaskRequest) -> TaskFull: ...
    def create_task(self, request: CreateTaskRequest) -> ActionResult: ...
    def update_task(self, request: UpdateTaskRequest) -> ActionResult: ...
    def complete_task(self, request: CompleteTaskRequest) -> ActionResult: ...
    def delete_task(self, request: DeleteTaskRequest) -> ActionResult: ...


class ExchangeClient:
    def __init__(self, settings: Settings, backend: ExchangeBackend) -> None:
        self.settings = settings
        self.backend = backend

    def ping(self) -> PingResult:
        return self.backend.ping()

    def get_mailbox_info(self) -> MailboxInfo:
        return self.backend.get_mailbox_info()

    def list_emails(self, request: ListEmailsRequest) -> list[EmailSummary]:
        return self.backend.list_emails(request)

    def get_email(self, request: GetEmailRequest) -> EmailFull:
        return self.backend.get_email(request)

    def search_emails(self, request: SearchEmailsRequest) -> list[EmailSummary]:
        return self.backend.search_emails(request)

    def send_email(self, request: SendEmailRequest) -> SendResult:
        return self.backend.send_email(request)

    def reply_email(self, request: ReplyEmailRequest) -> SendResult:
        return self.backend.reply_email(request)

    def forward_email(self, request: ForwardEmailRequest) -> SendResult:
        return self.backend.forward_email(request)

    def move_email(self, request: FolderActionRequest) -> ActionResult:
        return self.backend.move_email(request)

    def copy_email(self, request: FolderActionRequest) -> ActionResult:
        return self.backend.copy_email(request)

    def delete_email(self, request: DeleteEmailRequest) -> ActionResult:
        return self.backend.delete_email(request)

    def mark_email(self, request: MarkEmailRequest) -> ActionResult:
        return self.backend.mark_email(request)

    def list_folders(self, request: ListFoldersRequest) -> list[FolderInfo]:
        return self.backend.list_folders(request)

    def create_folder(self, request: CreateFolderRequest) -> ActionResult:
        return self.backend.create_folder(request)

    def create_draft(self, request: DraftEmailRequest) -> ActionResult:
        return self.backend.create_draft(request)

    def send_draft(self, request: SendDraftRequest) -> ActionResult:
        return self.backend.send_draft(request)

    def get_attachment(self, request: GetAttachmentRequest) -> AttachmentResult:
        return self.backend.get_attachment(request)

    def list_events(self, request: ListEventsRequest) -> list[CalendarEvent]:
        return self.backend.list_events(request)

    def get_event(self, request: GetEventRequest) -> CalendarEvent:
        return self.backend.get_event(request)

    def create_event(self, request: CreateEventRequest) -> CreateEventResult:
        return self.backend.create_event(request)

    def update_event(self, request: UpdateEventRequest) -> ActionResult:
        return self.backend.update_event(request)

    def delete_event(self, request: DeleteEventRequest) -> ActionResult:
        return self.backend.delete_event(request)

    def respond_to_invite(self, request: RespondToInviteRequest) -> ActionResult:
        return self.backend.respond_to_invite(request)

    def find_free_slots(self, request: FindFreeSlotsRequest) -> list[FreeSlot]:
        return self.backend.find_free_slots(request)

    def get_my_availability(self, request: ListEventsRequest) -> AvailabilityResult:
        return self.backend.get_my_availability(request)

    def list_calendars(self) -> list[CalendarInfo]:
        return self.backend.list_calendars()

    def search_contacts(self, request: SearchContactsRequest) -> list[ContactSummary]:
        return self.backend.search_contacts(request)

    def get_contact(self, request: GetContactRequest) -> ContactFull:
        return self.backend.get_contact(request)

    def create_contact(self, request: CreateContactRequest) -> ActionResult:
        return self.backend.create_contact(request)

    def update_contact(self, request: UpdateContactRequest) -> ActionResult:
        return self.backend.update_contact(request)

    def delete_contact(self, request: DeleteContactRequest) -> ActionResult:
        return self.backend.delete_contact(request)

    def list_tasks(self, request: ListTasksRequest) -> list[TaskSummary]:
        return self.backend.list_tasks(request)

    def get_task(self, request: GetTaskRequest) -> TaskFull:
        return self.backend.get_task(request)

    def create_task(self, request: CreateTaskRequest) -> ActionResult:
        return self.backend.create_task(request)

    def update_task(self, request: UpdateTaskRequest) -> ActionResult:
        return self.backend.update_task(request)

    def complete_task(self, request: CompleteTaskRequest) -> ActionResult:
        return self.backend.complete_task(request)

    def delete_task(self, request: DeleteTaskRequest) -> ActionResult:
        return self.backend.delete_task(request)


def build_default_backend(settings: Settings) -> ExchangeBackend:
    return EWSExchangeBackend(settings)


class EWSExchangeBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._account: Account | None = None
        self._account_lock = threading.Lock()

    @property
    def account(self) -> Account:
        if self._account is None:
            with self._account_lock:
                if self._account is None:
                    self._account = self._build_account()
        return self._account

    def warm_up(self) -> None:
        """Build the Account eagerly so the first tool call does not pay for
        auth, server version negotiation and folder resolution."""
        try:
            account = self.account
            # Touch the distinguished folder hierarchy to prime exchangelib's cache
            account.inbox.refresh()
            logger.info("exchange session warmed up for %s", account.primary_smtp_address)
        except Exception as exc:  # noqa: BLE001 — warm-up is best-effort
            logger.warning("exchange warm-up failed (first call will retry): %s", exc)

    def _build_account(self) -> Account:
        auth = build_auth_context(self.settings)
        if auth.auth_type == "OAuth2":
            raise APIError(
                "validation_error",
                "OAuth2 is not wired in this build yet",
                details=[{"field": "EXCHANGE_AUTH_TYPE", "reason": "supported values for live checks are NTLM or Basic"}],
            )

        BaseProtocol.TIMEOUT = self.settings.exchange_timeout
        self._configure_ssl_verification()
        self._configure_timezone_fallback()
        retry_policy = FailFast() if self.settings.exchange_max_retries == 0 else FaultTolerance(
            max_wait=max(self.settings.exchange_timeout * self.settings.exchange_max_retries, self.settings.exchange_timeout)
        )
        credentials = Credentials(username=auth.username, password=auth.password)
        auth_type = BASIC if auth.auth_type == "Basic" else NTLM
        service_endpoint = self._normalize_service_endpoint(self.settings.exchange_server)
        config = Configuration(
            service_endpoint=service_endpoint,
            credentials=credentials,
            auth_type=auth_type,
            retry_policy=retry_policy,
            version=self._exchange_version(),
        )
        access_type = IMPERSONATION if auth.impersonate_as else DELEGATE
        try:
            return Account(
                primary_smtp_address=auth.primary_smtp_address,
                config=config,
                autodiscover=False,
                access_type=access_type,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc

    def _normalize_service_endpoint(self, value: str) -> str:
        endpoint = value.strip()
        if "://" not in endpoint:
            endpoint = f"https://{endpoint}"
        if not endpoint.lower().endswith("/ews/exchange.asmx"):
            endpoint = endpoint.rstrip("/") + "/EWS/Exchange.asmx"
        return endpoint

    def _exchange_version(self) -> ews_version.Version | None:
        if not self.settings.exchange_version:
            return None
        name = self.settings.exchange_version.upper()
        build = getattr(ews_version, name, None)
        if not isinstance(build, ews_version.Build):
            build = next((b for b, api, _ in ews_version.VERSIONS if api.upper() == name), None)
        if not isinstance(build, ews_version.Build):
            supported = sorted(n for n in dir(ews_version) if n.startswith("EXCHANGE_"))
            raise APIError(
                "validation_error",
                f"unknown EXCHANGE_VERSION: {self.settings.exchange_version}",
                details=[{"field": "EXCHANGE_VERSION", "reason": f"supported values: {', '.join(supported)}"}],
            )
        return ews_version.Version(build)

    def _configure_ssl_verification(self) -> None:
        if self.settings.exchange_verify_ssl:
            BaseProtocol.HTTP_ADAPTER_CLS = HTTPAdapter
            return
        BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter
        warnings.filterwarnings("ignore", category=InsecureRequestWarning)
        logger.warning("SSL certificate verification is disabled for Exchange connections")

    def _configure_timezone_fallback(self) -> None:
        global _TZ_FROM_MS_ID_ORIGINAL
        if _TZ_FROM_MS_ID_ORIGINAL is None:
            _TZ_FROM_MS_ID_ORIGINAL = EWSTimeZone.from_ms_id.__func__
        original = _TZ_FROM_MS_ID_ORIGINAL
        fallback_timezone = self.settings.exchange_timezone

        def from_ms_id_with_fallback(cls, ms_id):
            try:
                return original(cls, ms_id)
            except UnknownTimeZone:
                if isinstance(ms_id, str) and _GUID_TIMEZONE_RE.match(ms_id):
                    logger.info(
                        "Mapping unknown Exchange timezone id %s to configured timezone %s",
                        ms_id,
                        fallback_timezone,
                    )
                    return cls(fallback_timezone)
                raise

        EWSTimeZone.from_ms_id = classmethod(from_ms_id_with_fallback)

    def _resolve_folder(self, value: str | None) -> Folder:
        account = self.account
        if not value or value == "root":
            return account.root
        normalized = value.strip("/").lower()
        builtin = {
            "inbox": account.inbox,
            "входящие": account.inbox,
            "sent": account.sent,
            "отправленные": account.sent,
            "sentitems": account.sent,
            "drafts": account.drafts,
            "черновики": account.drafts,
            "deleted": account.trash,
            "deleteditems": account.trash,
            "удаленные": account.trash,
            "trash": account.trash,
            "junk": account.junk,
            "junkemail": account.junk,
            "спам": account.junk,
            "calendar": account.calendar,
            "contacts": account.contacts,
        }
        # Check if the entire path matches a builtin folder
        if normalized in builtin:
            return builtin[normalized]
        # Check if the first part is a builtin folder, then traverse the rest
        parts = [segment for segment in value.strip("/").split("/") if segment]
        if len(parts) > 1:
            first_part_lower = parts[0].lower()
            if first_part_lower in builtin:
                current = builtin[first_part_lower]
                for part in parts[1:]:
                    next_folder = next(
                        (child for child in current.children if child.name.lower() == part.lower()),
                        None,
                    )
                    if next_folder is None:
                        raise NotFoundError(value)
                    current = next_folder
                return current
        # If the value looks like an EWS folder ID, bind to it directly with a
        # single GetFolder call instead of walking the entire folder hierarchy.
        if _EWS_ID_RE.match(value):
            candidate = Folder(root=account.root, id=value)
            try:
                candidate.refresh()
            except Exception:  # noqa: BLE001 — not an ID (or gone); fall back to path traversal
                pass
            else:
                return candidate
        # Try to traverse from root
        current = account.root
        for part in parts:
            next_folder = next(
                (child for child in current.children if child.name.lower() == part.lower()),
                None,
            )
            if next_folder is None:
                raise NotFoundError(value)
            current = next_folder
        return current

    def _fetch_item(self, item_id: str, folder: Folder | None = None) -> Any:
        try:
            return next(self.account.fetch(ids=[ItemId(id=item_id, changekey=None)], folder=folder))
        except StopIteration as exc:
            raise NotFoundError(item_id) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=item_id) from exc

    def _mailbox(self, address: str) -> Mailbox:
        return Mailbox(email_address=address)

    def _email_address(self, mailbox: Any) -> EmailAddress:
        if mailbox is None:
            return EmailAddress(email="unknown@example.invalid", name=None)
        email = getattr(mailbox, "email_address", None) or getattr(mailbox, "email", None) or "unknown@example.invalid"
        name = getattr(mailbox, "name", None)
        return EmailAddress(email=email, name=name)

    def _recipients(self, values: Iterable[Any] | None) -> list[EmailAddress]:
        return [self._email_address(value) for value in values or []]

    def _to_email_summary(self, item: Any) -> EmailSummary:
        return EmailSummary(
            id=item.id,
            subject=item.subject or "",
            from_=self._email_address(getattr(item, "author", None) or getattr(item, "sender", None)),
            to=self._recipients(getattr(item, "to_recipients", None)),
            date=getattr(item, "datetime_received", None)
            or getattr(item, "datetime_sent", None)
            or getattr(item, "datetime_created", None)
            or datetime.now(UTC),
            is_read=bool(getattr(item, "is_read", False)),
            has_attachments=bool(getattr(item, "has_attachments", False)),
            preview=self._preview(item),
            importance=self._normalize_importance(getattr(item, "importance", None)),
            categories=list(getattr(item, "categories", None) or []),
        )

    def _attachment_metadata(self, attachment: Any) -> Attachment:
        return Attachment(
            id=getattr(getattr(attachment, "attachment_id", None), "id", None),
            name=getattr(attachment, "name", "attachment"),
            size=getattr(attachment, "size", None),
            content_type=getattr(attachment, "content_type", None),
        )

    def _to_email_full(self, item: Any) -> EmailFull:
        body_text, body_html = self._extract_message_body(item)
        return EmailFull(
            **self._to_email_summary(item).model_dump(by_alias=True),
            cc=self._recipients(getattr(item, "cc_recipients", None)),
            bcc=self._recipients(getattr(item, "bcc_recipients", None)),
            body_text=body_text,
            body_html=body_html,
            attachments=[self._attachment_metadata(a) for a in getattr(item, "attachments", None) or []],
            conversation_id=getattr(getattr(item, "conversation_id", None), "id", None),
            headers=self._headers_to_dict(getattr(item, "headers", None)),
            truncated=False,
        )

    def _extract_message_body(self, item: Any) -> tuple[str, str | None]:
        text = ""
        html = None
        if getattr(item, "text_body", None):
            text = str(item.text_body)
        elif getattr(item, "body", None):
            text = str(item.body)

        body = getattr(item, "body", None)
        if isinstance(body, HTMLBody):
            html = str(body)
        elif body is not None and "</" in str(body):
            html = str(body)
        return text, html

    def _preview(self, item: Any) -> str:
        text, _ = self._extract_message_body(item)
        return text[:200]

    def _headers_to_dict(self, headers: Any) -> dict[str, str]:
        result: dict[str, str] = {}
        for header in headers or []:
            name = getattr(header, "name", None)
            value = getattr(header, "value", None)
            if name and value is not None:
                result[str(name)] = str(value)
        return result

    def _normalize_importance(self, value: Any) -> str:
        normalized = str(value or "normal").lower()
        return normalized if normalized in {"low", "normal", "high"} else "normal"

    def _to_calendar_event(self, item: Any) -> CalendarEvent:
        attendees = [
            self._to_attendee(attendee)
            for attendee in (getattr(item, "required_attendees", None) or []) + (getattr(item, "optional_attendees", None) or [])
        ]
        organizer = self._email_address(getattr(item, "organizer", None))
        body_text, _ = self._extract_message_body(item)
        online_url = getattr(item, "meeting_workspace_url", None) or getattr(item, "net_show_url", None)
        recurrence = getattr(item, "recurrence", None)
        return CalendarEvent(
            id=item.id,
            subject=item.subject or "",
            start=item.start,
            end=item.end,
            location=getattr(item, "location", None),
            organizer=organizer,
            attendees=attendees,
            is_all_day=bool(getattr(item, "is_all_day", False)),
            is_recurring=bool(getattr(item, "is_recurring", False)),
            my_response=self._normalize_response(getattr(item, "my_response_type", None)),
            online_meeting_url=str(online_url) if online_url else None,
            body=body_text or None,
            reminder_minutes=getattr(item, "reminder_minutes_before_start", None),
            categories=list(getattr(item, "categories", None) or []),
            recurrence_pattern={"value": str(recurrence)} if recurrence else None,
            importance=self._normalize_importance(getattr(item, "importance", None)),
        )

    def _to_attendee(self, attendee: Any) -> Any:
        mailbox = getattr(attendee, "mailbox", None) or attendee
        response = getattr(attendee, "response_type", None)
        address = self._email_address(mailbox)
        from .models import Attendee as ApiAttendee

        return ApiAttendee(
            email=address.email,
            name=address.name,
            response_type=self._normalize_response(response),
        )

    def _normalize_response(self, value: Any) -> str:
        normalized = str(value or "unknown").lower()
        mapping = {
            "accept": "accept",
            "accepted": "accept",
            "organizer": "accept",
            "tentative": "tentative",
            "decline": "decline",
            "declined": "decline",
        }
        return mapping.get(normalized, "unknown")

    def _make_message(self, request: SendEmailRequest | DraftEmailRequest) -> Message:
        body: str | HTMLBody = HTMLBody(request.body) if request.body_type == "html" else request.body
        message = Message(
            account=self.account,
            folder=self.account.drafts,
            subject=request.subject,
            body=body,
            to_recipients=[self._mailbox(address) for address in request.to],
            cc_recipients=[self._mailbox(address) for address in request.cc],
            bcc_recipients=[self._mailbox(address) for address in request.bcc],
            reply_to=[self._mailbox(request.reply_to)] if getattr(request, "reply_to", None) else None,
            importance=request.importance.capitalize() if hasattr(request, "importance") else "Normal",
        )
        for path in request.attachments:
            with Path(path).open("rb") as handle:
                message.attach(FileAttachment(name=Path(path).name, content=handle.read()))
        for image in getattr(request, "inline_images", None) or []:
            with Path(image.path).open("rb") as handle:
                content = handle.read()
            message.attach(
                FileAttachment(
                    name=Path(image.path).name,
                    content=content,
                    is_inline=True,
                    content_id=image.content_id,
                    content_type=self._guess_content_type(Path(image.path).suffix),
                )
            )
        return message

    @staticmethod
    def _guess_content_type(suffix: str) -> str:
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
        }.get(suffix.lower(), "application/octet-stream")

    def _to_folder_info(self, folder: Folder, depth: int) -> FolderInfo:
        children = []
        if depth > 0:
            children = [self._to_folder_info(child, depth - 1) for child in folder.children]
        return FolderInfo(
            id=getattr(folder, "id", None),
            name=folder.name,
            path=self._folder_path(folder),
            unread_count=getattr(folder, "unread_count", 0) or 0,
            total_count=getattr(folder, "total_count", 0) or 0,
            children=children,
        )

    def _folder_path(self, folder: Folder) -> str:
        parts = []
        current = folder
        while current is not None and getattr(current, "name", None):
            parts.append(current.name)
            current = getattr(current, "parent", None)
        return "/".join(reversed(parts))

    def _map_exception(self, exc: Exception, item_id: str | None = None) -> APIError:
        message = str(exc)
        if isinstance(exc, APIError):
            return exc
        if isinstance(exc, UnauthorizedError):
            return AuthFailedError()
        if isinstance(exc, RateLimitError):
            return ExchangeUnavailableError("exchange throttling or rate limit encountered")
        if isinstance(exc, (ErrorItemSavePropertyError, ErrorFolderSavePropertyError)):
            return ConflictError(message)
        # ResponseMessageError subclasses TransportError, so it must be checked first
        if isinstance(exc, ResponseMessageError):
            lowered = message.lower()
            if "not found" in lowered and item_id:
                return NotFoundError(item_id)
            if "access is denied" in lowered or "permission" in lowered:
                return PermissionDeniedError()
            return APIError("exchange_error", message)
        # Raw requests-level network failures: drop the cached account so the
        # next call rebuilds the EWS session instead of reusing a dead socket.
        if isinstance(exc, RequestsTimeout):
            self._invalidate_account()
            return TimeoutAPIError(self.settings.exchange_timeout)
        if isinstance(exc, RequestsConnectionError):
            self._invalidate_account()
            return ExchangeUnavailableError(message)
        if isinstance(exc, (TransportError, TimeoutError)):
            lowered = message.lower()
            if "timed out" in lowered or "timeout" in lowered:
                self._invalidate_account()
                return TimeoutAPIError(self.settings.exchange_timeout)
            if "connection" in lowered or "reset" in lowered or "aborted" in lowered:
                self._invalidate_account()
            return ExchangeUnavailableError(message)
        return ExchangeUnavailableError(message)

    def _invalidate_account(self) -> None:
        if self._account is None:
            return
        logger.warning("dropping cached Exchange session after a connection failure; it will be rebuilt on next call")
        try:
            close = getattr(getattr(self._account, "protocol", None), "close", None)
            if callable(close):
                close()
        except Exception:  # noqa: BLE001 — best-effort cleanup of a broken session
            pass
        self._account = None

    def ping(self) -> PingResult:
        started = datetime.now(UTC)
        account = self.account
        try:
            account.inbox.refresh()
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc
        latency_ms = round((datetime.now(UTC) - started).total_seconds() * 1000)
        parsed = urlparse(self.settings.exchange_server)
        version = getattr(getattr(account.protocol, "version", None), "api_version", None)
        return PingResult(status="ok", server=parsed.netloc or self.settings.exchange_server, version=version, latency_ms=latency_ms)

    def get_mailbox_info(self) -> MailboxInfo:
        account = self.account
        version = getattr(getattr(account.protocol, "version", None), "api_version", None)
        return MailboxInfo(
            email_address=account.primary_smtp_address,
            display_name=account.fullname or account.primary_smtp_address,
            timezone=str(account.default_timezone),
            exchange_version=version,
        )

    def list_emails(self, request: ListEmailsRequest) -> list[EmailSummary]:
        folder = self._resolve_folder(request.folder)
        qs = folder.all().only(*_EMAIL_SUMMARY_FIELDS).order_by("-datetime_received")
        if request.from_address:
            # EWS restrictions cannot filter on the sender's email address, and AQS
            # query strings cannot be combined with restrictions, so the whole
            # filter must be expressed as AQS here.
            qs = qs.filter(self._aqs_list_query(request))
        else:
            filters: dict[str, Any] = {}
            if request.subject:
                filters["subject__icontains"] = request.subject
            if request.since:
                filters["datetime_received__gte"] = datetime.combine(request.since, datetime.min.time(), tzinfo=self.account.default_timezone)
            if request.before:
                filters["datetime_received__lt"] = datetime.combine(request.before + timedelta(days=1), datetime.min.time(), tzinfo=self.account.default_timezone)
            if request.unread_only:
                filters["is_read"] = False
            if request.has_attachments is not None:
                filters["has_attachments"] = request.has_attachments
            if filters:
                qs = qs.filter(**filters)
        try:
            items = list(qs[request.offset : request.offset + request.limit])
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc
        return [self._to_email_summary(item) for item in items]

    @staticmethod
    def _aqs_list_query(request: ListEmailsRequest) -> str:
        parts = [f"from:{_aqs_quote(str(request.from_address))}"]
        if request.subject:
            parts.append(f"subject:{_aqs_quote(request.subject)}")
        if request.since and request.before:
            parts.append(f"received:{request.since.isoformat()}..{request.before.isoformat()}")
        elif request.since:
            parts.append(f"received:>={request.since.isoformat()}")
        elif request.before:
            parts.append(f"received:<={request.before.isoformat()}")
        if request.unread_only:
            parts.append("isread:false")
        if request.has_attachments is not None:
            parts.append(f"hasattachment:{str(request.has_attachments).lower()}")
        return " AND ".join(parts)

    def get_email(self, request: GetEmailRequest) -> EmailFull:
        item = self._fetch_item(request.id)
        return self._to_email_full(item)

    def search_emails(self, request: SearchEmailsRequest) -> list[EmailSummary]:
        folder = self._resolve_folder(request.folder) if request.folder else self.account.inbox
        # AQS query string: a bare term is searched server-side across all indexed
        # properties (subject, body, sender, recipients) using the content index.
        # AQS keywords like from:, subject:, hasattachment: are passed through as-is.
        try:
            qs = folder.filter(request.query).only(*_EMAIL_SUMMARY_FIELDS).order_by("-datetime_received")
            items = list(qs[: request.limit])
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc
        return [self._to_email_summary(item) for item in items]

    def send_email(self, request: SendEmailRequest) -> SendResult:
        message = self._make_message(request)
        try:
            message.send_and_save()
            return SendResult(id=message.id or "", status="sent")
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc

    def reply_email(self, request: ReplyEmailRequest) -> SendResult:
        item = self._fetch_item(request.id)
        try:
            if request.reply_all:
                item.reply_all(subject=f"Re: {item.subject or ''}", body=request.body)
            else:
                item.reply(subject=f"Re: {item.subject or ''}", body=request.body)
            warning = None
            if request.attachments:
                warning = "EWS reply does not support attachments; they were not included"
            return SendResult(id=request.id, status="sent", warning=warning)
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def forward_email(self, request: ForwardEmailRequest) -> SendResult:
        item = self._fetch_item(request.id)
        try:
            item.forward(
                subject=f"Fwd: {item.subject or ''}",
                body=request.comment or "",
                to_recipients=[self._mailbox(address) for address in request.to],
            )
            warning = None
            if request.attachments:
                warning = "EWS forward does not support extra attachments; they were not included"
            return SendResult(id=request.id, status="sent", warning=warning)
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def move_email(self, request: FolderActionRequest) -> ActionResult:
        item = self._fetch_item(request.id)
        destination = self._resolve_folder(request.folder)
        try:
            result = item.move(to_folder=destination)
            return ActionResult(id=getattr(result, "id", request.id), status="moved", new_folder=request.folder)
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def copy_email(self, request: FolderActionRequest) -> ActionResult:
        item = self._fetch_item(request.id)
        destination = self._resolve_folder(request.folder)
        try:
            result = item.copy(to_folder=destination)
            return ActionResult(
                id=request.id,
                status="copied",
                new_folder=request.folder,
                new_id=getattr(result, "id", None),
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def delete_email(self, request: DeleteEmailRequest) -> ActionResult:
        item = self._fetch_item(request.id)
        try:
            if request.hard_delete:
                item.delete()
            else:
                item.move_to_trash()
            return ActionResult(id=request.id, status="deleted")
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def mark_email(self, request: MarkEmailRequest) -> ActionResult:
        item = self._fetch_item(request.id)
        updated_fields: list[str] = []
        if request.read is not None:
            item.is_read = request.read
            updated_fields.append("is_read")
        if request.importance is not None:
            item.importance = request.importance.capitalize()
            updated_fields.append("importance")
        warning = None
        if request.flag is not None:
            item.categories = [] if request.flag == "none" else [request.flag]
            updated_fields.append("categories")
            warning = "flag is mapped to Exchange categories (replaces existing ones); follow-up flags are not supported"
        if not updated_fields:
            return ActionResult(id=request.id, status="updated", updated_fields=[])
        try:
            item.save(update_fields=updated_fields)
            return ActionResult(id=request.id, status="updated", updated_fields=updated_fields, warning=warning)
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def list_folders(self, request: ListFoldersRequest) -> list[FolderInfo]:
        folder = self._resolve_folder(request.parent)
        return [self._to_folder_info(child, request.depth - 1) for child in folder.children] if request.depth != 0 else [self._to_folder_info(folder, 0)]

    def create_folder(self, request: CreateFolderRequest) -> ActionResult:
        parent = self._resolve_folder(request.parent)
        folder = Folder(parent=parent, name=request.name)
        try:
            folder.save()
            return ActionResult(id=getattr(folder, "id", ""), status="created", path=self._folder_path(folder))
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc

    def create_draft(self, request: DraftEmailRequest) -> ActionResult:
        message = self._make_message(request)
        try:
            message.save()
            return ActionResult(id=message.id or "", status="draft")
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc

    def send_draft(self, request: SendDraftRequest) -> ActionResult:
        item = self._fetch_item(request.id, folder=self.account.drafts)
        try:
            item.send_and_save()
            return ActionResult(id=request.id, status="sent")
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def get_attachment(self, request: GetAttachmentRequest) -> AttachmentResult:
        item = self._fetch_item(request.email_id)
        target_dir = Path(request.save_path) if request.save_path else Path(tempfile.gettempdir())
        target_dir.mkdir(parents=True, exist_ok=True)
        for attachment in getattr(item, "attachments", None) or []:
            attachment_id = getattr(getattr(attachment, "attachment_id", None), "id", None)
            if attachment_id != request.attachment_id:
                continue
            if not isinstance(attachment, FileAttachment):
                raise APIError(
                    "validation_error",
                    f"attachment {request.attachment_id} is an embedded item, not a file",
                )
            max_size_bytes = self.settings.attachment_max_size_mb * 1024 * 1024
            declared_size = getattr(attachment, "size", None)
            if declared_size is not None and declared_size > max_size_bytes:
                raise APIError(
                    "validation_error",
                    "attachment exceeds the configured size limit",
                    details=[{"field": "attachment_id", "reason": f"size {declared_size} exceeds ATTACHMENT_MAX_SIZE_MB={self.settings.attachment_max_size_mb}"}],
                )
            content = attachment.content
            if len(content) > max_size_bytes:
                raise APIError(
                    "validation_error",
                    "attachment exceeds the configured size limit",
                    details=[{"field": "attachment_id", "reason": f"size {len(content)} exceeds ATTACHMENT_MAX_SIZE_MB={self.settings.attachment_max_size_mb}"}],
                )
            # Strip any directory components from the server-provided name
            filename = Path(getattr(attachment, "name", None) or "attachment.bin").name or "attachment.bin"
            path = self._unique_path(target_dir / filename)
            path.write_bytes(content)
            return AttachmentResult(
                filename=filename,
                size=len(content),
                saved_path=str(path),
                content_type=getattr(attachment, "content_type", None),
            )
        raise NotFoundError(request.attachment_id)

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        index = 1
        while True:
            candidate = path.with_name(f"{stem}-{index}{suffix}")
            if not candidate.exists():
                return candidate
            index += 1

    def _to_ews_datetime(self, value: datetime) -> EWSDateTime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=self.account.default_timezone)
        else:
            value = value.astimezone(self.account.default_timezone)
        if isinstance(value, EWSDateTime):
            return value
        return EWSDateTime.from_datetime(value)

    def list_events(self, request: ListEventsRequest) -> list[CalendarEvent]:
        folder = self.account.calendar if not request.calendar_id else self._resolve_folder(request.calendar_id)
        start = self._to_ews_datetime(request.start)
        end = self._to_ews_datetime(request.end)
        if request.include_recurring:
            qs = folder.view(start=start, end=end).only(*_EVENT_FIELDS)
        else:
            # A restriction-based query returns recurring masters unexpanded
            qs = folder.filter(start__lt=end, end__gt=start).only(*_EVENT_FIELDS)
        try:
            items = list(qs)
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc
        return [self._to_calendar_event(item) for item in items]

    def get_event(self, request: GetEventRequest) -> CalendarEvent:
        item = self._fetch_item(request.id, folder=self.account.calendar)
        return self._to_calendar_event(item)

    def create_event(self, request: CreateEventRequest) -> CreateEventResult:
        folder = self.account.calendar if not request.calendar_id else self._resolve_folder(request.calendar_id)
        start = self._to_ews_datetime(request.start)
        end = self._to_ews_datetime(request.end)
        item = CalendarItem(
            account=self.account,
            folder=folder,
            subject=request.subject,
            start=start,
            end=end,
            location=request.location,
            body=request.body,
            required_attendees=[Attendee(mailbox=self._mailbox(address)) for address in request.attendees],
            is_all_day=request.is_all_day,
            categories=request.categories,
            importance=request.importance.capitalize(),
            reminder_minutes_before_start=request.reminder_minutes,
        )
        try:
            item.save(send_meeting_invitations="SendToAllAndSaveCopy" if request.attendees else "SendToNone")
            warnings = []
            if request.recurrence:
                warnings.append("recurrence is not supported yet and was ignored")
            if request.online_meeting:
                warnings.append("online_meeting is not supported via EWS and was ignored")
            return CreateEventResult(
                id=item.id or "",
                status="created",
                subject=request.subject,
                start=start,
                end=end,
                invite_sent=bool(request.attendees),
                warning="; ".join(warnings) or None,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc

    def update_event(self, request: UpdateEventRequest) -> ActionResult:
        item = self._fetch_item(request.id, folder=self.account.calendar)
        updated_fields: list[str] = []
        for field in ["subject", "start", "end", "location", "body", "reminder_minutes"]:
            value = getattr(request, field)
            if value is not None:
                if field in {"start", "end"}:
                    value = self._to_ews_datetime(value)
                target = "reminder_minutes_before_start" if field == "reminder_minutes" else field
                setattr(item, target, value)
                updated_fields.append(target)
        if request.add_attendees:
            current = list(getattr(item, "required_attendees", None) or [])
            current.extend(Attendee(mailbox=self._mailbox(address)) for address in request.add_attendees)
            item.required_attendees = current
            updated_fields.append("required_attendees")
        if request.remove_attendees:
            remove_set = {address.lower() for address in request.remove_attendees}
            item.required_attendees = [
                attendee
                for attendee in getattr(item, "required_attendees", None) or []
                if getattr(getattr(attendee, "mailbox", None), "email_address", "").lower() not in remove_set
            ]
            if "required_attendees" not in updated_fields:
                updated_fields.append("required_attendees")
        if not updated_fields:
            return ActionResult(id=request.id, status="updated", updated_fields=[])
        try:
            invitations = {
                "none": "SendToNone",
                "all": "SendToAllAndSaveCopy",
                "modified": "SendOnlyToChanged",
            }[request.send_updates]
            item.save(update_fields=updated_fields, send_meeting_invitations=invitations)
            return ActionResult(id=request.id, status="updated", updated_fields=updated_fields)
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def delete_event(self, request: DeleteEventRequest) -> ActionResult:
        item = self._fetch_item(request.id, folder=self.account.calendar)
        try:
            item.delete(
                send_meeting_cancellations="SendToAllAndSaveCopy" if request.notify_attendees else "SendToNone"
            )
            warning = None
            if request.cancel_message:
                warning = "cancel_message is not supported via EWS delete and was ignored"
            return ActionResult(id=request.id, status="deleted", warning=warning)
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def respond_to_invite(self, request: RespondToInviteRequest) -> ActionResult:
        item = self._fetch_item(request.id, folder=self.account.calendar)
        try:
            if request.response == "accept":
                item.accept(body=request.message)
            elif request.response == "tentative":
                item.tentatively_accept(body=request.message)
            else:
                item.decline(body=request.message)
            return ActionResult(id=request.id, status=request.response)
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def find_free_slots(self, request: FindFreeSlotsRequest) -> list[FreeSlot]:
        start = self._to_ews_datetime(request.start)
        end = self._to_ews_datetime(request.end)
        attendees = [str(address) for address in request.attendees]
        try:
            views = self.account.protocol.get_free_busy_info(
                accounts=[(address, "Required", False) for address in attendees],
                start=start,
                end=end,
                merged_free_busy_interval=request.duration,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc

        merged_views = [getattr(view, "merged", "") or "" for view in views]
        if not merged_views:
            return []
        slots: list[FreeSlot] = []
        interval = timedelta(minutes=request.duration)
        cursor = start
        index = 0
        while cursor + interval <= end:
            slot_end = cursor + interval
            busy = [
                attendee
                for attendee, merged in zip(attendees, merged_views)
                if index < len(merged) and merged[index] != "0"
            ]
            if not busy and self._within_work_hours(cursor, slot_end, request.work_hours):
                slots.append(FreeSlot(start=cursor, end=slot_end, all_available=True, busy_attendees=[]))
            cursor = slot_end
            index += 1
        return slots

    @staticmethod
    def _within_work_hours(start: datetime, end: datetime, work_hours: Any) -> bool:
        if work_hours is None:
            return True
        try:
            wh_start = time.fromisoformat(work_hours.start)
            wh_end = time.fromisoformat(work_hours.end)
        except ValueError:
            return True
        return start.date() == end.date() and start.time() >= wh_start and end.time() <= wh_end

    def get_my_availability(self, request: ListEventsRequest) -> AvailabilityResult:
        # Declined events do not block availability. Note: self-created
        # appointments may report my_response "unknown", so only "decline"
        # is safe to exclude here.
        events = sorted(
            (event for event in self.list_events(request) if event.my_response != "decline"),
            key=lambda event: event.start,
        )
        busy_slots = [{"start": event.start, "end": event.end, "subject": event.subject} for event in events]
        window_start = self._to_ews_datetime(request.start)
        window_end = self._to_ews_datetime(request.end)
        tz = window_start.tzinfo
        free_slots: list[FreeSlot] = []
        cursor = window_start
        for event in events:
            if event.start > cursor:
                free_slots.append(FreeSlot(start=cursor, end=min(event.start, window_end).astimezone(tz)))
            cursor = max(cursor, event.end.astimezone(tz))
            if cursor >= window_end:
                break
        if cursor < window_end:
            free_slots.append(FreeSlot(start=cursor, end=window_end))
        return AvailabilityResult(free_slots=free_slots, busy_slots=busy_slots)

    def list_calendars(self) -> list[CalendarInfo]:
        default = self.account.calendar
        calendars = [
            CalendarInfo(
                id=default.id,
                name=default.name,
                is_default=True,
                owner_email=self.account.primary_smtp_address,
            )
        ]
        # Secondary calendars live as subfolders of the default Calendar folder
        for child in getattr(default, "children", None) or []:
            calendars.append(
                CalendarInfo(
                    id=getattr(child, "id", "") or "",
                    name=child.name,
                    is_default=False,
                    owner_email=self.account.primary_smtp_address,
                )
            )
        return calendars

    def _contact_summary_from_contact(self, contact: Contact, source: str) -> ContactSummary:
        emails = [entry.email for entry in getattr(contact, "email_addresses", None) or [] if getattr(entry, "email", None)]
        phones = [entry.phone_number for entry in getattr(contact, "phone_numbers", None) or [] if getattr(entry, "phone_number", None)]
        return ContactSummary(
            id=contact.id,
            display_name=contact.display_name or contact.file_as or "",
            email_addresses=emails,
            phone_numbers=phones,
            company=getattr(contact, "company_name", None),
            job_title=getattr(contact, "job_title", None),
            department=getattr(contact, "department", None),
            source=source,
        )

    def search_contacts(self, request: SearchContactsRequest) -> list[ContactSummary]:
        results: list[ContactSummary] = []
        if request.source in {"personal", "all"}:
            try:
                qs = self.account.contacts.filter(display_name__icontains=request.query).only(*_CONTACT_SUMMARY_FIELDS)[: request.limit]
                results.extend(self._contact_summary_from_contact(contact, "personal") for contact in qs)
            except Exception as exc:  # noqa: BLE001
                raise self._map_exception(exc) from exc
        if request.source in {"gal", "all"} and len(results) < request.limit:
            try:
                resolved = ResolveNames(protocol=self.account.protocol).call(
                    unresolved_entries=[request.query],
                    return_full_contact_data=True,
                    search_scope="ActiveDirectory",
                    contact_data_shape="AllProperties",
                )
                for mailbox, contact in resolved:
                    if contact is not None and getattr(contact, "id", None):
                        results.append(self._contact_summary_from_contact(contact, "gal"))
                    elif mailbox is not None:
                        results.append(
                            ContactSummary(
                                id=getattr(mailbox, "email_address", None) or request.query,
                                display_name=getattr(mailbox, "name", None) or getattr(mailbox, "email_address", None) or request.query,
                                email_addresses=[getattr(mailbox, "email_address", None)] if getattr(mailbox, "email_address", None) else [],
                                phone_numbers=[],
                                source="gal",
                            )
                        )
                    if len(results) >= request.limit:
                        break
            except Exception as exc:  # noqa: BLE001
                raise self._map_exception(exc) from exc
        return results[: request.limit]

    def get_contact(self, request: GetContactRequest) -> ContactFull:
        item = self._fetch_item(request.id, folder=self.account.contacts)
        birthday = getattr(item, "birthday", None)
        if isinstance(birthday, datetime):
            birthday = birthday.date()
        return ContactFull(
            id=item.id,
            display_name=item.display_name or item.file_as or "",
            first_name=getattr(item, "given_name", None),
            last_name=getattr(item, "surname", None),
            email_addresses=[
                {"type": entry.label, "address": entry.email}
                for entry in getattr(item, "email_addresses", None) or []
                if getattr(entry, "email", None)
            ],
            phone_numbers=[
                {"type": entry.label, "number": entry.phone_number}
                for entry in getattr(item, "phone_numbers", None) or []
                if getattr(entry, "phone_number", None)
            ],
            addresses=[
                {
                    "type": entry.label,
                    "street": entry.street,
                    "city": entry.city,
                    "state": entry.state,
                    "postal_code": entry.zipcode,
                    "country": entry.country,
                }
                for entry in getattr(item, "physical_addresses", None) or []
            ],
            company=getattr(item, "company_name", None),
            job_title=getattr(item, "job_title", None),
            department=getattr(item, "department", None),
            manager=getattr(item, "manager", None),
            notes=getattr(item, "notes", None),
            birthday=birthday,
            source="personal",
        )

    def create_contact(self, request: CreateContactRequest) -> ActionResult:
        contact = Contact(
            account=self.account,
            folder=self.account.contacts,
            display_name=request.display_name,
            given_name=request.first_name,
            surname=request.last_name,
            company_name=request.company,
            job_title=request.job_title,
            notes=request.notes,
            email_addresses=[IndexedEmailAddress(label="EmailAddress1", email=str(request.email))] if request.email else [],
            phone_numbers=[IndexedPhoneNumber(label="PrimaryPhone", phone_number=request.phone)] if request.phone else [],
        )
        try:
            contact.save()
            return ActionResult(id=contact.id or "", status="created")
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc

    def update_contact(self, request: UpdateContactRequest) -> ActionResult:
        contact = self._fetch_item(request.id, folder=self.account.contacts)
        updated_fields: list[str] = []
        field_map = {
            "display_name": "display_name",
            "first_name": "given_name",
            "last_name": "surname",
            "company": "company_name",
            "job_title": "job_title",
            "notes": "notes",
        }
        for request_field, item_field in field_map.items():
            value = getattr(request, request_field)
            if value is not None:
                setattr(contact, item_field, value)
                updated_fields.append(item_field)
        if request.email is not None:
            contact.email_addresses = [IndexedEmailAddress(label="EmailAddress1", email=str(request.email))]
            updated_fields.append("email_addresses")
        if request.phone is not None:
            contact.phone_numbers = [IndexedPhoneNumber(label="PrimaryPhone", phone_number=request.phone)]
            updated_fields.append("phone_numbers")
        if not updated_fields:
            return ActionResult(id=request.id, status="updated", updated_fields=[])
        try:
            contact.save(update_fields=updated_fields)
            return ActionResult(id=request.id, status="updated", updated_fields=updated_fields)
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def delete_contact(self, request: DeleteContactRequest) -> ActionResult:
        contact = self._fetch_item(request.id, folder=self.account.contacts)
        try:
            contact.move_to_trash()
            return ActionResult(id=request.id, status="deleted")
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    # --- Tasks -------------------------------------------------------------

    _TASK_STATUS_MAP = {
        "NotStarted": "not_started",
        "InProgress": "in_progress",
        "Completed": "completed",
        "WaitingOnOthers": "waiting_on_others",
        "Deferred": "deferred",
    }
    _TASK_STATUS_REVERSE = {v: k for k, v in _TASK_STATUS_MAP.items()}

    def _normalize_task_status(self, value: Any) -> str:
        normalized = str(value or "NotStarted")
        return self._TASK_STATUS_MAP.get(normalized, "not_started")

    def _to_task_summary(self, item: Any) -> TaskSummary:
        return TaskSummary(
            id=item.id,
            subject=item.subject or "",
            status=self._normalize_task_status(getattr(item, "status", None)),
            percent_complete=int(float(getattr(item, "percent_complete", 0) or 0)),
            due_date=self._date_from_ews(getattr(item, "due_date", None)),
            start_date=self._date_from_ews(getattr(item, "start_date", None)),
            is_complete=bool(getattr(item, "is_complete", False)),
            has_attachments=bool(getattr(item, "has_attachments", False)),
            importance=self._normalize_importance(getattr(item, "importance", None)),
            categories=list(getattr(item, "categories", None) or []),
            reminder_is_set=bool(getattr(item, "reminder_is_set", False)),
        )

    def _to_task_full(self, item: Any) -> TaskFull:
        body_text, body_html = self._extract_message_body(item)
        return TaskFull(
            **self._to_task_summary(item).model_dump(by_alias=True),
            body=body_text or None,
            body_type="html" if body_html else "text",
            complete_date=self._date_from_ews(getattr(item, "complete_date", None)),
            reminder_minutes_before_start=getattr(item, "reminder_minutes_before_start", None),
            companies=list(getattr(item, "companies", None) or []),
            contacts=list(getattr(item, "contacts", None) or []),
            billing_information=getattr(item, "billing_information", None),
            owner=getattr(item, "owner", None),
            last_modified_time=getattr(item, "last_modified_time", None),
        )

    @staticmethod
    def _date_from_ews(value: Any) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return None

    def list_tasks(self, request: ListTasksRequest) -> list[TaskSummary]:
        qs = self.account.tasks.all().only(*_TASK_SUMMARY_FIELDS).order_by("-due_date")
        filters: dict[str, Any] = {}
        if request.status:
            filters["status"] = self._TASK_STATUS_REVERSE[request.status]
        if request.category:
            filters["categories__contains"] = request.category
        if request.due_before:
            filters["due_date__lt"] = request.due_before
        if request.due_after:
            filters["due_date__gte"] = request.due_after
        if request.incomplete_only:
            filters["is_complete"] = False
        if filters:
            qs = qs.filter(**filters)
        try:
            items = list(qs[request.offset : request.offset + request.limit])
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc
        return [self._to_task_summary(item) for item in items]

    def get_task(self, request: GetTaskRequest) -> TaskFull:
        item = self._fetch_item(request.id, folder=self.account.tasks)
        return self._to_task_full(item)

    def create_task(self, request: CreateTaskRequest) -> ActionResult:
        body: Any = None
        if request.body:
            body = HTMLBody(request.body) if request.body_type == "html" else request.body
        task = Task(
            account=self.account,
            folder=self.account.tasks,
            subject=request.subject,
            body=body,
            start_date=request.start_date,
            due_date=request.due_date,
            categories=request.categories or None,
            importance=request.importance.capitalize(),
        )
        if request.reminder_minutes is not None:
            task.reminder_is_set = True
            task.reminder_minutes_before_start = request.reminder_minutes
        try:
            task.save()
            return ActionResult(id=task.id or "", status="created")
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc) from exc

    def update_task(self, request: UpdateTaskRequest) -> ActionResult:
        from decimal import Decimal

        item = self._fetch_item(request.id, folder=self.account.tasks)
        updated_fields: list[str] = []
        if request.subject is not None:
            item.subject = request.subject
            updated_fields.append("subject")
        if request.body is not None:
            item.body = HTMLBody(request.body) if request.body_type == "html" else request.body
            updated_fields.append("body")
        if request.start_date is not None:
            item.start_date = request.start_date
            updated_fields.append("start_date")
        if request.due_date is not None:
            item.due_date = request.due_date
            updated_fields.append("due_date")
        if request.percent_complete is not None:
            item.percent_complete = Decimal(request.percent_complete)
            updated_fields.append("percent_complete")
            if request.percent_complete == 100:
                item.status = "Completed"
                if "status" not in updated_fields:
                    updated_fields.append("status")
            elif 0 < request.percent_complete < 100:
                if str(getattr(item, "status", "")) in ("NotStarted", "Completed"):
                    item.status = "InProgress"
                    if "status" not in updated_fields:
                        updated_fields.append("status")
        if request.reminder_minutes is not None:
            item.reminder_is_set = True
            item.reminder_minutes_before_start = request.reminder_minutes
            updated_fields.append("reminder_is_set")
            updated_fields.append("reminder_minutes_before_start")
        if request.categories is not None:
            item.categories = request.categories or None
            updated_fields.append("categories")
        if request.importance is not None:
            item.importance = request.importance.capitalize()
            updated_fields.append("importance")
        if not updated_fields:
            return ActionResult(id=request.id, status="updated", updated_fields=[])
        try:
            item.save(update_fields=updated_fields)
            return ActionResult(id=request.id, status="updated", updated_fields=updated_fields)
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def complete_task(self, request: CompleteTaskRequest) -> ActionResult:
        from decimal import Decimal

        item = self._fetch_item(request.id, folder=self.account.tasks)
        try:
            item.status = "Completed"
            item.percent_complete = Decimal(100)
            # complete_date is server-computed when status becomes Completed;
            # writing it directly raises "read-only field".
            item.save(update_fields=["status", "percent_complete"])
            return ActionResult(id=request.id, status="completed", updated_fields=["status", "percent_complete"])
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc

    def delete_task(self, request: DeleteTaskRequest) -> ActionResult:
        item = self._fetch_item(request.id, folder=self.account.tasks)
        try:
            if request.hard_delete:
                item.delete()
            else:
                item.move_to_trash()
            return ActionResult(id=request.id, status="deleted")
        except Exception as exc:  # noqa: BLE001
            raise self._map_exception(exc, item_id=request.id) from exc
