# Administrator system management — frontend issue 73

## Behaviour

The administrator System page reads the active database model registration,
checks the corresponding runtime, and shows its recorded validation/test
metadata. It does not use the legacy public `/model/status` response as the
identity of the active model. No active model, multiple active registrations,
unsupported versions and unavailable/inconsistent runtimes have distinct states.
Metrics retain their recorded cohort scope and scale. Missing held-out results
are not invented. Simulated research evaluations do not establish clinical
performance or individual disease risk.

The verified canonical symptom definitions are intentionally protected: code,
display label, features, kind and red flags cannot be changed through this API.
Such changes require a reviewed mapping/schema release. Administrators can
maintain review notes and add/remove custom English and Kiswahili aliases.
Baseline aliases remain protected to preserve established recognition and
safety routing. Alias additions affect the existing database-backed mapper
immediately; they do not alter model features or retrain the model.

Disease information is seeded from the existing condition-information file.
Administrators can edit presentation names and short awareness descriptions.
Condition identities remain fixed to the existing catalogue. New prediction
responses and their saved history use the current database descriptions;
existing saved results retain their original descriptions.

Feedback has independent pending/reviewed/resolved status, an administrator
note, last reviewer reference and review time. The patient's comment and rating
remain intact. Lists support server-side comment search, helpfulness/status
filters and pagination across all records.

NLP diagnostics distinguish mapping failures, no recognised symptoms and
partially unmatched wording. The `/nlp/map` endpoint records counts only in a
best-effort transaction after ending its read transaction; it reuses the request
session rather than waiting for a second pool connection. Raw input, exception details, IP addresses
and identities are not copied into this diagnostic log. Logging failures do
not break mapping. These events begin with this update; historical errors
cannot be reconstructed. A separate view shows unsupported symptoms/unmatched
wording already saved in assessment history. Unmatched wording is not
automatically classified as a software failure.

## Access, validation and transactions

Every route below checks the current database administrator role and account
availability using the existing authentication dependencies. Patient JWTs,
including forged administrator role claims, cannot grant access. Success
responses are private (`Cache-Control: no-store`). Validation errors omit
submitted input. Content is bounded, normalised plain text; common explicit
negation words and ambiguous duplicate mappings are rejected. Administrators
must still review alias meaning; these checks do not establish semantic or
clinical equivalence.

Writes require the displayed `expected_revision`. Row locks reject stale
updates with `409/record_changed`. Alias writes also acquire a transaction-level
PostgreSQL advisory lock so concurrent additions cannot claim the same wording
for different symptoms/languages. Revision changes and activity rows commit
with their content changes; an audit failure rolls back the edit.

The activity list combines the previous account-management audit with new
symptom/alias/disease/feedback actions. It records actor reference, target
reference, action and time, without copying passwords, notes, aliases or
patient comments. There is no activity update/delete endpoint. This is an
application audit trail, not a claim of protection against database operators.

## API

Base path: `/admin/system`. Paged responses use `{items, total, page}` with
12 records per page and stable secondary ordering. Search is literal and
case insensitive. Totals are database totals, not a capped recent sample.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/model` | Active registration, runtime state and recorded evaluations |
| GET | `/symptoms?q=&page=1` | Symptom/alias search |
| PATCH | `/symptoms/{id}` | Review note + expected revision |
| POST | `/symptoms/{id}/aliases` | Alias, en/sw language + expected revision |
| DELETE | `/symptoms/{id}/aliases/{alias_id}` | Custom alias, expected revision in JSON body |
| GET | `/diseases?q=&page=1` | Disease information |
| PATCH | `/diseases?condition=...` | Display name, description + expected revision |
| GET | `/feedback?q=&status=&helpful=&page=1` | Feedback review queue |
| PATCH | `/feedback/{id}` | Review status/note + expected revision |
| GET | `/nlp?source=events&kind=&page=1` | Counts-only mapping diagnostics |
| GET | `/nlp?source=assessments&page=1` | Saved unsupported/unmatched assessment context |
| GET | `/activity?page=1` | Read-only combined management audit |

Use query parameters for disease identity, including condition names containing
slashes. Unknown records return `404/record_not_found`; protected baseline
changes return `403/protected_definition`; duplicate aliases return
`409/alias_exists`. Other authentication behaviour remains unchanged.

## Files

- `app/admin_system_routes.py`: authenticated management API and audit reads.
- `app/services/system_management.py`: current description overlays and NLP counts.
- `app/db/models.py`: review fields, disease information, activity and diagnostic tables.
- `app/main.py`: router, validation redaction, mapping event recording and prediction overlays.
- `alembic/versions/c83e10a5b642_add_system_management.py`: migration after `d7a42f9b6e10`.
- `tests/test_admin_system_management.py`: integration/security tests.
- Existing candidate/V2 routing unit fixtures mock the new description lookup
  because those fixtures intentionally use a fake database.

## Running

In the backend root with its existing virtual environment and development
database configuration:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Expected head: `c83e10a5b642`. Startup refuses an outdated schema. No new Python
dependencies or credential changes are required.

Tests require `TEST_DATABASE_URL` pointing at the separate safe test database,
as for the existing suite; never point it at development or production data.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_admin_system_management.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

Implementation validation: the full 404-case run completed with 403 passes and
one setup error when PostgreSQL closed a connection during test-account creation.
That exact permissions test passed on rerun. The final 22-case focused run also
passed the new request-session/logging-failure checks and existing NLP/readiness
checks after the logging adjustment. One existing Starlette/httpx deprecation
warning remains. The development database migration/manual acceptance remains
for the user; automated runs use isolated schemas in the separate test database.

Security design references: [OWASP authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
and [OWASP input validation](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html).
