# Plexichat Source Code

This directory contains the core implementation of the Plexichat server.

## Structure

- **api**: FastAPI application, routes, and schemas.
- **core**: Business logic and data management (Authentication, Messaging, Servers, etc.).
- **utils**: Server-specific utility functions.

## Technical Overview

The server is built on FastAPI and utilizes an asynchronous architecture for high performance. It supports both SQLite for simple deployments and PostgreSQL for production environments.
