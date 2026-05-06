# Bootstrap response contract

The shell consumes a bootstrap response produced by the bootstrap service.

## Response envelope

The bootstrap response should contain:

- `environment`
- `user`
- `permissions`
- `flags`
- `features`
- `metadata`

## Feature entry shape

Each feature in `features` should include runtime-friendly fields such as:

- `featureKey`
- `displayName`
- `route`
- `version`
- `nav`
- `frontend.entryUrl`
- `backend.apiBaseUrl`
- `authorization.requiredPermissions`
- `authorization.requiredFlags`

## Ownership boundary

- Feature repo publishes runtime-ready manifest data
- Registry stores release/runtime data
- Bootstrap service builds shell-facing response
- Shell consumes the bootstrap response only