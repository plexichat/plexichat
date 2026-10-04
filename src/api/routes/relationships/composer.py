"""
RelationshipsRouter composer - combines all mixins into the final class.

The RelationshipsRouter class registers all relationship route handlers on
a FastAPI APIRouter instance via register_routes().
"""

from typing import List

from fastapi import APIRouter

from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.api.schemas.relationships import (
    DetailedRelationshipInfo,
    RelationshipResponse,
)

from .base import RelationshipsBase
from .listing import ListingMixin
from .friend_requests import FriendRequestMixin
from .blocking import BlockingMixin
from .deletion import DeletionMixin


class RelationshipsRouter(
    ListingMixin,
    FriendRequestMixin,
    BlockingMixin,
    DeletionMixin,
    RelationshipsBase,
):
    def register_routes(self, router: APIRouter) -> None:
        router.get(
            "/@me",
            response_model=List[DetailedRelationshipInfo],
            summary="Get relationships",
            responses={
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                500: {
                    "model": ErrorResponse,
                    "description": "Internal server error",
                },
            },
        )(self.get_relationships)

        router.post(
            "",
            response_model=RelationshipResponse,
            summary="Create relationship",
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Invalid user ID or self-request",
                },
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                403: {"model": ErrorResponse, "description": "User is blocked"},
                404: {"model": ErrorResponse, "description": "User not found"},
                409: {
                    "model": ErrorResponse,
                    "description": "Relationship already exists",
                },
                500: {
                    "model": ErrorResponse,
                    "description": "Internal server error",
                },
            },
        )(self.create_relationship)

        router.put(
            "/{user_id}/accept",
            response_model=SuccessResponse,
            summary="Accept friend request",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid user ID"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                404: {
                    "model": ErrorResponse,
                    "description": "Friend request not found",
                },
                500: {
                    "model": ErrorResponse,
                    "description": "Internal server error",
                },
            },
        )(self.accept_friend_request)

        router.delete(
            "/{user_id}",
            response_model=SuccessResponse,
            summary="Remove a relationship",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid user ID"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                404: {
                    "model": ErrorResponse,
                    "description": "Relationship not found",
                },
                500: {
                    "model": ErrorResponse,
                    "description": "Internal server error",
                },
            },
        )(self.delete_relationship)

        router.post(
            "/block",
            response_model=RelationshipResponse,
            summary="Block a user",
            responses={
                400: {"model": ErrorResponse, "description": "Invalid user ID"},
                401: {
                    "model": ErrorResponse,
                    "description": "Invalid or expired token",
                },
                404: {"model": ErrorResponse, "description": "User not found"},
                409: {"model": ErrorResponse, "description": "Already blocked"},
                500: {
                    "model": ErrorResponse,
                    "description": "Internal server error",
                },
            },
        )(self.block_user)
