# Candidate runtime setup and registration

Backend issue: #52
ML release: issue #58, merged in ML PR #59
Runtime version: 0.1.0

## Implemented scope

The backend can load the verified candidate and prepare an inactive
model-version registration.

Candidate request routing, symptom mapping, assessment persistence,
frontend handling and activation are separate work in issues #53–#55.

## Artifact layout

The backend expects:

app/ml/artifacts/candidate_v1/
    respiratory_candidate_runtime-0.1.0-py3-none-any.whl
    bundle/
        manifest.json
        model.joblib
        feature_schema.json
        policy.json
        classes.json
        reference_cases.json
        provenance/

This directory is ignored by Git. A backend clone does not include
these artifacts.

Trusted release records are committed under:

docs/candidate_runtime_release/

Transfer the wheel and complete bundle from the reviewed ML release.
The backend does not read a sibling ML repository during operation.

## Trusted release identities

Bundle manifest SHA-256:

52ee494aedd887210a0c4ca991d94321321af0e3766ee7b63f0742867730858b

Wheel SHA-256:

08d31afb8934fe8ed37a573a7af032fd4f8156753f274e9cf17df7b48371d05e

The trusted manifest digest comes from the committed release record.
Do not replace that digest with one calculated from an unknown bundle.

## Environment and installation

Use Python 3.11.9.

For a fresh development environment, install the backend dependencies
with the verified runtime constraints:

```powershell
python -m pip install -c docs/candidate_runtime_release/runtime_constraints.txt -r requirements-dev.txt
```

After transferring the verified artifacts, install the candidate wheel:

```powershell
python -m pip install --no-deps --require-hashes -r requirements-candidate.txt
python -m pip check
```

The runtime checks exact versions of Python, numpy, pandas, scipy,
scikit-learn and joblib against its verified release.

## Backend loading

```python
from app.ml.candidate_loader import load_candidate_runtime

runtime = load_candidate_runtime()
```

Loading checks the trusted release identity and delegates payload,
schema, class-order and environment verification to the installed runtime.

The first successful load is cached under a process lock.
Loading performs no predictions or database writes.

Failed loads are not cached. They raise ServiceNotReadyError with the
original exception retained as its cause. No alternate model is loaded
as a fallback.

Artifact replacement requires a controlled process restart and fresh
verification.

## Registration behaviour

```python
from app.core.database import SessionLocal
from app.db.register_candidate_model_version import (
    register_candidate_model_version,
)

with SessionLocal.begin() as db:
    record = register_candidate_model_version(db)
```

This example commits registration in the database selected by the
application configuration. Run it only when intentionally registering
the candidate in that environment.

A new record is inactive.

An identical existing record is returned without changing its activation
status or metadata. Conflicting metadata raises
CandidateRegistrationConflictError rather than replacing historical data.

The registration function itself does not commit. The caller owns the
transaction. It does not seed, deactivate or activate other model versions.

Importing the module does not run registration.

## Registry sample counts

The candidate uses these definitions:

| Registry field | Meaning | Count |
|---|---|---:|
| training_samples | Unique fitting source groups | 334588 |
| validation_samples | Reserved uncertainty-evaluation source groups | 16741 |
| test_samples | Primary official-test source groups | 53272 |

The 662036 fitting observations are recorded separately in parameters.

Model-selection summaries refer to a different validation cohort from
the reserved uncertainty evaluation. These counts must not be presented
as interchangeable patient totals.

Registry metadata contains verified evaluation summaries and provenance.
It does not claim clinical validation or deployment approval.

## Testing

Standalone loader tests require no database:

```powershell
python -B -m pytest --noconftest tests/test_candidate_model_loader.py -q
```

Registration integration tests and the full suite require
TEST_DATABASE_URL to identify a dedicated PostgreSQL test database.

The local setup uses respiratory_test, separate from the application's
neondb. Configure TEST_DATABASE_URL in the terminal without committing
credentials or changing the application .env file.

The shared test setup creates a temporary schema, applies migrations,
seeds baseline data and removes that schema afterward.

```powershell
python -B -m pytest tests/test_candidate_model_registration.py tests/test_candidate_model_loader.py -q
python -B -m pytest tests/ -q
```

Do not use --noconftest for the database integration tests.

Tests cover inactive creation, repeated registration, metadata conflicts,
activation-state preservation and rollback in PostgreSQL.

## Limitations

The candidate remains a research model evaluated on simulated data.
Scores are not clinically validated individual disease risks.

Loading or registering the candidate does not activate it or implement
its API prediction path.