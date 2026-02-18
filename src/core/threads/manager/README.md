# Thread Manager

## Purpose
Implements thread lifecycle management, membership, and state transitions
for server-based thread conversations.

## Primary Responsibilities
- Create, update, and archive threads
- Manage thread membership and permissions
- Maintain thread metadata and message counts
- Enforce thread naming and lock rules

## Core Components
- ThreadManager: main orchestration class for thread operations
- Thread models for state, type, and auto-archive duration

## Dependencies
- Auth module for user validation
- Messaging module for thread message interactions
- Servers module for permission checks
- Notifications module for thread mention signals

## Notes
- Auto-archive checks are based on last activity timestamps.
