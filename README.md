# Softarr

![Version](https://img.shields.io/badge/version-v1.0.0-blue)
![License](https://img.shields.io/github/license/arrsome/softarr)
![Last Commit](https://img.shields.io/github/last-commit/arrsome/softarr)
![Issues](https://img.shields.io/github/issues/arrsome/softarr)
![Pull Requests](https://img.shields.io/github/issues-pr/arrsome/softarr)
![Docker](https://img.shields.io/badge/docker-supported-blue)
![Python](https://img.shields.io/badge/python-3.14+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-async-green)
![Security](https://img.shields.io/badge/security-focused-critical)
![Auth](https://img.shields.io/badge/auth-2FA_enabled-green)
![API](https://img.shields.io/badge/API-OpenAPI-informational)
![ARR Inspired](https://img.shields.io/badge/inspired_by-ARR_stack-purple)

**Software Release Manager -- v1.0.0**

A production-oriented tool for tracking, analysing, and safely managing software releases. Inspired by the *arr stack (Sonarr, Radarr, Lidarr) but built for software with emphasis on structured data, verification, risk visibility, staged approvals, and audit logging.

No automatic downloads or installs. All actions go through explicit review and approval.

---

## Feature Highlights

- **Release discovery** from GitHub repositories, Newznab (Usenet) indexers, and Torznab (torrent) indexers
- **Security analysis** -- heuristic publisher matching, GPG/sigstore signature verification, archive scanning, and hash intelligence from VirusTotal, NSRL, CIRCL, MalwareBazaar, and MISP warninglists
- **Trust and flag system** -- Developer Verified, Admin Verified, or Unverified trust levels; none/warning/restricted/blocked flags with visible reasons
- **Explicit workflow state machine** -- discovered, staged, under_review, approved, rejected, queued_for_download, downloaded
- **Download client integration** -- SABnzbd (NZB) and qBittorrent (torrent/magnet), with queue polling and completion tracking
- **Notifications** -- email (SMTP), Discord webhook, HTTP webhook, and Apprise-compatible targets (ntfy, Gotify, Slack, etc.) with per-channel event filtering
- **Scheduled automation** -- periodic release checking, auto-approve thresholds, auto-queue upgrades, retention policies
- **Role-based access control** -- admin and viewer roles, TOTP 2FA, configurable password policy, API key auth
- **Observability** -- Prometheus metrics, structured JSON logging, audit log with CSV export, system health dashboard
- **Dark responsive UI** -- Tailwind CSS, HTMX, Alpine.js; mobile sidebar; WCAG 2.2 AA target; 10 languages

Signature verification and archive scanning are functional but limited -- see [docs/architecture.md](docs/architecture.md) for details.

---

## Architecture

Built with FastAPI, async SQLAlchemy, Jinja2/HTMX/Alpine.js, and Tailwind CSS. See [docs/architecture.md](docs/architecture.md) for the full codebase structure, data flow, and design decisions.

---

## Prerequisites

- Docker and docker-compose (recommended)
- Git
- Python 3.14+ (for local development without Docker)

---

## Installation

### Docker + docker-compose (Recommended)

**Production:**

```bash
git clone https://github.com/arrsome/softarr.git
cd softarr
# Run migrations then start
docker compose up --build -d -f docker-compose.yml
docker compose exec app alembic upgrade head
```

See [docs/configuration.md](docs/configuration.md#running-as-a-non-root-user) for the non-root user, bind mount, and `--user` reference.

**Development:**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The dev override enables hot reload, bind mounts, and auto table creation.

### Local Development

```bash
git clone https://github.com/arrsome/softarr.git
cd softarr
mise run setup # creates .venv and runs `uv sync`
mise run dev # starts uvicorn via `uv run` with --reload
```

See [docs/configuration.md](docs/configuration.md) for the full `.env` and `softarr.ini` reference.

### First Login

On first startup with no users in the database, an admin account is created with default credentials. A warning is printed to the console:

```
========================================
  DEFAULT ADMIN CREDENTIALS
  Username : admin
  Password : admin

  This is the DEFAULT password. Change it immediately
  via Settings > Users after your first login.
========================================
```

To use a pre-hashed password instead, generate a bcrypt hash and set `ADMIN_PASSWORD_HASH` in `.env`:

```bash
python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
```

---

## Database Migrations

Softarr uses Alembic with async SQLAlchemy.

```bash
alembic upgrade head            # Apply migrations (required for production)
alembic revision --autogenerate -m "description"  # Generate after model changes
alembic downgrade -1            # Rollback one step
```

When `AUTO_CREATE_TABLES=true`, tables are created automatically on startup. Production must use Alembic.

---

## API

Interactive API documentation is available at `/docs` (OpenAPI/Swagger) when the application is running. For a static endpoint reference, see [docs/api.md](docs/api.md).

All state-changing endpoints require authentication. Settings, staging, indexer, and action endpoints require the admin role.

---

## Running Tests

```bash
pytest tests/ -v
```

Unit tests run without any database. Integration tests use an in-memory SQLite database and cover the full workflow lifecycle, auth, settings, analysis, override persistence, and indexer management.

---

## Security

- Session cookies are signed (itsdangerous) with `samesite=strict` and configurable expiry
- CSRF tokens validated on all state-changing requests from browser sessions
- Rate limiting per-IP: 60 req/min default, 10/min for search, 5/min for download sends
- Settings API masks all secret values; `softarr.ini` created with 0600 permissions
- Audit logs capture all state changes, approvals, overrides, and settings modifications

See [docs/architecture.md](docs/architecture.md) and [docs/configuration.md](docs/configuration.md) for full security details.

---

## Documentation

- [docs/architecture.md](docs/architecture.md) -- Codebase structure, data flow, design decisions
- [docs/configuration.md](docs/configuration.md) -- `.env` and `softarr.ini` reference
- [docs/api.md](docs/api.md) -- Full API endpoint reference
- [docs/software.md](docs/software.md) -- Software library management
- [docs/release-rules.md](docs/release-rules.md) -- Version pinning and auto-reject rules
- [docs/usenet-indexers.md](docs/usenet-indexers.md) -- Newznab indexer setup
- [docs/torznab-indexers.md](docs/torznab-indexers.md) -- Torznab indexer setup
- [docs/two-factor-authentication.md](docs/two-factor-authentication.md) -- 2FA setup

---

## Accessibility

Softarr targets WCAG 2.2 AA compliance with keyboard navigation, ARIA landmarks, labelled controls, and live regions. See [docs/architecture.md](docs/architecture.md) for implementation details.

---

## Language Support

Softarr includes a localisation framework based on JSON locale files. Supported languages: `en` (fully translated), `de`, `es`, `fr`, `it`, `ja`, `ko`, `pt`, `zh`, `ar`. Non-English locales currently display English text as a placeholder. Translations are welcome via pull request.

---

## Legal and Usage

Softarr is provided on an "as is" basis, without warranty of any kind, express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, or non-infringement. To the maximum extent permitted by applicable law, the developers and contributors accept no liability for any direct, indirect, incidental, special, consequential, or exemplary damages arising from the use or inability to use this software.

To the extent permitted by the **Australian Consumer Law** (Schedule 2 of the Competition and Consumer Act 2010 (Cth)), all implied guarantees, conditions, and warranties are excluded.

**Users are fully and solely responsible** for ensuring their use of Softarr complies with all applicable laws and regulations in their jurisdiction, including laws governing intellectual property, copyright, data protection, and the downloading or distribution of software or media.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for all release history.

---

## Licence

MIT
