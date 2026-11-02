# Candidate symptom mapping

Backend issue: #53

Mapping ID: backend_candidate_symptom_mapping_v1
Schema ID: respiratory_candidate_v1

## Scope

The candidate mapper converts a completed server-generated symptom
confirmation into the evaluated candidate's 70 numerical features.

It does not perform NLP, calculate predictions, modify the symptom
catalogue, change V1/V2 mapping or activate the candidate.

API routing and assessment persistence are implemented separately in #54.

## Implementation

Mapping definition:

app/data/candidate_symptom_mapping_v1.json

Mapping service:

app/services/candidate_symptom_mapping.py

Usage:

```python
from app.services.symptom_confirmation import confirm_symptoms
from app.services.candidate_symptom_mapping import (
    map_candidate_confirmation,
)

confirmation = confirm_symptoms(
    text=text,
    confirmed_codes=confirmed_codes,
    db=db,
)

mapped = map_candidate_confirmation(age, sex, confirmation)
```

Callers must obtain confirmation from the server service. A client-supplied
confirmation dictionary is not a trusted substitute.

The existing confirmed-red-flag route must remain ahead of candidate
loading and inference when this mapper is integrated into the API.

## Catalogue audit

The local read-only audit of the application database found:

- 36 canonical symptoms in the files and database.
- 165 alias entries in the files and database.
- No missing or additional symptom codes.
- No catalogue field differences.
- No missing or additional aliases.

No database synchronization or catalogue update was required.

This records the audited state; it does not guarantee that the database
catalogue can never change.

The mapper checks recognised and confirmed symptom metadata against the
reviewed catalogue and rejects detected differences.

## Mapping categories

| Category | Codes | Behaviour |
|---|---:|---|
| Supported | 24 | Encode the reviewed concepts as present |
| Clarification required | 9 | Preserve the code as unsupported; leave its concepts unknown |
| Safety only | 3 | Preserve unsupported status and independent safety flags |

The mapping definition records all codes and their exact concept mappings.

Supported mappings assume the user confirms the reviewed canonical
display label, including its qualifiers. For example, the current
tiredness label is "Constant tiredness".

Broad aliases remain recognition suggestions. They do not independently
establish every qualifier of a model feature.

## Clarification-required symptoms

The following codes currently require clarification:

- drooling
- hoarse_voice
- runny_blocked_nose
- itchy_eyes
- pain_deep_breath
- muscle_aches
- heavy_sweating
- loss_of_appetite
- weight_loss

Their required meanings are recorded in the mapping definition and
returned in clarification_required.

This mapper does not infer qualifiers from additional free-text wording.
It does not implement follow-up questions.

Unsupported codes must be passed to the candidate assessment layer.
They must not be silently discarded to obtain a prediction.

## Regional pain and reporting units

Headache encodes E_53 and head_region_pain.

Chest pain encodes E_53 and chest_region_pain.

The mapper does not invent forehead, upper-chest or other exact locations.

The verified runtime's reporting-unit helper counts a region and its
required general-pain parent as one unit.

Tiredness with headache and tiredness with chest pain therefore have
different feature vectors, each containing two reporting units.

No additional symptom relationships are inferred automatically.

## Unknown and rejected symptoms

Unreported concepts remain unknown:

- present indicator = 0
- absent indicator = 0

Rejected NLP suggestions are not explicit negative evidence.
This mapping interface generates no absent indicators.

Duplicate confirmed codes do not increase reporting-unit counts.

Unmatched wording is preserved without generating features.
Sneezing is absent from the current backend catalogue; tested sneezing
wording remains unmatched and is not converted into wheezing.

An empty unsupported list does not establish that every part of the
user's text was understood.

## Safety handling

All eight existing canonical red-flag codes are preserved:

- coughing_blood
- shortness_of_breath
- stridor
- drooling
- chest_pain_at_rest
- blue_lips
- confusion
- fainting

Safety flags remain present even when numerical mapping is unsupported.

The mapper is not a complete clinical safety assessment.
The earlier API safety route must not depend on candidate availability.

## Output and provenance

The mapper returns:

- feature_values in the exact candidate feature order.
- Deduplicated confirmed_codes and supported_codes.
- unsupported_symptoms and clarification_required.
- safety_flags and has_red_flag.
- Present, absent and unknown concept state.
- reporting_units and reported_unit_count.
- Preserved unmatched_wording and has_unmatched_wording.
- mapping_provenance.

Provenance includes mapping identity, mapping-content fingerprint,
catalogue-content fingerprint, schema identity and fingerprint, and
the verified bundle-manifest fingerprint.

Mapping and catalogue semantic fingerprints use UTF-8 JSON with sorted
keys, compact separators and ensure_ascii=False. Formatting and line
endings do not change these semantic fingerprints.

The original catalogue byte fingerprint remains recorded as audit context.

## Validation and errors

Age must be an integer from 0 through 120; booleans are rejected.
Sex must be exactly M or F.

The mapper validates completed confirmation, recognised/confirmed code
consistency, reviewed catalogue metadata and safety-flag consistency.

CandidateMappingInputError represents invalid mapping input.
ServiceNotReadyError represents unavailable or incompatible mapping
resources, including detected catalogue metadata changes.

The legacy flat model_features list is not used to construct candidate
features.

## Tests

Standalone tests:

```powershell
python -B -m pytest --noconftest tests/test_candidate_symptom_mapping.py -q
```

Database-backed integration tests require TEST_DATABASE_URL pointing to
the dedicated PostgreSQL test database:

```powershell
python -B -m pytest tests/test_candidate_symptom_mapping_integration.py tests/test_candidate_symptom_mapping.py -q
```

The combined mapping suite passed 57 tests, covering:

- English/Kiswahili and spelling-error confirmation.
- Distinct regional-pain features.
- Unknown states and rejected suggestions.
- Duplicate confirmations.
- Clarification-required symptoms.
- Every existing confirmed red flag.
- Unmatched sneezing wording.
- Catalogue drift and inconsistent confirmation.
- Preservation of existing confirmation output.
- Mapping without calculating predictions.

## Limitations

This mapping preserves reviewed software representation rules.
It does not establish clinical validation for Kenyan users.

Frontend confirmation wording must be verified before candidate activation.
Candidate API integration, persistent assessment metadata and activation
remain separate work.