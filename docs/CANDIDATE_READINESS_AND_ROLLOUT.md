# Candidate Readiness, Activation and Rollback

Backend issue: #55

## Current decision

Backend integration checks have passed.

The application remains on v2_23class_prototype.
The candidate_v1_research registration remains inactive.

Frontend compatibility has not yet been verified.
Application candidate activation is therefore pending.

This work does not establish clinical performance for Kenyan users or
approve the system for clinical diagnosis.

## Implemented readiness checks

Startup checks that all ORM-required tables and columns exist.
This detects a missing prediction_sessions.assessment column before
requests are served.

The startup check does not validate every column type, constraint or
Alembic revision.

The read-only readiness audit separately checks:
- The expected Alembic revision.
- The nullable JSONB assessment column.
- V2 as the sole active model.
- An inactive candidate registration matching verified release metadata.
- Canonical database symptom fields against the reviewed catalogue.
- The candidate wheel fingerprint.
- Bundle integrity and runtime environment compatibility.
- All 13 saved ML reference cases.

Reference decisions, labels and response fields must match exactly.
Floating-point scores allow an absolute tolerance of 1e-12.
This tolerance does not excuse a different acceptance decision.

The audit checks the wheel file hash, not every installed module's bytes.
It checks canonical symptom fields, not every NLP alias.

Run the audit from the backend root:

```powershell
python -B -m app.db.audit_candidate_readiness
```

It performs no database writes or activation changes. It writes:

reports/candidate_readiness/readiness.json

This is a pre-activation audit expecting V2 to be active. It is not a
post-activation candidate health check.

## Evidence and chronology

The issue #54 full backend suite passed 294 tests.

The issue #55 focused checks passed:
- Six startup schema tests.
- All 13 frozen reference cases in the readiness audit.
- One activation and rollback integration rehearsal.

The readiness JSON was produced before the rollback rehearsal. Its
rollback_rehearsal=pending field describes that earlier audit checkpoint.
The later passing rehearsal is recorded here and in the PR validation.

Run the final full suite before merging issue #55 and record its result
in the PR.

## What the rollback rehearsal verifies

The rehearsal runs only in respiratory_test, inside pytest's temporary
test schema.

It verifies:
- A stale expected previous model prevents candidate activation.
- Conflicting candidate metadata prevents activation.
- An uncommitted activation can be rolled back.
- Committed candidate selection routes requests to the candidate.
- Accepted and withheld candidate assessments are persisted.
- Selecting V2 again changes future prediction routing.
- Existing candidate assessments and prediction snapshots remain intact.
- Candidate history remains readable after rollback.
- The assessment column remains present.
- New V2 sessions retain assessment=NULL.

The test restores the original test registry state afterwards.
It does not activate the candidate in the application database.

## Preconditions for future application activation

Complete and record the following before running activation:

1. Verify the exact backend release and its passing tests.
2. Install the verified wheel and complete artifact bundle.
3. Apply Alembic migrations and confirm the application database target.
4. Run the pre-activation readiness audit in a fresh process.
5. Verify the previous active V2 model and its rollback artifacts.
6. Complete frontend compatibility checks.
7. Record a separate decision to activate this research candidate.
8. Pause prediction traffic while switching and restarting workers.

Frontend checks must cover:
- Empty predictions without indexing a missing first result.
- insufficient_information and unsupported_input.
- urgent_review.
- No disease name or score for withheld assessments.
- Candidate model-score explanations.
- History where assessment is either an object or null.
- Operational errors distinct from ordinary withholding.
- Confirmation labels that preserve the supported symptom qualifiers.
- No claim that unmatched wording was understood.
- Strict request fields accepted by the backend.

Adaptive follow-up questions are not implemented by this work.

## Future activation procedure — not executed for issue #55

Use the backend virtual environment and the intended application
configuration.

The following procedure requires the expected local application target:
database neondb, schema public.

Do not repurpose it for another environment without reviewing the target
checks.

```powershell
@'
from sqlalchemy import text
from app.core.database import SessionLocal, check_schema
from app.db.activate_candidate_model_version import (
    activate_candidate_model_version,
)
from app.ml.model_loader import V2_MODEL_VERSION

check_schema()

with SessionLocal.begin() as db:
    assert db.scalar(text("SELECT current_database()")) == "neondb"
    assert db.scalar(text("SELECT current_schema()")) == "public"

    candidate = activate_candidate_model_version(
        db,
        expected_previous_version=V2_MODEL_VERSION,
    )
    selected_version = candidate.version

print("Committed active model:", selected_version)
'@ | python -B -
```

The activation function validates frozen registration metadata and locks
model-version rows. The caller controls the commit.

Importing the module does not activate anything. The function does not
establish frontend readiness or clinical deployment approval.

After committing:
- Restart backend workers so resources are verified in fresh processes.
- Check that exactly one database model is active and it is the candidate.
- Run authenticated smoke checks through the application.
- Confirm a withheld response has empty predictions and null condition/score.
- Confirm a safety response bypasses candidate prediction.
- Confirm assessment persistence and history display.

Use designated test accounts for smoke checks. Requests save ordinary
history records.

The existing /model/status endpoint reports the legacy V1 artifact loaded
by the process. It does not identify the database-selected prediction
model. Do not use it as evidence of candidate activation.

## Rollback procedure

Rollback changes future model selection. It must not delete candidate
registrations, prediction history or assessment data.

Pause prediction traffic before switching.

The following procedure expects the candidate to be the sole active
model and verifies V2 artifacts through the existing activation service.

```powershell
@'
from sqlalchemy import select, text
from app.core.database import SessionLocal
from app.db.activate_v2_model_version import activate_v2_model_version
from app.db.models import ModelVersion
from app.ml.candidate_loader import CANDIDATE_MODEL_VERSION

with SessionLocal.begin() as db:
    assert db.scalar(text("SELECT current_database()")) == "neondb"
    assert db.scalar(text("SELECT current_schema()")) == "public"

    versions = list(
        db.scalars(
            select(ModelVersion)
            .order_by(ModelVersion.id)
            .with_for_update()
        ).all()
    )
    active = [row for row in versions if row.is_active]
    assert len(active) == 1
    assert active[0].version == CANDIDATE_MODEL_VERSION

    restored = activate_v2_model_version(db)
    restored_version = restored.version

print("Committed rollback model:", restored_version)
'@ | python -B -
```

After rollback:
- Restart backend workers.
- Verify V2 is the sole active model.
- Verify new predictions report v2_23class_prototype.
- Confirm candidate history remains readable and unchanged.
- Keep the additive assessment migration.

Do not use Alembic downgrade as a model rollback.
Downgrading the assessment migration removes saved assessment data.

Do not run model-seeding commands as an activation or rollback shortcut.

## Interpretation of prediction checks

The tiredness/headache and fever/cough reference examples are withheld.

The tiredness/chest-pain reference example still returns an accepted
Pulmonary neoplasm result. Matching this result verifies preservation of
the frozen model's behaviour, not clinical correctness.

Calibration and withholding reduce some misleading outputs but do not
make every accepted prediction clinically reliable.

## Reproducibility

From the backend virtual environment, with TEST_DATABASE_URL configured:

```powershell
python -B -m pytest tests/ -q
git diff --check
```

The shared pytest configuration applies migrations inside a temporary
schema in the dedicated test database.

The application database must not be used for the integration test suite.