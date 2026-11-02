# Candidate Assessment and History Integration

Issue: #54

## Implemented behaviour

The authenticated POST /predict endpoint supports the evaluated
candidate_v1_research model when it is selected by the active database
registration.

This implementation does not activate the candidate.

The request contains:
- age: a strict integer from 0 through 120.
- sex: M or F.
- text: up to 2,000 characters.
- confirmed_codes: up to 50 canonical symptom codes.

Unexpected request fields are rejected. Explicit negative answers,
client-supplied feature vectors and client-selected model settings are
not supported by this request contract.

The backend repeats symptom confirmation on the server before prediction.

## Independent safety handling

Confirmed red flags are handled before active-model selection or candidate
loading.

The response:
- Has backend status urgent_review.
- Contains an empty predictions list.
- Includes a safety_review_required assessment.
- Records prediction_performed=false.
- Uses null model, policy, bundle and mapping provenance.
- Is saved without a model-version reference.

Empty unsupported-symptom lists in this response do not mean the candidate
evaluated every symptom. Candidate mapping was bypassed.

The existing safety rules are not a complete clinical safety assessment.

## Candidate assessment

The selected registration must match the verified runtime identity,
artifact filename, feature count, class count and runtime provenance.

Confirmed symptoms are mapped using backend_candidate_symptom_mapping_v1.
The service preserves unknown states, unsupported symptoms, clarification
requirements and unmatched wording.

The installed runtime applies the frozen calibration and uncertainty
policy. The backend does not tune or duplicate its acceptance thresholds.

| Runtime status | Backend status | Predictions |
|---|---|---|
| prediction_available | prediction_complete | One enriched condition |
| insufficient_information | insufficient_information | Empty |
| unsupported_input | unsupported_input | Empty |
| safety_review_required | urgent_review | Empty |

Successful assessments, including withheld results, return HTTP 200.

The assessment uses contract_version=1 and includes the runtime decision,
model and policy identities, artifact fingerprints, mapping provenance,
reporting-unit count, uncertainty reasons and score explanation.

Only approved runtime fields are forwarded. Research diagnostic rankings
are excluded.

Accepted model scores retain their numerical precision in the assessment.
The display percentage is rounded after the runtime acceptance decision.

Withheld assessments contain null predicted_condition and model_score.
Condition enrichment runs only for accepted predictions.

A withheld assessment may have prediction_performed=true when scoring
occurred but the score or margin failed the frozen policy.

## Persistence and history

Alembic revision d94a71f26c30 adds a nullable JSONB assessment column to
prediction_sessions.

The prediction session and its assessment are committed together.
A failed commit rolls back and propagates the failure.

Existing V1/V2 callers can omit the assessment. Existing rows retain SQL
NULL rather than receiving fabricated historical uncertainty metadata.

Candidate assessments retain the selected model-version reference,
including assessments withheld before scoring.

The following endpoints return the saved assessment:
- GET /history
- GET /history/{prediction_id}
- GET /admin/history
- GET /admin/history/{prediction_id}

Existing patient ownership and administrator access rules remain in place.
History returns saved predictions and assessment snapshots without
rerunning inference or updating historical condition descriptions.

The assessment's provenance remains stored independently of the nullable
model-version relationship.

## Errors

- Invalid request fields or demographics: HTTP 422.
- Invalid symptom confirmation or mapping input: HTTP 400.
- Unavailable or incompatible candidate resources: HTTP 503.
- Unexpected candidate inference or persistence failure: HTTP 500.

Candidate operational errors use generic public messages.
No fallback model is selected after a candidate failure.

## Validation

Focused checks cover:
- Nullable assessment storage and SQL NULL compatibility.
- Accepted and withheld assessment snapshots.
- Failed database commits.
- Public response filtering and internal error boundaries.
- Strict request validation and candidate HTTP routing.
- Independent safety handling without candidate availability.
- Patient and administrator history responses.
- Real runtime, database-backed confirmation and PostgreSQL persistence.
- Registration mismatch rejection without saving a result.

Database tests require TEST_DATABASE_URL pointing to respiratory_test.
The shared test setup creates and removes a temporary schema.

The candidate integration fixture temporarily selects the candidate only
inside that test schema and restores the previous test registration.

## Interpretation and remaining work

The original tiredness/headache and fever/cough examples are withheld by
the evaluated candidate policy.

The frozen tiredness/chest-pain example still produces an accepted
Pulmonary neoplasm result. Its regression test verifies preservation of
the frozen behaviour, not clinical correctness.

These results do not establish clinical performance for Kenyan users.

Before running this backend version against the application database,
apply the additive Alembic migration after testing.

Keep the assessment column when rolling back model selection to V2.
Downgrading the migration would remove stored assessments.

Issue #55 covers integration readiness and activation/rollback procedures.
Frontend compatibility must be completed before user-facing candidate
activation.

Adaptive follow-up questions remain a separate feature.