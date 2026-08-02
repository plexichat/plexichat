# PlexiSuite architecture and app homes

## Product surfaces

The authenticated client contains a universal PlexiSuite home plus an
independent home/workspace for each product:

- Plexichat — communication core
- PlexiScribe — documents and writing
- PlexiScript — code and technical files
- PlexiBoard — collaborative whiteboards
- PlexiStash — Drive-like files and collections
- PlexiSum — collaborative spreadsheets and structured data

Plexichat remains the default app and the platform beneath all of them.

## Universal home

The universal home provides:

- app cards and capability state
- recent artifacts across apps
- pinned items
- shared-with-me items
- cross-app activity
- quick-create actions
- search over permitted metadata
- onboarding and system/storage notices

It should use bounded, paginated queries and tolerate one app being unavailable.
It is not a replacement for the Plexichat home, which remains the chat app's
own home for DMs, friends, servers, and channels.

## App home contract

Each app home must provide:

- recent, pinned, shared, and searchable items
- app-specific templates
- new-item flow
- useful empty states
- loading skeletons and partial-failure states
- denied and unavailable states
- app settings and onboarding
- stable deep links
- a full workspace destination
- optional embedded compatibility mode

This is the primary productization work. Existing Scribe, Script, and
Whiteboard editors already provide useful editing capability, but they
currently behave mainly as artifact editors opened from chat. They need to
become destinations users can enter directly.

## App registry

The client registry is declarative. Each manifest defines an app ID, label,
icon, color token, artifact types, capability key, routes, create action,
supported scopes, embedded support, and offline intent. The backend may expose a
capability-aware projection, but the client never treats a manifest as proof of
permission.

## Brand/app switcher

The Plexichat brand control should:

- open the universal home on primary click
- open a compact app switcher through a chevron, secondary control, or mobile
  action
- show Plexichat, Scribe, Script, Board, Stash, and Sum
- mark the active app
- preserve safe server/channel/conversation context when returning to chat
- provide a back-to-document/workbook item when context permits

Routes encode `app`, object ID, optional server/channel/conversation context,
mode, and a validated same-origin return path. In-memory state is only a
convenience for transitions and must not be required after refresh.

## Embedded/workspace migration

Keep the existing compatibility entry points:

- `window.openPlexiscribe` / `window.createPlexiscribe`
- `window.openPlexiscript` / `window.createPlexiscript`
- `window.openWhiteboard` / `window.createWhiteboard`

Message attachments may open embedded mode. App homes open workspace mode.
Both use the same artifact ID, API, permissions, and collaboration contracts.
This avoids a risky all-at-once rewrite.

## Mounting strategy

The first client implementation should mount suite surfaces inside the existing
authenticated shell without taking over the chat DOM. A future dedicated HTML
entry point may be added if it uses the same registry, routes, auth bootstrap,
and API conventions.
