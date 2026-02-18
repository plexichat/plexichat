# Notification Manager

## Purpose
Parses mentions, creates notifications, manages per-user settings, and
dispatches notification events to connected clients.

## Primary Responsibilities
- Parse and validate mentions in message content
- Create notifications and notification feed entries
- Enforce user and channel notification settings
- Dispatch WebSocket notification events
- Maintain unread counts and mention metadata

## Core Components
- NotificationManager: notification orchestration and dispatch
- Mention parser and validation helpers
- Notification settings and override models

## Dependencies
- Messaging module for message lookup
- Servers module for permission checks
- Relationships module for block filtering
- Presence module for @here targeting
- WebSocket dispatcher for live events

## Notes
- Content previews are truncated based on config limits.
- Mentions are validated for role and channel access.
