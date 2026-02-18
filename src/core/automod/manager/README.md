# AutoMod Manager

## Purpose
Executes automatic moderation rules, evaluates message content, and
applies configured actions with audit tracking.

## Primary Responsibilities
- Load and evaluate automod rules per server
- Apply rule actions such as delete, timeout, kick, or ban
- Integrate AI moderation adapters when configured
- Track violations and user reputation signals
- Provide exemptions and rule-scoped bypasses

## Core Components
- AutoModManager: orchestration of rule evaluation and actions
- Rule classes: keyword, regex, spam, invite, link, caps, emoji, and AI
- Action classes: delete message, timeout, kick, ban, alert moderators
- AI adapters: OpenAI, Perspective, and custom endpoint hooks

## Dependencies
- Servers module for moderation actions
- Messaging module for message operations
- Notifications module for moderator alerts

## Configuration
- Rule enablement, limits, and AI keys are loaded from config
