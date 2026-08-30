# Feedback API

Submit and check the status of user feedback. You use the feedback endpoints
to send product feedback and to query rate limits and availability.

All endpoints in this document are relative to `https://api.plexichat.com`.
For local development, use `http://localhost:8000`.

You must authenticate with `Authorization: Bearer <token>` for all feedback
endpoints.

## Submit feedback

Submits feedback for the authenticated user.

```
POST /api/v1/feedback
```

The request is rate-limited per user. If the feedback system is disabled,
the endpoint returns `503`.

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `content` | string | Yes | Feedback content, 10–2000 characters. |
| `category` | string | No | Category, max 50 characters. |
| `rating` | integer | No | Rating from `1` to `5`. |

#### Example request

```bash
curl -X POST https://api.plexichat.com/api/v1/feedback \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "The new thread UI is much easier to use. Thanks!",
    "category": "ui",
    "rating": 5
  }'
```

### Response

`200 OK`

```json
{
  "id": 42,
  "message": "Thank you for your feedback!"
}
```

| Field | Type | Description |
| --- | --- | --- |
| `id` | integer | ID of the created feedback entry. |
| `message` | string | Success message. |

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid content, category, or rating. |
| `401` | Not authenticated. |
| `429` | Too many feedback submissions. Try again later. |
| `500` | Failed to submit feedback. |
| `503` | Feedback system is disabled or not initialized. |

### Guidance

*   Keep feedback concise and focused on your experience.
*   Do not include secrets, tokens, or private logs in the feedback body.
*   Use the status endpoint to check whether you can submit before you show
    the feedback form.

## Get feedback status

Returns the feedback system's availability and your current rate limit state.

```
GET /api/v1/feedback/status
```

### Response

`200 OK`

```json
{
  "enabled": true,
  "submissions_this_hour": 2,
  "submissions_today": 5,
  "max_per_hour": 5,
  "max_per_day": 20,
  "can_submit": true
}
```

| Field | Type | Description |
| --- | --- | --- |
| `enabled` | boolean | Whether the feedback system is enabled. |
| `submissions_this_hour` | integer | Submissions you sent in the last hour. |
| `submissions_today` | integer | Submissions you sent in the last 24 hours. |
| `max_per_hour` | integer | Maximum submissions allowed per hour. |
| `max_per_day` | integer | Maximum submissions allowed per day. |
| `can_submit` | boolean | Whether you can currently submit feedback. |

Defaults are `5` per hour and `20` per day, configurable on the server.

### Example request

```bash
curl -X GET https://api.plexichat.com/api/v1/feedback/status \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Errors

| Status | Description |
| --- | --- |
| `401` | Not authenticated. |
| `500` | Internal server error. |

## Rate limiting

Feedback submissions are rate-limited with two buckets:

*   Hourly bucket: configurable via `feedback.rate_limit.max_per_hour`
*   Daily bucket: configurable via `feedback.rate_limit.max_per_day`

If you exceed either bucket, `POST /feedback` returns `429`. Check
`GET /feedback/status` to disable the submit button when `can_submit` is
`false` or `enabled` is `false`.
