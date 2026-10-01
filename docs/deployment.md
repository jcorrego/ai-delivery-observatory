# Admin access and deployment

The bundled server is a local reference tool. It binds only to `127.0.0.1`, requires
an environment-supplied credential for every configured admin, and rejects duplicate
or short credentials. It has no non-admin report route. The publicly shareable demo
is a synthetic static export, separate from the admin server.

Successful sign-in issues an HttpOnly, SameSite=Strict cookie with a one-hour session.
Authenticated API clients can use a bearer token. Review writes require a custom
action header; cross-origin browser requests are rejected. Review identity comes
from authentication. An expected PR revision prevents approval of an unseen update.
Sign-out invalidates the session. Data writes are atomic and private files use mode 0600.

The CLI is a trusted local administrator workflow. File access and supplied review
identity are controlled by the operator's OS account. Imported approval metadata is
not independently signed. Production multi-user approval needs an authenticated
identity provider and durable audit storage.

## Organizational installation

Before adapting this reference for a shared deployment, implement these concrete
components for your organization:

- SSO/OIDC sign-in with a server-side admin role check on reads, exports, and approvals.
- A reverse proxy with TLS and a deliberate trusted-proxy/Origin policy. The local
  server's fixed Origin check and loopback binding must not be bypassed casually.
- Tenant-scoped repository credentials, queries, storage, and exports when hosting
  more than one organization. This version has one organization per configuration.
- Durable encrypted storage, append-only authenticated review audit events, a backup
  policy, data retention, and a reviewed export path.
- Collection jobs with retry queues, source cursors, monitoring, and reviewed updates
  to provider contracts. Do not report a job launch as completed historical coverage.

The demo contains no SSO integration, multi-tenant database, webhook receiver, or
production deployment manifest. No employer deployment is bundled or assumed.

## Sharing an export

A static export embeds the team and every person-level report for its fixed period.
Its filters are presentation controls, not access control. It also includes baseline
and unlinked-account evidence for administrators. Only share an export after reviewing
the entire underlying contents. An HTML file in a public repository is public data.

Do not publish generated reports from your real `data/` folder to GitHub Pages. The
committed `docs/demo.html` and images are generated solely from fictional fixtures.
`.gitignore` is an accidental-commit precaution, not a security boundary.
