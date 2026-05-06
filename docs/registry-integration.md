# Registry integration

This feature can publish its manifest to a feature registry.

## Inputs

The registry publish flow uses:

- `build/feature-manifest.resolved.json`
- `build/registry-payload.json`

## Publish flow

1. Render the runtime manifest
2. Validate the resolved manifest
3. Render the registry payload
4. Publish the payload to the registry API

## Registry payload shape

```json
{
  "featureKey": "{{ names.feature_key }}",
  "version": "{{ manifest_version }}",
  "environment": "dev|test|prod",
  "manifest": {}
}