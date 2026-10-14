# Optional metrics contribution

**[Türkçe sürüm](CONTRIBUTION-API.md) [TR]** · **English [EN]**

## Status

The local client and Flask WSGI API code are ready; **they have not been deployed to a VDS and no remote data has been sent**. The API accepts only numeric OCR-run summaries. OCR, video, SRT/VTT, crops, and AI provider calls do not run in this service. GitHub Pages does not host this API.

## Local UI flow

The `/katki` page lists only job IDs from completed current-session jobs. Creating a preview makes no network request. An allowlist builds the payload from the current stats dictionary and local run duration. The user sees the JSON, actual HTTPS target hostname, and retention days from `H2S_CONTRIB_RETENTION_DAYS`; no remote request occurs without the separate checkbox and **“Onayla ve gönder”** (“Approve and send”) click. The send button is disabled if URL/token/retention configuration is missing. The client reports the day count on submission; the API compares it with its own policy and rejects a mismatch. For a successful or uncertain submission, the browser stores only a random submission UUID, target hostname, and local creation time in `localStorage`; it does not store the payload or bearer token. The latest 100 deletion records are listed in the UI after refresh. Because an API 404 does not distinguish an expired record from token ownership mismatch, the UI retains the ID in that case; the user can clear only the local record with the separate **“Yalnız listeden kaldır”** (“Remove from list only”) button. Local records are also retained after other deletion failures. The server purges a record when it reaches the configured retention age. The URL must use HTTPS; the client does not follow redirects and sends only to `/v1/contributions`.

The submitted v1 fields are a per-submission UUID, UTC time, duration, scanned frame count, cue count, low-confidence cue count/rate, OCR device class (`cuda`/`cpu`/`unknown`), engine class, and speech duration. The payload contains no full video/series/filename, absolute path, SRT/VTT text, image, hash, username, IP, or persistent device ID. The server adds the bearer token's SHA-256 digest to the submission row; it does not store the raw token. Records submitted with the same token can be linked through this digest. The digest is used for per-token rate limits and deletion authorization by the same token owner; retention is based on `received_at` age, not token hash. A contribution row is purged after the retention window in this document. Therefore the consent text explains token-scoped linkability. CER is not enabled in the UI. If added later, payload creation/submission must fail without a trusted local VTT reference and separate CER consent; the API also requires a separate CER consent header.

Contract: [`schemas/contribution-metrics-v1.schema.json`](../schemas/contribution-metrics-v1.schema.json). The schema is a strict allowlist; both client and server reject unknown top-level or metrics fields.

## Windows VDS setup — not live

For a live installation, verify the VDS operating system, DNS name, TLS certificate, firewall, and persistent disk location. This repository does not configure them. The API listens only on loopback; internet traffic must pass through a TLS reverse proxy. The remote client's HTTPS URL may point to `/`; the client uses only `/v1/contributions`.

1. Install the repository in an application directory dedicated to the API, where the service account cannot write. Create a virtual environment in AppRoot and install dependencies there: `py -3 -m venv .venv`, then `.\\.venv\Scripts\python.exe -m pip install -r requirements-contribution-api.txt`. Run `Waitress` from the virtual environment under AppRoot. The service account may only read and execute the application directory; do not run it as `LocalSystem`.
2. Explicitly choose the retention period after product/legal review. `H2S_CONTRIB_RETENTION_DAYS` must be an integer from 1 to 3650; `H2S_CONTRIB_DB` must be an absolute SQLite file path outside the repository/source tree. `H2S_CONTRIB_TOKEN_TTL_DAYS` sets the lifetime of new tokens in days; the default is 7 and the allowed range is 1–30 days. Tokens cannot be perpetual. At startup, the API validates Windows ACL/owner and process identity; it fails closed if SID/ACL cannot be read, the data directory is missing, the owner is wrong, a broad principal/permission exists, or the application account does not match. Give the client the retention day count; the UI displays it before consent and the API rejects a different value. Placeholder values do not start the service.
3. Copy `deploy/hardsub-contrib-service.xml.example` next to the WinSW executable using the same filename; replace the Waitress executable, AppRoot, DB/log/retention, and purge-account placeholders. Install Waitress in the virtual environment under AppRoot (for example `.venv\Scripts\waitress-serve.exe`); the script checks that the executable is in this protected tree and has no reparse/junction path. The script stops if AppRoot itself or any tree receiving recursive ACLs contains a reparse point. If a Windows service with the same name is already installed, before any stop/config change the script checks that its `PathName` exactly identifies this WinSW executable; otherwise it exits without touching the existing service. Do not install/start the service manually. WinSW XML arguments must be exactly `--listen=127.0.0.1:8787 contribution_api:app`; extra listeners or arguments are rejected. The following elevated PowerShell command installs the WinSW service, switches it from LocalSystem to the dedicated service account while **stopped/disabled**, resolves its SID after this step, and configures ACLs. At the end, owner/permissions for AppRoot/Waitress, data, log, certificate, and DB are checked again. The service remains disabled; it does not start if any step fails:

   ```powershell
   $retentionDays = [int](Read-Host 'Retention day count (product/legal decision)')
   .\deploy\configure_contribution_windows.ps1 `
     -WinSWExe 'C:\Services\hardsub\app\hardsub-contribution-api.exe' `
     -AppRoot 'C:\Services\hardsub\app' `
     -DataDirectory 'D:\HardsubData\contrib' `
     -LogDirectory 'D:\HardsubData\logs' `
     -CertificateDirectory 'D:\HardsubData\tls' `
     -PurgeAccount 'VDS01\hardsub-purge' `
     -CaddyAccount 'NT SERVICE\Caddy' `
     -RetentionDays $retentionDays `
     -TokenTtlDays 7
   ```

   Placeholder paths/accounts/day counts are not live values. The script rejects LocalSystem, switches the service to virtual account `NT SERVICE\hardsub-contribution-api`, verifies it is stopped/disabled, and resets/restricts ACLs: app root is RX-only for the API, data/log allow Modify for the API, data allows Modify for purge, cert/key allow RX-only for Caddy, and SYSTEM/Administrators have FullControl. The data directory is owned by the API service SID; the DB file may be owned by the API or separate purge SID. The purge account may belong only to local `BUILTIN\Users`; membership in another local group, including Administrators, is rejected. If the script fails, do not start the service.
4. Register a Scheduled Task for 03:15 daily with `deploy/register_contribution_purge_task.ps1`; the task runs as separate, non-admin `PurgeAccount` using S4U. The account needs local file access and `Log on as a batch job`; if Task Scheduler cannot set this up, deployment remains blocked. Example:

   ```powershell
   $retentionDays = [int](Read-Host 'Retention day count matching the API')
   .\deploy\register_contribution_purge_task.ps1 `
     -TaskName 'hardsub2srt-retention-purge' `
     -PurgeAccount 'VDS01\hardsub-purge' `
     -PythonExe 'C:\Python312\python.exe' `
     -AppRoot 'C:\Services\hardsub\app' `
     -WinSWXml 'C:\Services\hardsub\hardsub-contribution-api.exe.xml' `
     -DatabasePath 'D:\HardsubData\contrib\contributions.sqlite3' `
     -RetentionDays $retentionDays `
     -TokenTtlDays 7 `
     -ServiceAccount 'NT SERVICE\hardsub-contribution-api'
   ```

   This command calls the API's `purge` command through `deploy/purge_contributions.ps1` with the same retention period. The scheduled task is not live until real Windows identity/ACL acceptance checks pass.
5. Configure Caddy with a verified domain/IP and a **public-trust TLS certificate obtained and verified to renew independently**. `deploy/Caddyfile.example` explicitly expects public certificate/key paths. A local CA certificate that Caddy provides for a bare IP is not trusted by browsers/clients by default; do not assume Caddy will provide a trusted IP certificate on its own. If using an IP SAN, separately verify CA/client support, short certificate lifetime, automatic renewal, and inbound port 80/443 access required for HTTP-01/TLS-ALPN. Deployment stays blocked until a suitable Windows IP certificate renewer/domain and port access are verified. The script grants only the Caddy account Read/Execute on the certificate directory. Open only Caddy's HTTPS ports in Windows Firewall; do not expose 8787.
6. Generate an API token with `py -3 contribution_api.py issue-token`. The raw token is shown once; place it in a password manager and provide it externally to the local client as the `H2S_CONTRIB_TOKEN` environment variable. From creation, it is valid for `H2S_CONTRIB_TOKEN_TTL_DAYS` (default 7, range 1–30 days); perpetual tokens are not issued. When it expires, create a new token and distribute it securely to the client. The client's `H2S_CONTRIB_URL` must be `https://<actual-domain-name>`. Revoke a token with `py -3 contribution_api.py revoke-token` (it prompts for hidden input).
7. Only after `/healthz`, firewall, TLS renewal, token revocation, rate limits, deletion, Windows task identity, ACL, and purge have all been verified, enable and start the service: `sc.exe config hardsub-contribution-api start= demand`, then `Start-Service hardsub-contribution-api`. After `/healthz` and privacy checks pass, distribute the URL/token configuration to users. No VDS setup, domain/TLS, secret, or payload submission has been performed on this branch.

## API security and storage

- `/healthz`: returns only `status`; no token is required.
- `POST /v1/contributions`: 16 KiB limit, bearer token, JSON schema, `Idempotency-Key`, explicit `X-H2S-Metrics-Consent: v1`; if CER is included, also requires `X-H2S-CER-Consent: v1`. Repeating the same idempotency key and payload does not create a new row; a different payload returns 409.
- `DELETE /v1/contributions/<submission_id>`: only the same bearer token that created the submission can delete it; missing record and ownership by another token return the same 404. The UI retains the local UUID for this ambiguous 404; **“Yalnız listeden kaldır”** (“Remove from list only”) clears only browser history and sends no server deletion request.
- Tokens are stored in the DB as SHA-256 digests; the raw token is shown once at issue time. `expires_at` is required, and shared auth checks reject both POST and DELETE after expiry. Default TTL is 7 days, with a server-configured range of 1–30 days; there is no special admin bypass that extends a token indefinitely. If an old DB lacks `expires_at`, a startup transaction derives it from the old `created_at` plus the currently configured TTL. Invalid legacy dates are immediately marked expired; old-dated tokens are not revived. If migration fails, the transaction rolls back and the API remains unavailable. Rate limiting is 60 requests/hour per token (in a single-process in-memory counter); submission ID primary key/replay dedupe prevents duplicate rows. SQLite stores the allowlisted payload, token hash, receipt time, and consent time, so records from the same token are linkable.
- Logs contain no request body, token, or IP; the `werkzeug` access logger is disabled. Configure proxy/service-wrapper logs to be privacy-safe too. The service binds only to loopback.
- `H2S_CONTRIB_RETENTION_DAYS` has no default. The operator must explicitly choose it before service startup. The API has a deletion endpoint; show users the ID from the submission response.
- If `H2S_CONTRIB_TOKEN_TTL_DAYS` is unset, a secure 7-day default is used; empty or out-of-range (`1..30`) values stop API startup. New token issuance writes both `created_at` and `expires_at` to the DB. The scheduled purge task uses the same TTL so a legacy-token migration during first DB startup calculates expiry consistently with the API.
- SQLite WAL mode is used, and each writer connection enables `secure_delete=ON`. A user deletion immediately removes the row from the main table; WAL/checkpoint and disk blocks are not immediately forensically erased. The daily retention task deletes expired rows, runs `wal_checkpoint(TRUNCATE)` and `VACUUM`. This is logical DB/WAL cleanup, not guaranteed secure overwrite of the storage device. SQLite backups, shadow copies, and volume snapshots need separate cleanup under the same retention period. Do not copy the live DB alone; use the SQLite online backup API or stop the service and copy DB+WAL together for a consistent backup.

## Operator configuration

Examples: [`deploy/contribution.env.example`](../deploy/contribution.env.example), [`deploy/hardsub-contrib-service.xml.example`](../deploy/hardsub-contrib-service.xml.example), [`deploy/configure_contribution_windows.ps1`](../deploy/configure_contribution_windows.ps1), [`deploy/contribution_security_helpers.ps1`](../deploy/contribution_security_helpers.ps1), [`deploy/register_contribution_purge_task.ps1`](../deploy/register_contribution_purge_task.ps1), [`deploy/purge_contributions.ps1`](../deploy/purge_contributions.ps1), and [`deploy/Caddyfile.example`](../deploy/Caddyfile.example). These are placeholders, not production secrets/domains. The server binds only to loopback; do not deploy publicly without an HTTPS certificate/proxy. The Caddy template expects an externally supplied TLS certificate; it does not claim that Caddy automatically creates a trusted certificate for a bare IP. The synthetic deployment-guard test that does not install a service is `tests/test_contribution_deploy_validation.ps1`.

## Not implemented

Live VDS configuration/access verification, DNS/IP selection, obtaining/renewing a public TLS certificate, port 80/443 access, service-wrapper installation, disk backup/access, product/legal selection of retention, distribution of a real client token, publication, Windows runtime acceptance, and real end-to-end submit/delete measurements have not been done on this branch. There is no AI provider integration in the API.
