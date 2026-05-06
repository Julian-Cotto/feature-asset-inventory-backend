# Authentication and Authorization Guide

## Overview

This feature uses a structured authentication and authorization model that supports:

- `none`
- `mock`
- `entra`

The feature is designed to work in local development, shell-hosted environments, and production deployments with consistent behavior.

## Current feature auth configuration

- **Mode:** `entra`
- **Shell auth required:** true
- **Token forwarding:** true
- **Allowed dev modes:** mock
- **Roles:** admin, operator, reader

## Auth modes

### `none`
No authentication is required.

Behavior:
- frontend treats user as anonymous
- backend allows access without bearer token
- shell auth contract is not required

### `mock`
Development-oriented authentication mode.

Behavior:
- frontend can fall back to a local dev identity
- backend can accept debug headers
- shell auth contract is optional
- useful for local development and fast iteration

### `entra`
Production-oriented authentication mode.

Behavior:
- shell auth contract is typically required
- frontend expects a valid shell-provided auth contract
- backend validates bearer tokens
- token forwarding is required when backend APIs are protected

## Shell auth contract

Features expect shell-provided auth through:

`window.__FEATURE_SHELL_AUTH__`

Current contract version: `v1`

Example:

```ts
window.__FEATURE_SHELL_AUTH__ = {
  version: "v1",
  isAuthenticated: true,
  userId: "u1",
  userName: "Jane Doe",
  email: "jane.doe@example.com",
  roles: ["developer"],
  accessToken: "<bearer token>"
};