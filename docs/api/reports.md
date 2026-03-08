# Report Routes

Reports let users flag content or behavior for review.

## Routes

- `POST /reports/user`
- `POST /reports/message`

## Purpose

- report an account or user profile
- report a specific message or content item

## Expected Behavior

- reporting requires enough identifying information for the target object
- abuse-report flows should respect normal authentication and permission checks
- the backend may queue moderation workflows that are not exposed through public docs

## Client Guidance

- present report reasons clearly and collect only the fields required by the API
- do not assume a report produces an immediate moderation action visible to the reporter
- pair success messaging with privacy-conscious UX

