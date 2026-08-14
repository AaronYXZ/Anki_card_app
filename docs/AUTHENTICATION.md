# Authentication Decision and Threat Model

## Status

Accepted for the private-alpha authentication vertical slice on 2026-08-10.

## Decision

The private alpha uses invite-only email and password accounts with opaque,
server-side sessions.

- Passwords are hashed with `hashlib.scrypt` using a unique random salt.
- The browser receives a random session token in an `HttpOnly` cookie.
- Only a SHA-256 digest of that token is stored in PostgreSQL.
- Sessions expire after a configured lifetime and can be revoked immediately.
- `SameSite=Lax` is used for the first slice. Production cookies must also be
  `Secure` and are only sent over HTTPS.
- Every mutation requires a CSRF token. Before login it uses a random same-origin
  cookie. After login it is derived from the current opaque session token and
  therefore rotates with that session.
- Every user-owned route resolves its user from the current request. Route code
  never reads a global or fixed production user identifier.
- Local development may retain the fixed development user only when
  `AUTH_MODE=development` is explicitly selected.

This keeps identity implementation small, makes logout and session revocation
reliable, and avoids placing user identity or password material in a signed but
client-readable cookie. A managed identity provider remains a valid later
migration if operating account recovery and email delivery becomes expensive.

## Alternatives considered

### Magic links

Deferred because they require a trusted email delivery provider, token-delivery
monitoring, and abuse controls before the first private deployment.

### OAuth-only login

Deferred because it adds provider configuration and callback failure modes while
the private alpha has a very small, known account set.

### Stateless signed sessions

Rejected for this stage because individual sessions cannot be revoked immediately
without maintaining additional server state.

## Account lifecycle

### Invite acceptance

The first slice does not send invitations. An administrator creates an account
through a controlled management path, then communicates the temporary credential
out of band. A later invite flow must use a single-use, expiring token stored as a
digest and must require the recipient to choose a password before activation.

### Login and logout

A successful login rotates to a newly generated session. Login errors do not
disclose whether an email address exists. Logout revokes the matching database
session before deleting the browser cookie.

### Session expiry

Sessions have an absolute expiry controlled by `SESSION_LIFETIME_DAYS`. Expired
sessions are rejected even if the browser still has the cookie. Sliding expiry is
not used in the first slice.

### Password reset

There is no self-service reset in this slice. An administrator must set a new
password through the controlled management path, and all existing sessions must
be revoked. Email-based reset requires the same single-use token properties as
invite acceptance.

### Account deletion

There is no deletion UI in this slice. A future deletion operation must first
revoke all sessions, provide or confirm an export, and apply a documented policy
to source documents, generated cards, and append-only review history. It must not
silently cascade away review history.

## Threat model

Protected assets include imported source text, generated cards, review history,
scheduling state, account credentials, session tokens, and AI-provider secrets.

| Threat | Initial control | Remaining work |
|---|---|---|
| Credential database disclosure | Salted scrypt hashes. No plaintext passwords | Define password rotation and breach response |
| Session database disclosure | Store only token digests | Periodic cleanup of expired rows |
| Session theft in transit | `Secure` production cookie and HTTPS | Verify deployment headers and TLS |
| Browser script reads session | `HttpOnly` cookie | Content Security Policy and render sanitization |
| Cross-site request forgery | `SameSite=Lax`, POST mutations, and explicit Session-bound CSRF tokens | Verify Origin behavior during deployment acceptance |
| Stored or reflected script injection | Jinja autoescape, text-only content rendering, and restrictive CSP | A future Markdown-to-HTML renderer must add an allowlist sanitizer |
| User reads another user's data | Request-scoped identity plus owner-filtered queries | Maintain an authorization matrix as routes grow |
| User mutates another user's data | Domain services require `user_id`; routes use authenticated identity | Add coverage for each new mutation |
| Login enumeration | One generic invalid-credentials response | Add rate limiting and audit events |
| Brute-force login | Strong password hashing | Add per-IP and per-account rate limits before wider alpha |
| Open redirect after login | Accept only local paths beginning with one slash | Keep redirect validation centralized |
| Fixed development identity in production | Explicit auth mode and production configuration validation | Deployment acceptance test |

## Security invariants

1. Password mode never falls back to the development user.
2. An absent, invalid, revoked, or expired session is unauthenticated.
3. Session tokens are returned only in cookies and are never logged or stored raw.
4. A successful login creates a new token. It never adopts a caller-provided token.
5. Every route that reads or writes user data depends on request-scoped identity.
6. Cross-user lookups return `404` where revealing object existence is unnecessary.
7. Public routes are limited to health, static PWA resources, installation guidance,
   and authentication endpoints.
8. Every POST route validates a CSRF token before executing its handler.
9. User and model content remains autoescaped text. No template uses `safe` or a
   browser HTML-insertion API.

## Explicit non-goals for this slice

- invitation email delivery;
- self-service registration, password reset, or account deletion;
- OAuth or passkeys;
- multi-factor authentication;
- rich Markdown-to-HTML rendering and its allowlist sanitizer;
- login rate-limit hardening;
- changing the background generation execution model.
