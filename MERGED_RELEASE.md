# Merged backend release

Partner account/profile/password-reset/admin features are preserved. The active local assessment path is now **V3 partial-evidence research**, 34 features and 21 classes. Existing V1/V2/independent urgent history remains readable. This is not a clinically validated diagnostic service.

## Current machine

Use `partner-merged/Start-Merged.ps1` from the workspace. API: `http://127.0.0.1:8010`; UI: `http://127.0.0.1:5180`. The private local `.env` points only at `respiratory_partner_merge_test`. The original app/database is separate. Register a new patient account rather than assuming original accounts were imported.

The full integration/evidence report is `../../MERGE_RESULT.md` in this extracted layout. Test artifacts are in `../../release/`.

## On another machine / existing project

Do not copy this machine's `.env` or database. Install requirements in a target-local Python environment and use `.env.example` with your own secure JWT secret, PostgreSQL URL, CORS origins and SMTP details. Retain your partner's actual SMTP/deployment settings privately. No real email delivery can work without those settings.

On a **new disposable test database first**, from this backend root:

```powershell
python -m alembic upgrade head
python -m app.db.seed_symptom_catalog
python -m app.db.register_awareness_model --activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Migration, catalogue seeding and activation are **explicit setup actions**, not commands to repeat on every start. Do not blindly seed over an existing customised SQL catalogue. For an existing populated deployment, back it up and review the additive migrations first. Do not run `seed_model_version` afterwards: that command reactivates legacy V1.

Check `/health`, `/readiness`, `/model/status` and authorised `/admin/system/model`. V3 readiness must report `v3_partial_evidence`, policy `ddxplus_selective_research_v1`, `clinical_validation: false`, and no learned COVID/taste/duration capability.

## Changed serving behaviour

- `app/nlp/reported_context.py` extends the served SQL mapper with state/subject/timing/precise-evidence rules; keep it with the modified `symptom_mapper.py`.
- `/symptoms/confirm`, `/safety/reviews` and `/predict` accept explicit corrections. `/predict` remaps original text and needs those corrections again; do not trust arbitrary browser features.
- `/safety/check` is unauthenticated and independent of SQL/model/history availability. A completely stopped API cannot serve it.
- Non-urgent V3 results use contract v2 and `predictions: []`. A displayed name is in `assessment.research_pattern`, not that empty array. Actual independent urgent API records retain contract v1.
- Unsupported/uncertain cases remain unnamed. Raw research rankings are stripped from patient API/history and are only available through authorised admin access.
- Current confirmed patient taste/smell changes trigger testing advice, not COVID diagnosis. Duration/taste remain context-only for scoring. Smell is a supported fitted feature.
- Admin-managed disease descriptions are included in new snapshots with new hashes; old snapshots are not recomputed. The exact V3 influenza display label remains `FLU(influenza)`.
- Admin monitoring/diagnostics recognise V3 without altering partner account security or introducing a second history store.

## Run regression checks

Use only a dedicated test database. The existing test fixture creates/drops an isolated random schema within it.

```powershell
$env:TEST_DATABASE_URL='<dedicated PostgreSQL test database URL>'
$env:FRONTEND_ORIGINS='http://localhost:5173,http://127.0.0.1:5173'
python -m pytest tests -q -k 'not candidate'
```

The explicit origins match existing partner CORS tests regardless of the local demo's port. Current selected result: **371 passed**. `tests/test_partner_merge.py` tests all 21 reference prompts through SQL-backed API/history, duration/negation/COVID, V3 admin readiness, reserved aliases, managed descriptions, corrected safety saves and 15 invalid-request cases that must never write history. Latest evidence is `../../release/pushcheck-20261106/backend-selected.xml` in this extracted layout.

The unrestricted backend suite is **not fully green**: the last complete run recorded **481 passed, 1 failed and 76 setup errors**, all in the old inactive candidate's unavailable original runtime/bundle path. Do not delete those tests, replace its artifacts or relax trusted hashes to claim success. The selected V3/partner-feature suite passes separately as stated above. The model's serious-condition false associations also remain material limitations; software checks are not clinical validation.

This is a review branch (`integration/nlp-v3-backend`), not a deployment or an automatic merge. Use it with the companion `integration/nlp-v3-frontend` branch and the separate ML research branch. Open a PR into the owner's intended working branch, inspect the diff and resolve any conflicts before merging. Private configuration, accounts, database contents and local environments are not included.
