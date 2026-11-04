# Forgotten-password recovery

OTP is used only to reset a forgotten password. Registration and ordinary
login do not require a code. Recovery never returns an access token.

## Database

Apply the migration before restarting the updated backend:

```powershell
python -m alembic upgrade head
python -m alembic current
```

The expected revision is `a62d9e14c730`. Existing users receive
`auth_version=0`, so their current tokens remain usable. A successful
password reset increments only that user's version and rejects all their
previous tokens. Login then issues a token for the new version.

The downgrade refuses to remove session revocation after a reset has
occurred. Plan a rollback after all previously issued JWTs have expired;
removing the version check immediately would revive revoked tokens.

## Email configuration

SMTP credentials belong only in the backend's ignored `.env`, never in
frontend Vite settings, source files, screenshots, or PR descriptions.
The application can start without SMTP, but recovery requests return a
generic 503 until a sender is configured.

Example for a dedicated Gmail sender:

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your-project-sender@gmail.com
SMTP_PASSWORD=your-generated-app-password
SMTP_FROM_EMAIL=your-project-sender@gmail.com
SMTP_FROM_NAME=RespiraCare
SMTP_TIMEOUT_SECONDS=10
```

Enable Google 2-Step Verification and create an app password yourself.
Use that app password without its display spaces, rather than the account's normal password.
Some school/work accounts and Advanced Protection accounts do not offer
app passwords. See [Google's instructions](https://support.google.com/mail/answer/185833?hl=en).

For another provider, change the SMTP settings. `starttls` and `ssl` are
supported; both validate the server certificate. Plaintext delivery and
certificate-verification bypasses are not supported. Resend's SMTP
service requires an API key and verified sending domain; see its
[SMTP documentation](https://resend.com/docs/send-with-smtp).

Restart the backend after changing `.env`. A sender connection error is
logged without SMTP details, recipient addresses or message contents.
Delivery runs after the generic request response. If delivery fails, the
corresponding unsent code is invalidated and the user may request another
code after the cooldown. Password-change notification failures do not
undo an already completed reset.

## API contract

`POST /auth/password-reset/request`:

```json
{"email":"patient@example.com","language":"en"}
```

Returns HTTP 202 with a random `challenge_id`, `expires_in_seconds=600`,
`resend_after_seconds=60`, and a generic message for both known and unknown
addresses. Neither the code nor account existence is returned. A resend
replaces the challenge, invalidating the old challenge ID and code.

`POST /auth/password-reset/confirm`:

```json
{
  "challenge_id":"11111111-1111-4111-8111-111111111111",
  "code":"012345",
  "new_password":"a-long-unique-password"
}
```

A valid code updates the password atomically and returns
`{"status":"password_reset","message":"Your password was reset. Please log in."}`.
The code cannot be consumed twice, including by simultaneous requests.
A security notification is sent to the account email without including
the new password. The frontend clears sensitive form state and asks the
user to log in; it never automatically authenticates a reset response.

Errors use `detail.code`: `invalid_code`, `expired_code`,
`attempts_exhausted`, `reset_rate_limited`, or `email_unavailable`.
HTTP 429 includes an exposed `Retry-After` header. Reset responses are
not cacheable; validation errors omit submitted values.

## Limits and storage

- Code lifetime: 10 minutes; maximum five wrong guesses per challenge.
- Resend cooldown: 60 seconds per normalised email address.
- Requests: five per email and twenty per client IP in a one-hour window.
- Confirmations: sixty per client IP in a one-hour window.
- Limits and challenges persist in PostgreSQL and apply across workers.
- Email/IP lookup keys and code digests use purpose-separated HMAC-SHA256.
- Only password hashes are saved. Plaintext codes are used for delivery
  and are never returned, persisted or logged.
- Failed recovery attempts never lock normal password login.

Client IP comes from the server's `request.client`; arbitrary
`X-Forwarded-For` headers are not trusted by this feature. When deploying
behind a proxy, configure Uvicorn's trusted proxy addresses explicitly.
Use HTTPS for the deployed frontend and API.

## Automated tests

Use the dedicated `TEST_DATABASE_URL` and existing test setup. The test
suite creates its own schema and never resets the application's database.
Tests capture synthetic messages; they do not contact Gmail or send email
to actual users.

```powershell
python -m pytest tests/test_password_reset.py tests/test_password_reset_email.py -q
python -m pytest -q
```

## Final browser check

1. Apply the migration, configure SMTP locally, and start both apps.
2. Use an existing test account whose mailbox you control.
3. Open Login > Forgot password and request a code.
4. Check the real inbox/spam folder. Enter an incorrect code first, then
   the current code and a new password.
5. Confirm the previous password fails and the new password logs in.
6. Confirm another browser session for the same account is rejected when
   it next calls a protected endpoint.
7. Request another code, wait for resend, and confirm only the replacement
   challenge/code works. Check expiry and English/Kiswahili presentation.
8. Close issue #70 only after the real email check and both PRs are merged.
