"""
Poll routes - Message poll endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.polls import (
    PollCreateRequest,
    PollVoteRequest,
    PollResponse,
    PollResultsResponse,
    PollOptionResponse,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.polls import (
    PollResultsVisibility,
    PollNotFoundError,
    PollOptionNotFoundError,
    PollEndedError,
    InvalidPollQuestionError,
    InvalidPollOptionError,
    PollOptionLimitError,
    InvalidPollDurationError,
    AlreadyVotedError,
    MultipleVoteNotAllowedError,
    PermissionDeniedError,
    MessageNotFoundError,
)

router = APIRouter(prefix="/polls", tags=["Polls"])


def _poll_to_response(poll, include_vote_counts: bool = False) -> PollResponse:
    options = [
        PollOptionResponse(
            id=opt.id,
            poll_id=opt.poll_id,
            text=opt.text,
            position=opt.position,
            vote_count=opt.vote_count if include_vote_counts else None,
        )
        for opt in poll.options
    ]
    return PollResponse(
        id=poll.id,
        message_id=poll.message_id,
        question=poll.question,
        created_by=poll.created_by,
        created_at=poll.created_at,
        ends_at=poll.ends_at,
        ended_at=poll.ended_at,
        allow_multiple_choice=poll.allow_multiple_choice,
        results_visibility=poll.results_visibility.value
        if isinstance(poll.results_visibility, PollResultsVisibility)
        else str(poll.results_visibility),
        options=options,
        total_votes=poll.total_votes,
        is_ended=poll.is_ended,
    )


def _results_to_response(results) -> PollResultsResponse:
    poll_response = _poll_to_response(results.poll, include_vote_counts=False)
    options = [
        PollOptionResponse(
            id=opt.id,
            poll_id=opt.poll_id,
            text=opt.text,
            position=opt.position,
            vote_count=opt.vote_count,
        )
        for opt in results.options
    ]
    return PollResultsResponse(
        poll=poll_response,
        options=options,
        total_votes=results.total_votes,
        user_voted=results.user_voted,
        user_votes=results.user_votes,
    )


@router.post(
    "",
    response_model=PollResponse,
    summary="Create poll",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid poll request"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_poll(
    body: PollCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> PollResponse:
    polls_module = api.get_polls()
    messaging = api.get_messaging()
    if not polls_module:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Polls module not available"}},
        )

    try:
        poll = polls_module.create_poll(
            user_id=current_user.user_id,
            message_id=int(body.message_id),
            question=body.question,
            options=list(body.options),
            duration_hours=body.duration_hours,
            allow_multiple_choice=body.allow_multiple_choice,
            results_visibility=PollResultsVisibility(body.results_visibility),
        )

        if messaging:
            try:
                messaging.update_message_metadata(
                    poll.message_id, {"poll_id": poll.id}
                )
            except Exception:
                logger.debug("Failed to update poll metadata on message")

        return _poll_to_response(poll)
    except (
        InvalidPollQuestionError,
        InvalidPollOptionError,
        PollOptionLimitError,
        InvalidPollDurationError,
        AlreadyVotedError,
        MultipleVoteNotAllowedError,
    ) as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )
    except MessageNotFoundError as e:
        raise HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": str(e)}}
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create poll: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.get(
    "/{poll_id}",
    response_model=PollResponse,
    summary="Get poll",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid poll ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Poll not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_poll(
    poll_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> PollResponse:
    polls_module = api.get_polls()
    if not polls_module:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Polls module not available"}},
        )
    try:
        pid = int(poll_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": "Invalid poll ID"}}
        )

    try:
        poll = polls_module.get_poll(pid, current_user.user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")
        return _poll_to_response(poll)
    except PollNotFoundError as e:
        raise HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": str(e)}}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get poll {poll_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.get(
    "/{poll_id}/results",
    response_model=PollResultsResponse,
    summary="Get poll results",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid poll ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Poll not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_poll_results(
    poll_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> PollResultsResponse:
    polls_module = api.get_polls()
    if not polls_module:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Polls module not available"}},
        )
    try:
        pid = int(poll_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": "Invalid poll ID"}}
        )

    try:
        results = polls_module.get_results(pid, current_user.user_id)
        return _results_to_response(results)
    except PollNotFoundError as e:
        raise HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": str(e)}}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get poll results {poll_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.post(
    "/{poll_id}/vote",
    response_model=PollResultsResponse,
    summary="Vote on poll",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid poll vote"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Poll not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def vote_on_poll(
    poll_id: str,
    body: PollVoteRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> PollResultsResponse:
    polls_module = api.get_polls()
    if not polls_module:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Polls module not available"}},
        )
    try:
        pid = int(poll_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": "Invalid poll ID"}}
        )

    try:
        results = polls_module.vote(
            user_id=current_user.user_id,
            poll_id=pid,
            option_ids=[int(x) for x in body.option_ids],
        )
        return _results_to_response(results)
    except (
        PollOptionNotFoundError,
        PollEndedError,
        AlreadyVotedError,
        MultipleVoteNotAllowedError,
    ) as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )
    except PollNotFoundError as e:
        raise HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": str(e)}}
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to vote on poll {poll_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.post(
    "/{poll_id}/close",
    response_model=PollResponse,
    summary="Close poll",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid poll request"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Poll not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def close_poll(
    poll_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> PollResponse:
    polls_module = api.get_polls()
    if not polls_module:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Polls module not available"}},
        )
    try:
        pid = int(poll_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": "Invalid poll ID"}}
        )

    try:
        poll = polls_module.close_poll(current_user.user_id, pid)
        return _poll_to_response(poll)
    except (PollEndedError, InvalidPollDurationError) as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )
    except PollNotFoundError as e:
        raise HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": str(e)}}
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to close poll {poll_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}}
        )


@router.delete(
    "/{poll_id}",
    response_model=SuccessResponse,
    summary="Delete poll",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid poll ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Poll not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_poll(
    poll_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    polls_module = api.get_polls()
    if not polls_module:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Polls module not available"}},
        )
    try:
        pid = int(poll_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": "Invalid poll ID"}}
        )

    try:
        polls_module.delete_poll(current_user.user_id, pid)
        return SuccessResponse(success=True)
    except PollNotFoundError as e:
        raise HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": str(e)}}
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete poll {poll_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}}
        )
