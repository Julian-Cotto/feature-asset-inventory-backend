# Registry contract

## Publish endpoint

Expected publish endpoint:

`POST /api/features/publish`

Expected request body:

```json
{
  "featureKey": "{{ names.feature_key }}",
  "version": "{{ manifest_version }}",
  "environment": "dev|test|prod",
  "manifest": {}
}