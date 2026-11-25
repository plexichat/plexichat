"""
Admin feedback ticket routes.
"""

from fastapi import APIRouter, Request, HTTPException, Query
from typing import List, Optional
from src.api.schemas.admin import (
    TicketResponse,
    TicketStatusUpdate,
    NoteResponse,
    InternalNoteCreate,
)
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeIDParam,
    SuccessResponse,
)
from .utils import check_host_restriction, require_admin_permission
import utils.logger as logger

router = APIRouter()


def _ticket_exists(ticket_id: int) -> bool:
    """Cheap existence probe for a feedback ticket.

    The write endpoints only need to know whether the ticket exists, so a
    ``SELECT 1`` avoids the full ``feedback``/``auth_users`` JOIN, the dozen
    selected columns and the FeedbackTicket construction that
    ``admin.get_ticket`` performs purely to be thrown away.
    """
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    return db.fetch_one("SELECT 1 FROM feedback WHERE id = ?", (ticket_id,)) is not None


@router.get(
    "/tickets",
    response_model=List[TicketResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_tickets(
    request: Request,
    status_filter: Optional[str] = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
):
    """
    Retrieve a list of user feedback tickets.

    Allows filtering by status and supports pagination.
    """
    check_host_restriction(request)
    require_admin_permission(request, "tickets.read")
    from src.core import admin

    try:
        # TicketResponse has no internal_notes field, so decrypting each row's
        # note in the core loader is pure waste on this listing path. Call the
        # core loader directly to pass the opt-out without changing the shared
        # admin wrapper; DB resolution is identical to that wrapper's.
        tickets = admin.get_feedback_tickets(
            status_filter,
            limit,
            offset,
            include_internal_notes=False,
        )
        return [
            TicketResponse(
                id=str(t.id),
                user_id=str(t.user_id),
                username=t.username,
                content=t.content,
                category=t.category,
                rating=t.rating,
                status=t.status,
                created_at=t.created_at,
                resolved_at=t.resolved_at,
                resolved_by=str(t.resolved_by) if t.resolved_by else None,
            )
            for t in tickets
        ]
    except Exception as e:
        logger.error(f"Tickets error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/tickets/{ticket_id}",
    response_model=TicketResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Ticket not found"},
    },
)
async def get_ticket(ticket_id: SnowflakeIDParam, request: Request):
    """
    Retrieve detailed information for a specific feedback ticket.
    """
    check_host_restriction(request)
    require_admin_permission(request, "tickets.read")
    from src.core import admin

    ticket = admin.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Ticket not found"}},
        )
    return TicketResponse(
        id=str(ticket.id),
        user_id=str(ticket.user_id),
        username=ticket.username,
        content=ticket.content,
        category=ticket.category,
        rating=ticket.rating,
        status=ticket.status,
        created_at=ticket.created_at,
        resolved_at=ticket.resolved_at,
        resolved_by=str(ticket.resolved_by) if ticket.resolved_by else None,
    )


@router.patch(
    "/tickets/{ticket_id}/status",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Ticket not found"},
    },
)
async def update_ticket_status(
    ticket_id: SnowflakeIDParam,
    update: TicketStatusUpdate,
    request: Request,
):
    """
    Update the processing status of a feedback ticket.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "tickets.close")
    from src.core import admin

    if not _ticket_exists(ticket_id):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Ticket not found"}},
        )
    admin.update_ticket_status(ticket_id, update.status, admin_id)
    return SuccessResponse(success=True, message=None)


@router.get(
    "/tickets/{ticket_id}/notes",
    response_model=List[NoteResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Ticket not found"},
    },
)
async def get_ticket_notes(ticket_id: SnowflakeIDParam, request: Request):
    """
    Retrieve all internal administrator notes associated with a ticket.
    """
    check_host_restriction(request)
    require_admin_permission(request, "tickets.read")
    from src.core import admin

    # Existence only: the full ``get_ticket`` fetch used here selected the
    # whole feedback/auth_users JOIN (including note ciphertext) and, with
    # encryption on, decrypted a note the NoteResponse list never carries.
    # A ``SELECT 1`` probe answers the 404 check without any of that work.
    if not _ticket_exists(ticket_id):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Ticket not found"}},
        )
    notes = admin.get_ticket_notes(ticket_id)
    return [
        NoteResponse(
            id=str(n.id),
            ticket_id=str(n.ticket_id),
            admin_id=str(n.admin_id),
            admin_username=n.admin_username,
            content=n.content,
            created_at=n.created_at,
        )
        for n in notes
    ]


@router.post(
    "/tickets/{ticket_id}/notes",
    response_model=NoteResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Ticket not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_ticket_note(
    ticket_id: SnowflakeIDParam, note: InternalNoteCreate, request: Request
):
    """
    Add a new internal administrator note to a ticket.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "tickets.respond")
    from src.core import admin

    if not _ticket_exists(ticket_id):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Ticket not found"}},
        )
    new_note = admin.add_internal_note(ticket_id, admin_id, note.content)
    if not new_note:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Failed to create note"}},
        )
    return NoteResponse(
        id=str(new_note.id),
        ticket_id=str(new_note.ticket_id),
        admin_id=str(new_note.admin_id),
        admin_username=new_note.admin_username,
        content=new_note.content,
        created_at=new_note.created_at,
    )
