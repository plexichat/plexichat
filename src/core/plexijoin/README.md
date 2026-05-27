# PlexiJoin - Federation Module

Server-to-server federation system for Plexichat. Enables cross-server communication, federation requests, and distributed messaging between independent Plexichat instances.

## Components

### FederationManager (`manager.py`)
Central orchestrator for federation operations:

- **`request_federation(server_id, target_server)`** - Initiate a federation request to a remote server
- **`accept_federation_request(request_id)`** - Approve a pending federation request
- **`reject_federation_request(request_id, reason)`** - Deny a federation request with reason
- **`list_active_federations(server_id)`** - List all active federation connections
- **`get_federation_status(federation_id)`** - Get detailed status of a federation link
- **`resolve_conflict(conflict_id, resolution)`** - Resolve data conflicts between federated servers

### Schema (`schema.py`)
Pydantic models for federation data structures:

- **`FederationJoinRequest`** - Incoming/outgoing federation join request
- **`FederationResponse`** - Standard response envelope for federation operations
- **`ServerFederationInfo`** - Metadata about a federated server connection
- **`FederationConfig`** - Global federation configuration (enabled, max_federations, allow_public)

## Key Concepts

- **Federation** - Two or more independent Plexichat servers sharing data and events
- **Join Request** - Formal request from server A to federate with server B
- **Conflict Resolution** - Mechanism to handle conflicting data between federated servers

## Dependencies

- `src.core.base` - SnowflakeID generation
- `src.core.servers` - Server membership and channel data
- External transport layer (not in this module) handles actual inter-server communication
