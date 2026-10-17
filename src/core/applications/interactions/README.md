# Interactions

Create and respond to user interactions such as slash commands, buttons, select menus, and modals.

## Overview

The interactions package handles the interaction lifecycle for bot applications. An interaction is created when a user invokes a command or component, a unique token is generated for that interaction, and the application must respond within a 15-minute window. The package validates responses, encrypts payloads at rest, and dispatches events to the gateway.

## Components

| File | Purpose |
|---|---|
| `handler.py` | `InteractionHandler` creates interactions, verifies tokens, validates responses, and writes encrypted payloads. |
| `components.py` | Builders and validators for message components (buttons, selects, text inputs, action rows, modals). |
| `responses.py` | Helpers for creating interaction responses (`create_message_response`, `create_deferred_response`, and others). |

## Before you begin

Verify the following:

* The applications module is initialized with `applications.setup(db, auth, servers, events)`.
* The `interaction_timeout` configuration is set (default `900` seconds in `handler.py:35`).

## Interaction lifecycle

1. You call `create_interaction` in `handler.py:58` or `applications.handle_interaction`. The handler generates a token with `generate_interaction_token`, hashes it, and stores the hash in `app_interactions.token_hash`. Interaction data is encrypted with `encrypt_data` before storage in `handler.py:94`.
2. The handler dispatches an `INTERACTION_CREATE` event through the events module in `handler.py:382`.
3. You verify the token and respond with `respond` in `handler.py:213` before the token expires. The handler enforces a single response by using an atomic `UPDATE ... WHERE responded = 0` guard in `handler.py:272` and raises `InteractionAlreadyRespondedError` on a race.
4. You can create follow-up messages or edit the original response with `create_followup` in `handler.py:289` and `edit_original` in `handler.py:323`.

Interaction data and the response payload are encrypted at rest. The handler decrypts data on read in `handler.py:563` and surfaces `InteractionValidationError` if encryption is unavailable rather than storing plaintext.

## Token handling

* Tokens are generated per interaction and bound to the interaction ID. Only the hash is stored.
* `verify_interaction_token` in `handler.py:180` parses the token with `parse_oauth_token`, verifies the hash with `verify_token_hash`, and checks expiry against `interaction_timeout`. It raises `InteractionNotFoundError` for invalid tokens and `InteractionExpiredError` for expired tokens.
* If you pass `application_id` to `respond`, the handler verifies that the interaction belongs to that application in `handler.py:237`.

## Build components

```python
from src.core.applications.interactions import (
    build_button,
    build_select_menu,
    build_text_input,
    build_action_row,
    build_modal,
)

button = build_button(
    style=1,  # ButtonStyle.PRIMARY
    label="Click me",
    custom_id="btn_1",
)

select = build_select_menu(
    custom_id="color_select",
    placeholder="Choose a color",
    options=[
        {"label": "Red", "value": "red"},
        {"label": "Blue", "value": "blue"},
    ],
)

row = build_action_row([button])
modal = build_modal(
    custom_id="feedback_modal",
    title="Feedback",
    components=[row],
)
```

Validate components before sending them:

```python
from src.core.applications.interactions import validate_components

valid, issues = validate_components(components, context="message")
# Use context="modal" for modal components
```

## Create responses

```python
from src.core.applications.interactions import (
    create_message_response,
    create_deferred_response,
    create_modal_response,
    create_autocomplete_response,
    create_update_response,
    InteractionHandler,
)

# Immediate message response
response = create_message_response(content="Pong!", ephemeral=False)

# Deferred response for long-running work
response = create_deferred_response(ephemeral=False)

# Modal response
response = create_modal_response(
    custom_id="feedback_modal",
    title="Submit Feedback",
    components=[
        {
            "type": 1,
            "components": [
                {
                    "type": 4,
                    "custom_id": "feedback_text",
                    "label": "Your Feedback",
                    "style": 2,
                    "required": True,
                }
            ],
        }
    ],
)

# Autocomplete response (maximum 25 choices)
response = create_autocomplete_response(
    choices=[
        {"name": "Option 1", "value": "opt1"},
        {"name": "Option 2", "value": "opt2"},
    ]
)

# Component update response
response = create_update_response(content="Updated!")

# Send the response
handler = InteractionHandler(db, config, events)
handler.respond(interaction_token, response)
```

If you need an ephemeral message, set `ephemeral=True` or include `flags=64`.

## Response validation

`_validate_response` in `handler.py:446` enforces the following rules:

| Interaction type | Allowed response types | Notes |
|---|---|---|
| `PING` (`1`) | `PONG` (`1`) only | Any other type returns an error. |
| `APPLICATION_COMMAND_AUTOCOMPLETE` (`4`) | `APPLICATION_COMMAND_AUTOCOMPLETE_RESULT` (`8`) only | Maximum `25` choices. |
| `MODAL_SUBMIT` (`5`) | Any type except `MODAL` (`9`) | Submitting a modal cannot open another modal. |
| `MESSAGE_COMPONENT` (`3`) | All types except `PONG`, `MODAL`, and `AUTOCOMPLETE_RESULT` | — |
| Other types | Checked for component validity | Components are validated with `validate_components` in `components.py`. |

## Use the handler directly

```python
from src.core.applications.interactions import InteractionHandler

handler = InteractionHandler(db, config, events_module)

# Create an interaction
interaction = handler.create_interaction(
    application_id=app_id,
    interaction_type=InteractionType.APPLICATION_COMMAND,
    user_id=user_id,
    data={"name": "ping", "type": 1},
    server_id=server_id,
    channel_id=channel_id,
)

# Respond to the interaction
handler.respond(interaction.token, create_message_response(content="Hello!"))

# Manage the original response
handler.edit_original(interaction.token, content="Edited content")
handler.delete_original(interaction.token)
```

## Error handling

| Exception | When it is raised |
|---|---|
| `InteractionNotFoundError` | The interaction ID or token is invalid, or the response does not exist. |
| `InteractionExpiredError` | The token is older than `interaction_timeout` (15 minutes by default). |
| `InteractionAlreadyRespondedError` | The interaction has already been responded to. |
| `InteractionValidationError` | The response type is not allowed for the interaction type, choices exceed limits, components are invalid, or encryption fails. |

## Configuration

The handler reads `interaction_timeout` from the `applications` configuration block. The default is `900` seconds. Override it in `config/config.yaml`:

```yaml
applications:
  interaction_timeout: 900
```
