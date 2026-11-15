# Respiratory Health Awareness Backend

**Bilingual symptom processing, safety-first assessment, and patient history — powered by FastAPI.**

Backend API for the **Respiratory Health Awareness and Prediction System**. It
processes English, Kiswahili, and mixed-language symptom descriptions, lets users
confirm or correct recognised symptoms, checks red flags, and returns
research-based awareness assessments.

> **Research and education only.** This is not a clinically validated diagnostic
> service. It does not replace professional medical advice, diagnosis, treatment,
> or emergency care.

[Architecture](#system-architecture) · [Setup](#getting-started) ·
[API](#main-api-endpoints) · [Testing](#testing) ·
[Limitations](#limitations) · [Documentation](#further-documentation)

## System Architecture

![Architecture showing patient and administrator React frontends connected to FastAPI, bilingual symptom mapping, red-flag safety checks, the V3 research model, and PostgreSQL](docs/images/system-architecture.svg)

*Current V3 architecture, simplified. The React frontends are separate applications;
this repository contains the backend. Confirmed red flags bypass model inference.*

### How an assessment works

1. **Describe:** the patient enters symptoms in English, Kiswahili, or both.
2. **Review:** the backend maps supported wording; the patient confirms symptoms
   and corrects their state or subject where necessary.
3. **Check safety:** recognised, confirmed red flags return urgent guidance
   without running a model.
4. **Assess:** non-urgent supported evidence is evaluated by the active model.
   V3 names a tentative research pattern only when its selection policy permits.
5. **Save:** authenticated assessments are stored in the patient's history;
   patients can submit feedback on their own saved records.

## Current Assessment Behaviour

The current release documents **V3 partial-evidence research** as its selected
assessment path: **34 features and 21 condition classes**, with selection policy
`ddxplus_selective_research_v1`.

| Outcome | Behaviour |
| --- | --- |
| Confirmed red flag | Urgent guidance; model inference is bypassed |
| Eligible research pattern | A tentative pattern in `assessment.research_pattern` |
| Uncertain or unsupported evidence | No named pattern is presented |
| COVID-related context | Conditional testing advice, not a COVID diagnosis |

Important integration details:

- Non-urgent V3 responses contain a versioned `assessment` object and
  `predictions: []`. Read any selected pattern from
  `assessment.research_pattern`, not that empty array.
- A research score is **not the probability that a patient has a disease**.
  Full research rankings are restricted to authorised administrator access.
- Duration and taste changes are context-only, not V3 scoring features.
  Smell is a supported fitted feature.
- Existing V1, V2, and independent urgent-review history remains readable.
- The model served by a deployment depends on its database registration.
  Check `/model/status` rather than assuming V3 is active.

See [release notes](MERGED_RELEASE.md) for serving behaviour and compatibility
details.

## Features

### Symptom processing and safety

- English, Kiswahili, and mixed-language symptom mapping
- Alias and fuzzy matching within the supported vocabulary
- Symptom confirmation, corrections, and reported-context handling
- Red-flag checks before model selection
- Independent safety-check endpoint
- Research-based awareness guidance and versioned assessment snapshots

### Patient accounts

- Registration, login, and profile updates
- Argon2 password hashing and JWT authentication
- Email-based password recovery when SMTP is configured
- Patient-specific assessment history and feedback

### Administration

- Role-protected history and feedback access
- Patient-account creation and access management
- Active-model diagnostics
- Custom symptom-alias and disease-information management
- Feedback review and management activity records

### Backend infrastructure

- PostgreSQL storage with SQLAlchemy and Alembic migrations
- Request validation and service-readiness checks
- Automated API, integration, and regression tests

## Technology Stack

| Area | Technology |
| --- | --- |
| Runtime | Python 3.11 |
| API | FastAPI, Uvicorn, Pydantic |
| Database | PostgreSQL, SQLAlchemy, Psycopg |
| Migrations | Alembic |
| NLP matching | Python, RapidFuzz |
| Model tooling | NumPy, pandas, scikit-learn, joblib |
| Authentication | PyJWT, pwdlib, Argon2 |
| Testing | pytest, HTTPX |

Dependency versions are listed in [requirements.txt](requirements.txt).
Random Forest artifacts remain for legacy paths; they are not the V3 model.

## Getting Started

### Prerequisites

- Python 3.11
- An accessible PostgreSQL database
- Git

Use a **new disposable development database** for the initial setup. The commands
below run from the backend repository root.

### 1. Clone the repository

```bash
git clone https://github.com/RAM-AS-RAP/ucs31-madhaparia-backend.git
cd ucs31-madhaparia-backend
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux / macOS:

```bash
source .venv/bin/activate
```

Use `python3` to create the environment if `python` is unavailable.
Subsequent commands use `python` inside the activated environment.

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

For development and testing:

```bash
python -m pip install -r requirements-dev.txt
```

### 4. Configure the environment

Copy [`.env.example`](.env.example) to `.env`.

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Linux / macOS:

```bash
cp .env.example .env
```

Set your PostgreSQL connection, generate a JWT secret, and adjust allowed
frontend origins:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Your PostgreSQL connection URL; use your provider's TLS requirements |
| `JWT_SECRET_KEY` | A random secret of at least 32 characters; placeholders are rejected |
| `FRONTEND_ORIGINS` | Comma-separated frontend origins allowed by CORS |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access-token lifetime; default is 60 minutes |
| `SMTP_*` | Email delivery settings for password recovery |

Generate a JWT secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste the generated value into `JWT_SECRET_KEY` in your private `.env`.
Password recovery emails require valid SMTP settings; see
[password recovery setup](docs/PASSWORD_RESET_SETUP.md).

**Never commit `.env`, database credentials, JWT secrets, or SMTP passwords.**

### 5. Initialise the database and activate V3

For a fresh development database:

```bash
python -m alembic upgrade head
python -m app.db.seed_symptom_catalog
python -m app.db.register_awareness_model --activate
```

These are explicit setup actions, **not commands to repeat on every server start**.

> **Existing database:** Back it up and review migrations before upgrading.
> Do not blindly reseed a customised symptom catalogue.
> Activation changes the selected model registration.
> Do not run `app.db.seed_model_version` after activating V3:
> that command reactivates legacy V1.

### 6. Create an administrator — optional

```bash
python -m app.db.create_admin admin@example.com
```

Public registration creates patient accounts only. If the email already belongs
to a registered user, this command promotes that account to administrator and
preserves its password. Otherwise, it prompts for a new administrator password
or reads `ADMIN_PASSWORD` from the environment.

### 7. Start the API

```bash
python -m uvicorn app.main:app --reload --port 8000
```

| Resource | Address |
| --- | --- |
| API | http://127.0.0.1:8000 |
| Interactive API documentation | http://127.0.0.1:8000/docs |
| OpenAPI schema | http://127.0.0.1:8000/openapi.json |
| Health | http://127.0.0.1:8000/health |
| Readiness | http://127.0.0.1:8000/readiness |
| Active model | http://127.0.0.1:8000/model/status |

`/health` reports application liveness; it does not establish full readiness.
After V3 activation, inspect `/model/status` for:

```json
{
  "model_type": "v3_partial_evidence",
  "policy_id": "ddxplus_selective_research_v1",
  "clinical_validation": false
}
```

This is a subset of the expected response, not its complete schema.

## Typical API Flow

1. Register using `POST /auth/register`.
2. Log in using `POST /auth/login`.
3. Send symptom text to `POST /nlp/map`.
4. Let the user review recognised symptoms and submit them to
   `POST /symptoms/confirm`, including any corrections.
5. For a confirmed urgent case, save via `POST /safety/reviews` without requiring
   demographics or model selection. Otherwise, submit to `POST /predict`.
6. Display guidance and any permitted tentative research pattern.
7. Retrieve saved records through `/history` and optionally submit feedback.

Protected endpoints require:

```http
Authorization: Bearer <access_token>
```

### Example assessment request

```http
POST /predict
Content-Type: application/json
Authorization: Bearer <access_token>
```

```json
{
  "age": 25,
  "sex": "M",
  "text": "I have fever na kikohozi",
  "confirmed_codes": ["fever", "cough"],
  "language": "en"
}
```

Use codes returned by the mapper and confirmed by the user. If corrections were
made during confirmation, include them again in `/predict`: the backend remaps
the original text rather than trusting browser-supplied model features.

This example does **not** guarantee a named research pattern.
`/predict` also checks confirmed red flags before model selection.

## Main API Endpoints

| Area | Endpoints |
| --- | --- |
| Health and diagnostics | `GET /health`, `GET /readiness`, `GET /database/status`, `GET /model/status` |
| Authentication | `POST /auth/register`, `POST /auth/login` |
| Profile | `GET /auth/me`, `PATCH /auth/me` |
| Symptom mapping | `POST /nlp/map` |
| Confirmation | `POST /symptoms/confirm` |
| Safety | `POST /safety/check`, `POST /safety/reviews` |
| Assessment | `POST /predict` |
| Patient history | `GET /history`, `GET /history/{prediction_id}` |
| Feedback | `POST /feedback` |
| Administrator history | `GET /admin/history`, `GET /admin/history/{prediction_id}` |
| Administrator feedback | `GET /admin/feedback` |
| Account management | Routes under `/admin/users` |
| System management | Routes under `/admin/system` |

See `/docs` for complete schemas, password-reset routes, and access requirements.

- `/safety/check` requires no authentication, database, or model availability.
  The API itself must still be running.
- Saving safety reviews, submitting assessments, accessing patient history, and
  submitting feedback require authentication.
- Patients can access only their own history and submit feedback only for their
  own saved sessions. Duplicate feedback for a session is rejected.
- Administrator routes require a current authorised administrator account.

## Project Structure

```text
ucs31-madhaparia-backend/
├── alembic/                  # Database migrations
├── app/
│   ├── core/                 # Configuration, authentication, database
│   ├── data/                 # Condition information and mappings
│   ├── db/                   # Models, seeding, model registration
│   ├── ml/                   # Model loaders, predictors, artifacts
│   ├── nlp/                  # Symptom mapping and reported context
│   ├── services/             # Assessment and application logic
│   ├── admin_system_routes.py
│   ├── admin_user_routes.py
│   ├── password_reset_routes.py
│   └── main.py
├── docs/
│   ├── images/               # README architecture diagram
│   └── ...                   # Integration and feature documentation
├── reports/                  # Recorded readiness reports
├── tests/                    # Automated tests and fixtures
├── .env.example
├── MERGED_RELEASE.md
├── requirements.txt
└── requirements-dev.txt
```

## Testing

Use a **dedicated PostgreSQL test database**, never a database containing real
patient or production data.

The shared fixture creates a temporary schema, applies migrations and seeds test
data, then removes that schema after the run.

Windows PowerShell:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/respiratory_test"
$env:FRONTEND_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
python -m pytest tests -q -k "not candidate"
```

Linux / macOS:

```bash
export TEST_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/respiratory_test"
export FRONTEND_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"
python -m pytest tests -q -k "not candidate"
```

The explicit CORS origins match the existing regression tests.

### Recorded verification and known failures

The [release notes](MERGED_RELEASE.md) record **371 passing tests** for the
selected V3/account-feature suite. This is a recorded result, not a test run
performed for this README update.

The unrestricted suite is **not fully passing**: the last complete recorded
run recorded 481 passes, 1 failure, and 76 setup errors in the older inactive
candidate integration, which requires its original external runtime/bundle.

To include those candidate tests:

```bash
python -m pytest tests -q
```

Software regression results do not establish clinical validity.

## Security and Data Handling

- Passwords are hashed using Argon2.
- Protected routes use JWT authentication and current account checks.
- Patient history and feedback are ownership-restricted.
- Administrator access is role-protected.
- Database migrations are explicit; back up populated databases before upgrades.
- Saved symptom text, profiles, and assessments are sensitive health-related data.

Administrator patient-account deletion permanently removes the account and its
associated assessments, feedback, and password-reset challenges. Review
[account-management documentation](docs/USER_ACCOUNT_MANAGEMENT.md) before use.

## Limitations

- The models have **not been clinically validated**.
- Simulated DDXPlus profiles do not establish local clinical prevalence or
  real-world diagnostic accuracy.
- Serious-condition false associations remain a material limitation, regardless
  of any displayed research score.
- NLP recognition is limited to supported vocabulary, aliases, and context rules.
- An unnamed or uncertain result does not establish that a person is healthy.
- Red-flag rules are not a complete emergency triage system.
- Conditional COVID testing advice is not a COVID diagnosis.
- Historical model outputs may use different contracts from current V3 results.

**Users with severe, urgent, or rapidly worsening symptoms should seek appropriate
professional medical care rather than wait for an assessment from this system.**

## Further Documentation

- [Merged release and V3 behaviour](MERGED_RELEASE.md)
- [Password recovery setup](docs/PASSWORD_RESET_SETUP.md)
- [Administrator account management](docs/USER_ACCOUNT_MANAGEMENT.md)
- [Administrator system management](docs/SYSTEM_MANAGEMENT.md)
- [Architecture diagram](docs/images/system-architecture.svg)

Some supporting documents contain historical branch names, machine-specific
paths, or older verification results. Use this README for repository-root setup
and confirm the actual deployment state through the API.

## Related Work

Machine-learning development and NLP preparation are maintained separately in
[ucs31-madhaparia-ml](https://github.com/RAM-AS-RAP/ucs31-madhaparia-ml).
Frontend applications are maintained in
[ucs31-madhaparia-frontend](https://github.com/RAM-AS-RAP/ucs31-madhaparia-frontend).
This repository contains the backend API and inference integration.
