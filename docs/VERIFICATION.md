# Verification

## Reproduce the automated checks

Requires Python 3.11+ and Node.js for the JavaScript syntax check. No Python dependencies are required. Run from the repository root:

```sh
python3 -m unittest discover -s tests -v
node --check app/static/app.js
```

The 38 regression tests use temporary directories, synthetic fixtures, mocked cloud responses, and temporary loopback HTTP servers. They do not require API keys or an existing collection. The runner must permit local socket binding.

Coverage includes authentication, CSRF/origin checks, upload validation, review/archive/delete, image cleanup, category renaming, duplicate merging, backup validation, credential exclusion, persistent retries, provider consent and free-model constraints, and optional auto-approval with stale-result protection.

## Manual smoke test

Start with a disposable data directory (replace the example path with an empty directory):

```sh
DATA_DIR=/tmp/later-smoke-test python3 -m app.server
```

1. Open http://127.0.0.1:8787 and create a password.
2. Upload a synthetic screenshot and confirm it appears in Inbox.
3. Use Create manually, edit its details, save, search, archive, and restore it.
4. Configure your provider and use Test saved connection. Upload a synthetic image and review the generated proposal.
5. Export a portable backup and restore it only into a disposable collection.
6. Check the interface at a 390-pixel viewport.

Stop the server when finished. The previews in this repository contain synthetic test data.

## Prior validation and limits

The native Python app and ARMv7 Docker image were tested on a Raspberry Pi 2 with Bookworm. English/Russian OCR, unprivileged container operation, read-only root filesystem, restart persistence, and private HTTPS access were checked. LM Studio vision extraction was exercised with synthetic English and Russian samples. Cloud adapters have mocked regression coverage; a Google text generation request was also exercised.

These checks do not establish extraction accuracy for real screenshots. ARM64/AMD64 container builds, actual iPhone/Android browsers, large collections, bulk/grouped inference, and alternative model compatibility need further validation. Docker builds use distribution package repositories and are not bit-for-bit reproducible.

## iPhone Shortcut uploads — 2026-10-14

Added a write-only bearer-authenticated binary image endpoint and session/CSRF-protected token creation/revocation controls. Only a SHA-256 token hash is stored; tokens are shown once and excluded from settings responses and portable exports. Uploads reuse normal image validation and the durable queue, with an 8 MB request limit and 60 authenticated requests per minute. Rotation invalidates the previous token. All 40 tests pass, including token scope, rotation/revocation, CSRF protection, invalid images, MIME types, request-size limits, rate limits, queue persistence, and export exclusion. JavaScript syntax and whitespace checks pass. Actual iPhone Shortcuts execution requires a device check after setup.

Deployed to the existing Raspberry Pi Docker Compose service. Both Shortcut HTTP tests also pass natively on its Python 3.11/ARMv7 installation. The deployed database integrity check passed; the new frontend controls are present, unauthenticated Shortcut uploads return 401, and the private Tailscale HTTPS session endpoint responds normally. Previous source and image retained for rollback. No production token was created during verification.
