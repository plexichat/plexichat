# Poll Routes

Polls are message-attached interactive objects that support voting and result inspection.

## Routes

- `POST /polls`
- `GET /polls/{poll_id}`
- `GET /polls/{poll_id}/results`
- `POST /polls/{poll_id}/vote`
- `POST /polls/{poll_id}/close`
- `DELETE /polls/{poll_id}`

## Purpose

Use these routes to create polls attached to a message, cast votes, fetch aggregate results, and close or delete a poll.

## Expected Rules

- poll creation is submitted to `/polls` and includes the target `message_id` in the request body
- voting permissions follow normal channel/message visibility rules
- closed polls stop accepting new votes
- result visibility may depend on poll settings and server policy

## Client Guidance

- fetch poll details after vote mutations if you need authoritative totals
- handle duplicate-vote or closed-poll failures cleanly
- render poll state from server responses instead of local assumptions

