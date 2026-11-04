# Administrator user-account management (frontend issue 72)

Administrators can list registered accounts and create/manage patient accounts.
Administrator accounts, including the acting administrator, are read-only here.
This implements the issue's **add users** option; it does not send invitations
or add OTP verification to registration.

## Account policy

- `is_active=true` and `is_blocked=false` are both required for sign-in and
  authenticated API access. Existing accounts retain these defaults.
- After a correct password, login returns `403/account_blocked` or
  `403/account_deactivated` with a contact-administrator message. Wrong
  passwords and unknown emails retain the same generic `401` response.
- Activation and blocking are independent. Unblocking an inactive account
  does not activate it, and activating a blocked account does not unblock it.
- Each actual access change increments the existing authentication version.
  Previous JWTs and previously issued reset codes cannot revive when access
  is restored. Existing sessions are rejected on their next protected request.
- Forgotten-password recovery does not issue usable codes to restricted
  accounts, and confirmation checks both availability and authentication version.
- User roles cannot be changed through these endpoints. New accounts are
  always patients. Full name/phone validation matches profile registration.
- Deletion permanently removes the target patient account, its saved
  assessments, their feedback and password-reset challenges in one transaction.
  Other accounts and their records remain intact.
- Deletion requires the target email, current administrator password and
  reviewed account revision. Five failed password checks per administrator
  within 15 minutes temporarily prevent further deletion verification.
  Ordinary login is not locked by this limit.
- Create/status/delete actions retain an audit reference with actor ID,
  target ID, action and timestamp. Audit rows contain no email, phone,
  password or assessment text. Failed deletion password checks are recorded.

## API

All routes require a current authenticated administrator and return
`Cache-Control: no-store` for account data. JWT role claims alone never grant
administrator access: permissions come from the current database account.

| Method and route | Input / behaviour |
| --- | --- |
| `GET /admin/users` | `q` (up to 100 characters), optional `role=patient/admin`, optional `status=active/inactive/blocked`, `page>=1`, `page_size=1..100` |
| `POST /admin/users` | `email`, required `full_name`, optional `phone_number`, initial `password` (8–128 characters, not blank) |
| `PATCH /admin/users/{id}/status` | `action=block/unblock/activate/deactivate`, `expected_revision` |
| `DELETE /admin/users/{id}` | Body: `confirmation_email`, `admin_password`, `expected_revision` |

The list returns `{users, total, page, page_size}`. `total` counts all matching
accounts, not just the current page. Search is a literal, case-insensitive
substring search of name, email or phone. The active filter means **can sign in**;
inactive and blocked filters inspect their individual flags.

Account responses whitelist ID, email, name, phone, role, status flags,
creation date and `revision`. They never return password hashes. Mutation
payloads forbid extra fields, and validation errors omit submitted input values.
Passwords are sent in request bodies, never URL parameters.

Status changes lock the target row and compare `expected_revision`. A stale
review returns `409/account_changed`; refresh before deciding again. Unknown
targets return `404/account_not_found`; protected administrator targets return
`403/protected_account`. Duplicate email creation returns `409/email_exists`.

## Migration and verification

Migration `d7a42f9b6e10` follows `a62d9e14c730`. It adds the two status flags and
the `admin_account_actions` table. It has been applied to the development
database. A downgrade refuses to remove availability enforcement while any
account remains blocked or inactive; restrictions must be reviewed deliberately.

```powershell
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Tests require the existing dedicated `TEST_DATABASE_URL`, pointing at
`respiratory_test`, never the application database. The shared fixture creates
and removes its own temporary schema, and applies all migrations there.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_admin_user_management.py tests/test_authentication.py tests/test_registration_profile.py tests/test_password_reset.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

New tests cover authorization, forged role claims, normalization, pagination,
search/filter correctness, status independence, session/reset-code revocation,
protected accounts, concurrent updates, deletion confirmation, password-attempt
limits, ownership of removed records and transaction rollback.

Verification completed: 385 backend tests passed with one existing
Starlette/httpx deprecation warning. The development schema check passed
at migration `d7a42f9b6e10`.

Relevant guidance: [OWASP authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
and [reauthentication for sensitive actions](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html#require-re-authentication-for-sensitive-features).

## Changed files

- `app/admin_user_routes.py`: admin-only list/create/status/delete API.
- `app/services/account_status.py`: shared sign-in availability rule.
- `app/db/models.py`: status flags and audit-reference model.
- `app/core/auth.py`: reject restricted accounts on protected requests.
- `app/services/auth.py`: reject restricted accounts during login.
- `app/services/password_reset.py`: apply availability to recovery.
- `app/main.py`: mount router and redact management validation input.
- `alembic/versions/d7a42f9b6e10_add_account_management.py`: schema migration.
- `tests/test_admin_user_management.py`: integration/security regressions.
- `tests/test_password_reset.py`: check the recovery response schema without
  mistaking unrelated numeric OpenAPI limits for a leaked OTP.

The repository owner handles Git, PRs and issue closure. Merge the backend
before the frontend PR for issue 72.
