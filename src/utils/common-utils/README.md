# Plexichat Shared Utilities

This directory contains shared Python utilities used by both the server and the client.

## Components

- **config**: Robust YAML/JSON configuration loader with environment variable override support.
- **logger**: Standardized logging utility with color support and file rotation.
- **validator**: Data validation helpers.
- **version**: Centralized version management for the entire project.

## Development

When adding new utilities, ensure they are generic enough to be used by multiple components of the Plexichat ecosystem.
