# Konnaxion Capsule Manager — Netcup Progress v18

Date: 2026-09-10

## Fixes

- Attaches the existing Traefik `secure-headers` middleware to all public routers: frontend, API, admin, and media.
- Adds HSTS at the Traefik edge (`stsSeconds: 31536000`) so the public frontend no longer depends on application-specific HSTS behavior.
- Adds the recommended browser security headers checked by SecurityDiag S12: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`, and `Permissions-Policy`.
- Removes the `X-Powered-By` response header at the Traefik edge.
- Keeps the CSP deliberately narrow (`frame-ancestors`, `object-src`, `base-uri`) to avoid breaking Next.js script/style execution.

## Preserved behavior

- `konnaxion.com` remains the default public domain and `www.konnaxion.com` remains an automatic alias.
- Let's Encrypt certificate resolution is unchanged.
- PostgreSQL/Redis ownership protections and the hardened non-root `kx-agent` behavior from v14-v17 are unchanged.
- No `stsIncludeSubdomains` or HSTS preload flag is enabled automatically; those require a separate domain-wide operational decision.

## Expected SecurityDiag effect

- S12 `external.headers.hsts` should become PASS.
- S12 `external.headers.recommended` should become PASS when probing the public frontend through Traefik.
