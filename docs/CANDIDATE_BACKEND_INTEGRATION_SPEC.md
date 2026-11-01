# Candidate Backend Integration Specification

Issue: #51

Status: implementation specification.
The candidate backend integration described here is not yet implemented.

## 1. Objective

Integrate the evaluated respiratory research candidate while preserving
symptom confirmation, independent safety checks, authenticated access
and historical prediction records.

The candidate uses:

- Model identity: candidate_v1_research.
- Selected estimator: rf_group_weighted.
- 70 numerical features representing 34 symptom concepts and demographics.
- 21 condition classes.
- Probability representation: temperature_scaled.
- Policy: candidate_v1_research_uncertainty_v1.
- Temperature: 0.6408387217626598.
- Minimum reporting units: 2.
- Minimum leading score: 0.78.
- Minimum top-two score margin: 0.20.

Exact values must come from verified artifacts. The backend must not
maintain independently tuned copies of the policy.

This integration does not establish clinical performance for Kenyan users.

## 2. Existing backend behaviour

The current flow is:

1. POST /nlp/map extracts possible canonical symptoms.
2. POST /symptoms/confirm records which suggestions the user confirms.
3. POST /predict repeats confirmation against the submitted text.
4. Confirmed red flags produce urgent_review without model prediction.
5. Otherwise, the active database model version selects V1 or V2.
6. Predictions are enriched with condition information.
7. A patient-owned prediction session is saved.

The API uses the database symptom catalogue. Updating JSON definitions
alone does not update the catalogue already stored in the database.

V2 accepts 66 features. It returns a leading condition even when its
uncertainty flag is true.

The V2 route separates unsupported evidence codes and predicts from the
supported subset when that subset is non-empty.

Prediction history currently stores status, predictions, confirmed codes,
model features and a model-version reference. It does not preserve the
complete uncertainty assessment returned by the prediction service.

These behaviours must not be assumed suitable for the candidate.

## 3. Scope and implementation sequence

Backend issues:

- #51: integration specification.
- #52: verified artifact loading and inactive registration.
- #53: confirmed symptom mapping.
- #54: API assessment responses and persistent history.
- #55: integration validation, activation and rollback documentation.

An ML repository issue supplies the portable verified runtime bundle.
Backend #52 depends on that bundle.

Outside this implementation:

- Model retraining or candidate reselection.
- Temperature fitting or threshold tuning.
- Adaptive follow-up questions.
- Evaluated use of explicit negative evidence.
- Frontend implementation.
- Automatic activation or clinical deployment approval.

## 4. Artifact and runtime requirements

The backend must consume a portable, versioned bundle exported from the
frozen ML candidate.

The bundle and its runtime distribution must supply:

- The selected model.
- The candidate feature schema and feature order.
- The model class labels and their order.
- The frozen calibration and uncertainty policy.
- Model, schema, policy and bundle identifiers.
- Integrity fingerprints and links to evaluation provenance.
- The inference implementation and its dependency specification.
- Reference cases for numerical and decision parity checks.

The backend must not depend on a sibling ML checkout or its experiment
directory being present on the same computer.

Bundle layout and code-distribution details will be defined by the ML
packaging issue and recorded during backend #52.

Fingerprint checks must occur before deserializing the model. Artifacts
must come from the controlled project export; matching hashes alone do
not establish trust in an arbitrary uploaded bundle.

Validate feature names, dimensions, class order and policy compatibility.

Missing or incompatible artifacts must make the candidate unavailable.
There must be no silent fallback to V1 or V2.

Load resources once per process after successful verification.
Artifact replacement requires a controlled restart and revalidation.

The candidate must be registered as inactive initially.

## 5. Request contract

Retain the authenticated POST /predict endpoint and its existing request
fields:

- age: integer from 0 through 120.
- sex: M or F.
- text: symptom text, subject to the existing 2,000-character limit.
- confirmed_codes: canonical symptom codes, subject to the existing
  50-entry limit.

The client must not supply trusted model features, policy thresholds,
model scores or the active model version.

For the candidate path, validate demographics without silently converting
a boolean age, fractional age or unknown sex into an accepted value.

Confirmed codes must be checked against recognition and confirmation
results. Do not accept invented codes merely because they appear in a
client request.

Repeated canonical codes must not increase the reporting-unit count.

An empty confirmed-code list is a valid candidate assessment request
and produces insufficient_information, provided other request validation
and resource requirements pass.

This phase adds no public negative-evidence or follow-up-answer fields.
If such fields are introduced later, their meaning must be explicit;
they must not be silently ignored as though negative answers were used.

## 6. Candidate symptom mapping

Map confirmed canonical symptoms using a reviewed, versioned candidate
mapping.

Do not build the candidate input from V2's already-filtered feature list.

The mapper must:

- Produce exactly the candidate schema's 70 features.
- Preserve unreported concepts as unknown.
- Preserve unsupported confirmed symptom codes separately.
- Preserve safety-only symptoms independently of numerical features.
- Validate the meaning and qualifiers of each supported concept.
- Record mapping/schema provenance for each candidate assessment.

For each concept:

| State | Present indicator | Absent indicator |
|---|---:|---:|
| Present | 1 | 0 |
| Unknown or unreported | 0 | 0 |
| Explicitly absent | 0 | 1 |

Explicit negative evidence is representable by the schema but is outside
the current candidate's evaluated inference inputs. If supplied through
an internal interface, it must produce unsupported_input.

Rejected NLP suggestions are not explicit negative symptom evidence.

### Regional pain

Confirmed head-region pain requires:

- E_53 present.
- head_region_pain present.

Confirmed chest-region pain requires:

- E_53 present.
- chest_region_pain present.

Do not invent an exact source location from a broad region report.

The region and required general-pain parent must not count as independent
reporting units. Use the evaluated reporting-unit implementation.

Tiredness with headache and tiredness with chest pain must produce
different feature vectors.

### Database catalogue

Audit the database-backed catalogue against the reviewed mapping.

Candidate mapping must not silently change the historical V2 mapping.
Any catalogue update must be explicit, versioned and tested.

Preserve unmatched wording for clarification. Do not guess numerical
features from unmatched text.

The absence of an unsupported-symptom flag does not establish that all
free text was understood or that the condition is within model scope.

## 7. Safety handling

The existing confirmed-red-flag route remains ahead of model inference.

It must return urgent_review with an empty predictions list and preserve
the existing guidance and patient ownership rules.

This route must not depend on successful candidate model loading.
A candidate artifact failure must not prevent the existing safety route
from returning its response.

For a safety response produced before model selection:

- No model prediction is performed.
- No candidate model or policy use is claimed.
- model_version_id remains null.

Candidate safety-only codes must also be preserved. If the candidate
adapter returns safety_review_required, translate that result into the
backend's urgent_review response while retaining the original assessment
status and any verified provenance available at that point.

A safety response does not require a disease prediction.

Existing red-flag logic is not asserted to be a complete clinical safety
assessment. This phase must preserve it and test that integration does
not bypass it.

## 8. Response contract

Keep the existing response envelope fields where applicable, including:

- prediction_id.
- status.
- age and sex.
- confirmed_codes.
- model_features.
- has_red_flag.
- predictions.
- Guidance and disclaimer fields.

For candidate requests, add a versioned assessment object.

### Status translation

| Candidate assessment status | Backend status | predictions |
|---|---|---|
| prediction_available | prediction_complete | One accepted condition |
| insufficient_information | insufficient_information | Empty list |
| unsupported_input | unsupported_input | Empty list |
| safety_review_required | urgent_review | Empty list |

The existing independent safety route also returns urgent_review.

Successful assessment responses use HTTP 200, including withholding.
An empty prediction list is a valid result, not an operational failure.

### Assessment fields

The assessment object must include:

- contract_version.
- status.
- model_version.
- policy_id.
- bundle_id.
- mapping_version.
- schema fingerprint.
- model fingerprint.
- policy fingerprint.
- predicted_condition.
- model_score.
- uncertain.
- uncertainty_reasons.
- reported_unit_count.
- probability_representation.
- prediction_performed.
- safety_flags.
- unsupported_symptoms.
- unsupported_absent_concepts.
- message.
- score_note.

Use contract_version 1 for this specification.

Fields unavailable because the independent safety route bypassed the
candidate must be null or empty as appropriate. Do not load the model
merely to fill them.

Always provide a score_note in the normalized candidate response, even
when the underlying ML response was blocked before scoring.

### Accepted results

For prediction_available:

- predicted_condition contains the canonical condition label.
- model_score contains the assessed score from 0 to 1.
- predictions contains one enriched condition.
- model_confidence_percent is the assessed score multiplied by 100
  and rounded for display.
- The score is described as a model score, not diagnostic certainty.

Evaluate policy thresholds using the original numerical precision,
before display rounding.

### Withheld results

For insufficient_information, unsupported_input or safety review:

- predicted_condition is null.
- model_score is null.
- predictions is an empty list.
- Reasons and explanatory text remain available.

Do not expose a hidden leading disease through another public field,
condition description or guidance text.

A response can have prediction_performed=true while withholding the
result because its score or margin failed the policy.

Research diagnostic rankings must not be included in public API or
history responses.

## 9. Persistence and historical compatibility

Add a nullable JSONB assessment field to prediction_sessions through an
additive Alembic migration.

Persist the normalized assessment object as returned at assessment time.

Preserve the existing predictions field as the snapshot of accepted,
enriched predictions. Withheld sessions store an empty list.

Persist candidate model-version linkage when a candidate was selected
and successfully assessed, including cases withheld before scoring.

For safety responses produced before model selection, retain a null
model-version reference and do not invent candidate provenance.

Do not save an ordinary successful assessment when artifact loading,
inference or persistence fails.

The prediction session and assessment must be saved in one transaction.

Patient and administrator list/detail history endpoints must expose the
saved assessment consistently, with existing ownership rules unchanged.

For older V1/V2 rows:

- assessment remains null.
- Existing statuses and prediction snapshots remain readable.
- Do not infer historical policy IDs or uncertainty reasons.
- Do not recompute old predictions using the candidate.
- Do not re-enrich historical labels from today's condition catalogue.

Keep the model/policy provenance snapshot even if the related model
registration is later changed or removed.

## 10. Error handling

Preserve existing authentication and authorization behaviour.

Use:

| Situation | Intended handling |
|---|---|
| Invalid request shape or demographic values | HTTP 422 |
| Invalid confirmation or contradictory user input | HTTP 400 |
| Valid but insufficient or unsupported evidence | HTTP 200 assessment |
| Missing catalogue or unavailable active model configuration | HTTP 503 |
| Missing, modified or incompatible candidate artifacts | HTTP 503 |
| Unexpected inference or persistence failure | Server error; no fabricated assessment |

Introduce explicit exception boundaries so an artifact-validation
ValueError is not reported as a user input error.

Public operational errors must not expose credentials, stack traces or
internal filesystem paths.

Do not substitute another model automatically after failure.

## 11. Frontend compatibility

Adding response fields alone does not establish frontend compatibility.

Before activation, the frontend must be verified to:

- Handle empty predictions without indexing a missing first result.
- Display insufficient_information and unsupported_input appropriately.
- Preserve urgent_review behaviour.
- Avoid presenting a disease name or score for withheld results.
- Display candidate score explanations.
- Render historical records where assessment is null.
- Distinguish operational errors from ordinary withholding.

Until those checks are completed, keep V2 active in the user-facing
application.

Frontend implementation belongs to a separate work item.

## 12. Validation plan

Backend #52 through #55 must cover:

- Valid bundle loading and inactive registration.
- Modified and missing bundle components.
- Feature and class order mismatches.
- Numerical parity with the frozen ML interface.
- Exact agreement on labels, statuses and policy decisions.
- Regional-pain distinction and reporting-unit counts.
- English, Kiswahili and mixed-language confirmation paths.
- Unsupported-only and supported-plus-unsupported inputs.
- Zero-unit and one-unit withholding.
- Explicit negatives through the internal service boundary.
- Contradictory input.
- Safety handling before model loading.
- No public diagnostic-ranking leakage.
- Persistence and history for accepted and withheld results.
- Existing V1/V2 history and routing.
- Authentication, patient ownership and administrator access.
- Operational-error handling without silent fallback.

Numerical comparisons may use a documented tolerance. A tolerance must
not excuse a different status or acceptance decision.

Use an isolated PostgreSQL test database. The existing test configuration
creates and removes a temporary schema and requires TEST_DATABASE_URL.

Do not run integration tests against the application database.

## 13. Activation and rollback

The intended sequence is:

1. Export and verify the portable ML bundle.
2. Install and validate it in the backend environment.
3. Apply the additive database migration.
4. Register the candidate as inactive.
5. Complete mapping, API, history and parity tests.
6. Complete frontend compatibility work.
7. Make a separate activation decision.
8. Switch the active model in a controlled transaction.
9. Restart processes and verify the resulting model identity.

Document the previous active model before switching.

Rollback selects the previous verified model and restores the required
process state. It must preserve candidate history and the additive
assessment data rather than deleting them.

A rollback changes future routing; it does not rewrite historical results.

## 14. Follow-up questions

Adaptive questions are a separate feature.

The current API can accept a later submission containing additional
confirmed symptoms, but this specification does not implement a
multi-turn questioning session or question-selection algorithm.

Yes/No/Not sure answers require explicit state handling. In particular,
No must not be silently converted to unknown.

Evaluate negative-evidence handling, question selection, stopping rules
and the user experience before enabling automated questions.

## 15. Completion criteria for issue #51

Issue #51 is complete when this specification is reviewed and committed.

It does not require artifact loading, database migration, endpoint changes,
model activation or frontend changes.

Implementation completion is tracked separately in backend #52–#55 and
the ML bundle issue.