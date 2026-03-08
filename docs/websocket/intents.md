# Gateway Intents

Gateway intents let clients declare which event families they want to receive on the WebSocket connection.

## Current Bitmask Summary

| Setting | Value |
|---------|-------|
| Default intents bitmask | `{{GATEWAY_DEFAULT_INTENTS}}` |
| All intents bitmask | `{{GATEWAY_ALL_INTENTS}}` |
| Privileged intents bitmask | `{{GATEWAY_PRIVILEGED_INTENTS}}` |

## Intent Catalog

| Value | Name | In default set | Privileged | Description |
|-------|------|----------------|------------|-------------|
{{GATEWAY_INTENT_ROWS}}

## Usage Notes

- clients typically start with the default intent set unless they have a clear need for more
- privileged intents may require additional approval or policy review
- requesting fewer intents reduces unnecessary event volume and client processing work
- if an intent is omitted, matching events may be withheld from the gateway session

## Related Pages

- [WebSocket Overview](index.md)
- [Events](events.md)
- [Opcodes](opcodes.md)
