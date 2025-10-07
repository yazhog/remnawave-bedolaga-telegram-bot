from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.ticket import TicketCRUD
from app.database.models import Ticket, TicketMessage, TicketStatus

from ..dependencies import get_db_session, require_api_token
from ..schemas.tickets import (
    TicketMessageResponse,
    TicketPriorityUpdateRequest,
    TicketReplyBlockRequest,
    TicketResponse,
    TicketStatusUpdateRequest,
)

router = APIRouter()


def _serialize_message(message: TicketMessage) -> TicketMessageResponse:
    return TicketMessageResponse(
        id=message.id,
        user_id=message.user_id,
        message_text=message.message_text,
        is_from_admin=message.is_from_admin,
        has_media=message.has_media,
        media_type=message.media_type,
        media_caption=message.media_caption,
        created_at=message.created_at,
    )


def _serialize_ticket(ticket: Ticket, include_messages: bool = False) -> TicketResponse:
    messages = []
    if include_messages:
        messages = sorted(ticket.messages, key=lambda m: m.created_at)

    return TicketResponse(
        id=ticket.id,
        user_id=ticket.user_id,
        title=ticket.title,
        status=ticket.status,
        priority=ticket.priority,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        closed_at=ticket.closed_at,
        user_reply_block_permanent=ticket.user_reply_block_permanent,
        user_reply_block_until=ticket.user_reply_block_until,
        messages=[_serialize_message(message) for message in messages],
    )


@router.get("", response_model=list[TicketResponse])
async def list_tickets(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: Optional[TicketStatus] = Query(default=None, alias="status"),
    priority: Optional[str] = Query(default=None),
    user_id: Optional[int] = Query(default=None),
) -> list[TicketResponse]:
    status_value = status_filter.value if status_filter else None

    if user_id:
        tickets = await TicketCRUD.get_user_tickets(
            db,
            user_id=user_id,
            status=status_value,
            limit=limit,
            offset=offset,
        )
    else:
        tickets = await TicketCRUD.get_all_tickets(
            db,
            status=status_value,
            priority=priority,
            limit=limit,
            offset=offset,
        )

    return [_serialize_ticket(ticket) for ticket in tickets]


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: int,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return _serialize_ticket(ticket, include_messages=True)


@router.post("/{ticket_id}/status", response_model=TicketResponse)
async def update_ticket_status(
    ticket_id: int,
    payload: TicketStatusUpdateRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    try:
        status_value = TicketStatus(payload.status).value
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid ticket status") from error

    closed_at = datetime.utcnow() if status_value == TicketStatus.CLOSED.value else None
    success = await TicketCRUD.update_ticket_status(db, ticket_id, status_value, closed_at)
    if not success:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    return _serialize_ticket(ticket, include_messages=True)


@router.post("/{ticket_id}/priority", response_model=TicketResponse)
async def update_ticket_priority(
    ticket_id: int,
    payload: TicketPriorityUpdateRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    allowed_priorities = {"low", "normal", "high", "urgent"}
    if payload.priority not in allowed_priorities:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid priority")

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    ticket.priority = payload.priority
    ticket.updated_at = datetime.utcnow()
    await db.commit()

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    return _serialize_ticket(ticket, include_messages=True)


@router.post("/{ticket_id}/reply-block", response_model=TicketResponse)
async def update_reply_block(
    ticket_id: int,
    payload: TicketReplyBlockRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    until = payload.until
    if not payload.permanent and until and until <= datetime.utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Block expiration must be in the future")

    success = await TicketCRUD.set_user_reply_block(
        db,
        ticket_id,
        permanent=payload.permanent,
        until=until,
    )
    if not success:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    return _serialize_ticket(ticket, include_messages=True)


@router.delete("/{ticket_id}/reply-block", response_model=TicketResponse)
async def clear_reply_block(
    ticket_id: int,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    success = await TicketCRUD.set_user_reply_block(
        db,
        ticket_id,
        permanent=False,
        until=None,
    )
    if not success:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    return _serialize_ticket(ticket, include_messages=True)
