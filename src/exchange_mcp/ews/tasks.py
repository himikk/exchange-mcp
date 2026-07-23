from __future__ import annotations

from pydantic import ValidationError

from ..errors import validation_error_from_pydantic
from ..exchange_client import ExchangeClient
from ..models import (
    CompleteTaskRequest,
    CreateTaskRequest,
    DeleteTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    UpdateTaskRequest,
    dump_model,
)


def list_tasks(client: ExchangeClient, arguments: dict) -> list[dict]:
    try:
        request = ListTasksRequest.model_validate(arguments)
    except ValidationError as exc:
        raise validation_error_from_pydantic(exc) from exc
    return dump_model(client.list_tasks(request))


def get_task(client: ExchangeClient, arguments: dict) -> dict:
    try:
        request = GetTaskRequest.model_validate(arguments)
    except ValidationError as exc:
        raise validation_error_from_pydantic(exc) from exc
    return dump_model(client.get_task(request))


def create_task(client: ExchangeClient, arguments: dict) -> dict:
    try:
        request = CreateTaskRequest.model_validate(arguments)
    except ValidationError as exc:
        raise validation_error_from_pydantic(exc) from exc
    return dump_model(client.create_task(request))


def update_task(client: ExchangeClient, arguments: dict) -> dict:
    try:
        request = UpdateTaskRequest.model_validate(arguments)
    except ValidationError as exc:
        raise validation_error_from_pydantic(exc) from exc
    return dump_model(client.update_task(request))


def complete_task(client: ExchangeClient, arguments: dict) -> dict:
    try:
        request = CompleteTaskRequest.model_validate(arguments)
    except ValidationError as exc:
        raise validation_error_from_pydantic(exc) from exc
    return dump_model(client.complete_task(request))


def delete_task(client: ExchangeClient, arguments: dict) -> dict:
    try:
        request = DeleteTaskRequest.model_validate(arguments)
    except ValidationError as exc:
        raise validation_error_from_pydantic(exc) from exc
    return dump_model(client.delete_task(request))
