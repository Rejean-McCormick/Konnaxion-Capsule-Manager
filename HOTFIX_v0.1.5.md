# Konnaxion Capsule Manager hotfix v0.1.5

Fixes Docker Compose interpolation of literal dollar signs inside generated runtime env values, especially `DJANGO_SECRET_KEY`.

## Root cause

Docker Compose applies interpolation to unquoted and double-quoted values loaded through ordinary `env_file` entries. A valid Django secret such as `...$nGHSg1Zdyq...` was therefore interpreted as a reference to the environment variable `nGHSg1Zdyq`. When that variable was unset, Compose emitted a warning and substituted an empty string into the container value.

The v0.1.4 env writers correctly protected keys that *start* with `$` from django-environ proxy semantics, but they did not protect literal dollars elsewhere in a secret from Docker Compose interpolation.

## Fix

- Runtime env serializers now encode every logical `$` as `$$`, Docker Compose's literal-dollar escape.
- Internal env readers collapse `$$` back to `$`, so secret preservation and validation continue to operate on the original logical value.
- The PostgreSQL credential reconciliation step normalizes legacy v0.1.4 env files before its first Docker Compose command. This upgrades existing instances without rotating `DJANGO_SECRET_KEY` or database credentials.
- The fallback/compatibility env writers use the same escaping rule.
- Regression tests cover the production-shaped `$nGHSg1Zdyq` case, `${VAR}` syntax, literal `$$`, and in-place normalization before Compose startup.

No database migration is included. Existing secrets are preserved; only their on-disk Compose representation changes.
