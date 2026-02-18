# Presence Manager

## Purpose
Tracks user presence, activities, typing indicators, and custom status
with server-aware visibility rules.

## Primary Responsibilities
- Maintain presence and status records
- Track typing indicators and auto-expire them
- Store and clear custom statuses with expiry
- Update Redis presence caches when available

## Core Components
- PresenceManager: presence orchestration and state updates
- Presence models: status, activity, typing, and custom status

## Dependencies
- Auth module for user validation
- Relationships module for visibility filtering
- Servers module for member presence visibility
- Redis cache helpers for high-speed lookups

## Data and Caching
- Presence states are cached in Redis for fast retrieval
- Typing indicators are periodically cleaned from the database
