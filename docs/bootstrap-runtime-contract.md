# Bootstrap runtime contract

This feature is published to the registry and consumed by the bootstrap service.

## Runtime-friendly manifest fields

The resolved manifest includes bootstrap-friendly aliases:

- route
- nav
- backend.apiBaseUrl
- authorization.requiredPermissions
- authorization.requiredFlags

## Example runtime shape

{
  "featureKey": "{{ names.feature_key }}",
  "displayName": "{{ names.display_name }}",
  "route": "{{ names.base_path }}",
  "version": "{{ manifest_version }}",
  "nav": {
    "label": "{{ names.display_name }}",
    "icon": "package"
  },
  "frontend": {
    "entryUrl": "/features/{{ names.feature_key }}/assets/bootstrap.js"
  },
  "backend": {
    "apiBaseUrl": "/api/{{ names.feature_key }}"
  },
  "authorization": {
    "requiredPermissions": [],
    "requiredFlags": []
  }
}

## Ownership boundary

- Feature repo publishes runtime-ready manifest
- Registry stores releases
- Bootstrap service resolves features
- Shell consumes bootstrap output