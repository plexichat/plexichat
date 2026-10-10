# Polls API

Create and manage interactive polls attached to messages. Polls support single or multiple choice, timed expiry, and controlled result visibility.

**Base URL**

`https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All paths in this document are prefixed with `/api/v1`. All endpoints require `Authorization: Bearer <token>`.

## Create a poll

### `POST /polls`

Create a new poll attached to an existing message. The message metadata is updated with `poll_id` on success.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message_id` | string (Snowflake ID) | Required | Message to attach the poll to. |
| `question` | string | Required | Poll question. 1-300 characters. |
| `options` | array of strings | Required | Poll options. 2-10 items. |
| `duration_hours` | integer | Optional | Duration in hours. `1`-`168`. Omit for no expiry. |
| `allow_multiple_choice` | boolean | Optional | Allow voting for multiple options. Default `false`. |
| `results_visibility` | string | Optional | When results are visible: `always`, `after_vote`, `after_end`. Default `always`. |

**Response `200 OK`**

Returns a `PollResponse` object.

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid question, options, duration, or vote constraints |
| 401 | Not authenticated |
| 403 | Access denied |
| 404 | Message not found |

## Get a poll

### `GET /polls/{poll_id}`

Retrieve information about a specific poll.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `poll_id` | string (Snowflake ID) | Poll ID. |

**Response `200 OK`**

Returns a `PollResponse` object.

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid poll ID format |
| 401 | Not authenticated |
| 404 | Poll not found |

## Get poll results

### `GET /polls/{poll_id}/results`

Retrieve current voting results. Visibility depends on `results_visibility` and whether the poll has ended.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `poll_id` | string (Snowflake ID) | Poll ID. |

**Response `200 OK`**

Returns a `PollResultsResponse` object.

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid poll ID |
| 401 | Not authenticated |
| 404 | Poll not found |

## Vote on a poll

### `POST /polls/{poll_id}/vote`

Cast a vote on a poll. Use this for the initial vote.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `poll_id` | string (Snowflake ID) | Poll ID. |

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `option_ids` | array of strings (Snowflake IDs) | Required | Selected option IDs. At least 1. Multiple IDs allowed only when `allow_multiple_choice` is `true`. |

**Response `200 OK`**

Returns a `PollResultsResponse` with updated totals and your vote.

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid option, poll has ended, already voted, or multiple votes not allowed |
| 401 | Not authenticated |
| 403 | Permission denied |
| 404 | Poll not found |

## Change your vote

### `PUT /polls/{poll_id}/vote`

Replace your existing vote with a new selection. Functionally identical to `POST /polls/{poll_id}/vote` except this endpoint is defined as idempotent replacement; the server calls `change_vote` instead of `vote`.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `poll_id` | string (Snowflake ID) | Poll ID. |

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `option_ids` | array of strings (Snowflake IDs) | Required | New selection of option IDs. |

Use `PUT` when the user edits their vote in the UI; use `POST` for the first vote. Both return `PollResultsResponse`.

**Response `200 OK`**

Returns a `PollResultsResponse`.

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid option, poll has ended, or multiple votes not allowed |
| 401 | Not authenticated |
| 404 | Poll not found |

## Close a poll

### `POST /polls/{poll_id}/close`

Manually end a poll, preventing further voting.

**Response `200 OK`**

Returns the updated `PollResponse` with `is_ended: true` and `ended_at` set.

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Poll already ended or invalid duration state |
| 401 | Not authenticated |
| 403 | Access denied |
| 404 | Poll not found |

## Delete a poll

### `DELETE /polls/{poll_id}`

Permanently delete a poll and all associated votes. When a poll is attached to a message, deleting the message also deletes the poll.

**Response `200 OK`**

```json
{ "success": true }
```

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid poll ID |
| 401 | Not authenticated |
| 403 | Access denied |
| 404 | Poll not found |

## Objects

### PollResponse

```json
{
  "id": "123456789012345678",
  "message_id": "234567890123456789",
  "question": "Which feature should we build next?",
  "created_by": "123456789012345678",
  "created_at": 1704067200,
  "ends_at": 1704153600,
  "ended_at": null,
  "allow_multiple_choice": false,
  "results_visibility": "always",
  "options": [
    { "id": "111", "poll_id": "123456789012345678", "text": "Voice", "position": 0, "vote_count": null },
    { "id": "222", "poll_id": "123456789012345678", "text": "Video", "position": 1, "vote_count": null }
  ],
  "total_votes": 42,
  "is_ended": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Poll ID. |
| `message_id` | string | Message ID the poll is attached to. |
| `question` | string | Poll question. |
| `created_by` | string | Creator user ID. |
| `created_at` | integer | Creation Unix timestamp. |
| `ends_at` | integer or null | Scheduled end timestamp. |
| `ended_at` | integer or null | Actual end timestamp when closed. |
| `allow_multiple_choice` | boolean | Whether multiple options can be selected. |
| `results_visibility` | string | `always`, `after_vote`, or `after_end`. |
| `options` | array of `PollOptionResponse` | Poll options. `vote_count` is `null` unless results are included. |
| `total_votes` | integer or null | Total votes. |
| `is_ended` | boolean | Whether the poll has ended. |

### PollResultsResponse

```json
{
  "poll": { "...": "PollResponse" },
  "options": [
    { "id": "111", "poll_id": "123456789012345678", "text": "Voice", "position": 0, "vote_count": 12 }
  ],
  "total_votes": 42,
  "user_voted": true,
  "user_votes": ["111"]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `poll` | `PollResponse` | Poll info (without vote counts). |
| `options` | array of `PollOptionResponse` | Options with `vote_count` populated. |
| `total_votes` | integer or null | Total votes. |
| `user_voted` | boolean | Whether the authenticated user has voted. |
| `user_votes` | array of strings | Option IDs the user voted for. |

## Client guidance

- After any vote mutation (`POST` or `PUT`), use the returned `PollResultsResponse` as the source of truth.
- Handle `400` for duplicate votes, closed polls, or multiple-choice violations without retrying.
- Check `is_ended` and `results_visibility` before rendering counts.
- Deleting a message with poll metadata also deletes the poll; no separate poll delete is required.
