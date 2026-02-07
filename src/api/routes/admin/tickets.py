"""
Admin feedback ticket routes.
"""

from fastapi import APIRouter, Request, HTTPException, status
from typing import List, Optional
from src.api.schemas.admin import TicketResponse, TicketStatusUpdate, NoteResponse, InternalNoteCreate
from src.api.schemas.common import SuccessResponse
from .utils import check_host_restriction, get_admin_from_token
import utils.logger as logger

router = APIRouter()

@router.get("/tickets", response_model=List[TicketResponse])
async def get_tickets(request: Request, status_filter: Optional[str] = None, limit: int = 50, offset: int = 0):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    try:
        tickets = admin.get_feedback_tickets(status_filter, limit, offset)
        return [
            TicketResponse(
                id=str(t.id), user_id=str(t.user_id), username=t.username,
                content=t.content, category=t.category, rating=t.rating,
                status=t.status, created_at=t.created_at, resolved_at=t.resolved_at,
                resolved_by=str(t.resolved_by) if t.resolved_by else None
            )
            for t in tickets
        ]
    except Exception as e:
        logger.error(f"Tickets error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})

@router.get("/tickets/{ticket_id}", response_model=TicketResponse)
async def get_ticket(ticket_id: int, request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    ticket = admin.get_ticket(ticket_id)
    if not ticket: raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Ticket not found"}})
    return TicketResponse(
        id=str(ticket.id), user_id=str(ticket.user_id), username=ticket.username,
        content=ticket.content, category=ticket.category, rating=ticket.rating,
        status=ticket.status, created_at=ticket.created_at, resolved_at=ticket.resolved_at,
        resolved_by=str(ticket.resolved_by) if ticket.resolved_by else None
    )

@router.patch("/tickets/{ticket_id}/status", response_model=SuccessResponse)
async def update_ticket_status(ticket_id: int, update: TicketStatusUpdate, request: Request):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin
    if not admin.get_ticket(ticket_id): raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Ticket not found"}})
    admin.update_ticket_status(ticket_id, update.status, admin_id)
    return SuccessResponse(success=True)

@router.get("/tickets/{ticket_id}/notes", response_model=List[NoteResponse])
async def get_ticket_notes(ticket_id: int, request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    if not admin.get_ticket(ticket_id): raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Ticket not found"}})
    notes = admin.get_ticket_notes(ticket_id)
    return [NoteResponse(id=str(n.id), ticket_id=str(n.ticket_id), admin_id=str(n.admin_id), admin_username=n.admin_username, content=n.content, created_at=n.created_at) for n in notes]

@router.post("/tickets/{ticket_id}/notes", response_model=NoteResponse)
async def add_ticket_note(ticket_id: int, note: InternalNoteCreate, request: Request):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin
    if not admin.get_ticket(ticket_id): raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Ticket not found"}})
    new_note = admin.add_internal_note(ticket_id, admin_id, note.content)
    if not new_note: raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Failed to create note"}})
    return NoteResponse(id=str(new_note.id), ticket_id=str(new_note.ticket_id), admin_id=str(new_note.admin_id), admin_username=new_note.admin_username, content=new_note.content, created_at=new_note.created_at)
