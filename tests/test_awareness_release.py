"""Release regressions: real parser/model, isolated SQL DB and authenticated API."""
import uuid
import subprocess
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.database import SessionLocal, SchemaNotReadyError
from app.core.security import create_access_token
from app.db.models import ModelVersion, User
from app.db.register_awareness_model import register
from app.main import app
from app.nlp.symptom_mapper import build_confirmation_output
from app.services.awareness import assess, public_assessment, MODEL_VERSION
from app.services.symptom_confirmation import confirm_symptoms, confirm_mapping


def mapping(text):
    return {s['code']: s for s in build_confirmation_output(text)['recognised_symptoms']}


@pytest.mark.parametrize('text,code,state,subject', [
    ('I have a cough but no fever', 'fever', 'absent', 'patient_or_unspecified'),
    ('Nina kikohozi lakini sina homa', 'fever', 'absent', 'patient_or_unspecified'),
    ('My mother has fever', 'fever', 'unknown', 'other_person'),
    ('Mama yangu ana homa', 'fever', 'unknown', 'other_person'),
    ('I am not sure whether I have fever', 'fever', 'unknown', 'patient_or_unspecified'),
    ('Sina uhakika kama nina homa', 'fever', 'unknown', 'patient_or_unspecified'),
    ('I had fever last week but not now', 'fever', 'absent', 'patient_or_unspecified'),
    ('I have cough but I do not have cough', 'cough', 'unknown', 'patient_or_unspecified'),
    ('My mother has fever but I have a cough', 'cough', 'present', 'patient_or_unspecified'),
    ('I have not lost taste', 'taste_change', 'absent', 'patient_or_unspecified'),
    ('I cannot taste anything', 'taste_change', 'present', 'patient_or_unspecified'),
    ('I can smell but cannot taste', 'loss_of_smell', 'absent', 'patient_or_unspecified'),
    ('does not taste well', 'taste_change', 'unknown', 'patient_or_unspecified'),
    ('My sister has loss of taste', 'taste_change', 'unknown', 'other_person'),
    ('sihisi ladha', 'taste_change', 'present', 'patient_or_unspecified'),
    ('ladha imepungua', 'taste_change', 'present', 'patient_or_unspecified'),
    ('ladha imebadilika', 'taste_change', 'present', 'patient_or_unspecified'),
])
def test_report_states(text, code, state, subject):
    symptom = mapping(text)[code]
    assert symptom['status'] == state
    assert symptom['reported_subject'] == subject


@pytest.mark.parametrize('text,detail', [('I cannot taste anything', 'loss'), ('reduced taste', 'reduced'), ('metallic taste', 'altered'), ('does not taste well', 'unclear')])
def test_taste_is_not_smell_or_trained_feature(text, detail):
    symptoms = mapping(text)
    assert symptoms['taste_change']['taste_detail'] == detail
    assert symptoms['taste_change']['features'] == []
    assert 'loss_of_smell' not in symptoms


@pytest.mark.parametrize('text', [
    'my sense of taste has reduced',
    'my sense of taste is reduced',
    'my taste has reduced',
    'my sense of taste has decreased',
])
def test_reduced_taste_sentence_forms_remain_context_only(text):
    symptom = mapping(text)['taste_change']
    assert symptom['status'] == 'present'
    assert symptom['taste_detail'] == 'reduced'
    assert symptom['features'] == [] and symptom['context_only']


@pytest.mark.parametrize('text,code,state,subject', [
    ('I can still smell normally', 'loss_of_smell', 'absent', 'patient_or_unspecified'),
    ('I am not sure whether my sense of taste has reduced', 'taste_change', 'unknown', 'patient_or_unspecified'),
    ('My mother says her sense of taste has reduced', 'taste_change', 'unknown', 'other_person'),
    ('I cannot smell but I can still smell normally', 'loss_of_smell', 'unknown', 'patient_or_unspecified'),
])
def test_sensory_sentence_forms_preserve_state_and_subject(text, code, state, subject):
    symptom = mapping(text)[code]
    assert symptom['status'] == state and symptom['reported_subject'] == subject


def test_covid_example_keeps_reduced_taste_intact_smell_and_local_timing():
    text = ('I have had a dry cough and fever for 3 days. '
            'Since yesterday, my sense of taste has reduced, but I can still smell normally.')
    symptoms = mapping(text)
    assert set(symptoms) == {'cough', 'fever', 'taste_change', 'loss_of_smell'}
    assert symptoms['fever']['duration_min_days'] == 3
    assert symptoms['taste_change']['status'] == 'present'
    assert symptoms['taste_change']['taste_detail'] == 'reduced'
    assert symptoms['taste_change']['duration_text'] == 'since yesterday'
    assert symptoms['taste_change']['duration_min_days'] is None
    assert symptoms['loss_of_smell']['status'] == 'absent'
    assert symptoms['loss_of_smell']['duration_text'] is None


def test_grammar_and_timing_words_are_not_flagged_as_unmatched_symptoms():
    text = ('I have had a dry cough and fever for 3 days. '
            'Since yesterday, my sense of taste has reduced, but I can still smell normally.')
    output = build_confirmation_output(text)
    remaining = ' '.join(item['unmatched_text'] for item in output['unmatched_wording']).lower()
    for residue in ['had dry', 'for 3 days', 'since yesterday', 'dry']:
        assert residue not in remaining
    assert not output['has_red_flag']
    # Genuinely unknown medical wording must still surface for review.
    assert any('rash' in item['unmatched_text'] for item in build_confirmation_output('fever and a rash')['unmatched_wording'])


def test_leading_sensory_timing_does_not_replace_an_explicit_duration():
    symptoms = mapping('Since yesterday, reduced taste for 2 days')
    assert symptoms['taste_change']['duration_min_days'] == 2


@pytest.mark.parametrize('text', ['my sense of taste has not reduced', 'my sense of taste is not reduced'])
def test_denied_taste_reduction_is_not_a_positive_fuzzy_match(text):
    symptom = mapping(text).get('taste_change')
    assert symptom is None or symptom['status'] != 'present'


@pytest.mark.parametrize('text', ['I cannot breathe', 'Ninakohoa damu na siwezi kupumua vizuri', 'blue lips and cough'])
def test_warning_is_independent_of_selection(text):
    confirmation = confirm_symptoms(text, [], None)
    assert confirmation['has_red_flag']
    assessment = assess(confirmation, 22, 'F')
    assert assessment['status'] == 'urgent_review'
    assert assessment['research_rankings'] == []


@pytest.mark.parametrize('text', ['I am not coughing up blood', 'My mother has blue lips', 'Sina shida ya kupumua'])
def test_no_invented_current_warning(text):
    assert not build_confirmation_output(text)['has_red_flag']


@pytest.mark.parametrize('text,days', [('cough for 2 days', 2), ('kikohozi kwa siku 5', 5), ('cough for three weeks', 21), ('cough for a few days', None), ('cough for two months', None)])
def test_duration_context(text, days):
    assert mapping(text)['cough']['duration_min_days'] == days


def test_precise_generic_evidence():
    symptoms = mapping('headache and chest pain and phlegm and tight chest')
    assert symptoms['headache']['features'] == ['E_53']
    assert symptoms['chest_pain']['features'] == ['E_53']
    assert symptoms['productive_cough']['features'] == []
    assert symptoms['wheezing']['features'] == []


@pytest.mark.parametrize('text', [
    'no appetite and blue lips',
    'not hungry and chest hurts while resting',
    "don't feel like eating and coughing up blood",
    'losing weight without trying and high pitched breathing sound',
    'sina hamu ya kula na nimekohoa damu',
])
def test_intrinsic_negative_phrases_do_not_hide_later_warnings(text):
    assert build_confirmation_output(text)['has_red_flag']


@pytest.mark.parametrize('text', [
    'chest hurts whlie resting',
    'chest hurts while retsing for 3 weeks',
    'chest pain even when retsing',
    'no apeptite and blue face',
    'not hugnry and confusion',
    'losing weight without triyng and blood in phlegm',
])
def test_qualifier_typos_and_intrinsic_negative_typos_preserve_safety(text):
    assert build_confirmation_output(text)['has_red_flag']


@pytest.mark.parametrize('text', [
    'I do not have chest hurts while retsing',
    'My mother has chest hurts whlie resting',
    'chest hurts while exercising',
])
def test_safety_typo_matching_does_not_invent_scope_or_rest_qualifier(text):
    assert not build_confirmation_output(text)['has_red_flag']


def test_swahili_productive_phrase_does_not_inherit_another_persons_cough():
    symptoms = mapping('nakohoa makohozi. Mama yangu ana kukohoa.')
    assert symptoms['cough']['reported_subject'] == 'other_person'
    assert symptoms['productive_cough']['status'] == 'present'
    assert symptoms['productive_cough']['features'] == []


@pytest.mark.parametrize('text,code', [
    ('kukohoa damu. Mama yangu ana kukohoa.', 'cough'),
    ('homa ya mafua. Mama yangu ana joto mwilini.', 'fever'),
    ('wet cough. My mother has cough.', 'cough'),
])
def test_longer_concepts_do_not_create_patient_mentions_from_other_person(text, code):
    symptom = mapping(text)[code]
    assert symptom['reported_subject'] == 'other_person'
    assert symptom['status'] == 'unknown'


def test_partial_evidence_and_duration_do_not_fabricate_scores():
    c = confirm_symptoms('fever and cough', ['fever', 'cough'], None)
    first = assess(c, 22, 'F', [{'code': 'cough', 'days': 2, 'text': '2 days'}])
    second = assess(c, 22, 'F', [{'code': 'cough', 'days': 21, 'text': '3 weeks'}])
    assert first['research_rankings'] == second['research_rankings']
    assert first['research_rankings'][0]['condition'] != 'Tuberculosis'
    assert first['status'] == 'insufficient_information'
    assert first['clinical_validation'] is False
    assert 'research_rankings' not in public_assessment(first)
    denied = confirm_symptoms('cough but no fever', ['cough'], None)
    assert 'E_91' in assess(denied, 22, 'F')['absent_features']


def test_corrections_and_duration_validation():
    c = confirm_symptoms('fever', ['fever'], None, [{'code': 'fever', 'status': 'absent', 'subject': 'patient_or_unspecified'}])
    assert not c['confirmed_codes']
    with pytest.raises(ValueError):
        assess(c, 22, 'F', [{'code': 'fever', 'days': 5}])
    with pytest.raises(ValueError):
        confirm_symptoms('fever', ['fever'], None, [{'code': 'invented', 'status': 'present', 'subject': 'patient_or_unspecified'}])


def test_selective_research_pattern_uses_evaluated_policy_not_personal_probabilities():
    text = 'fever and body aches and chills and sore throat'
    c = confirm_symptoms(text, list(mapping(text)), None)
    result = assess(c, 22, 'F')
    assert result['status'] == 'research_pattern'
    assert result['research_pattern']['display_name'] == 'FLU(influenza)'
    assert result['policy_id'] == 'ddxplus_selective_research_v1'
    assert result['clinical_validation'] is False
    public = public_assessment(result)
    assert 'research_rankings' not in public
    assert 'raw_relative_score' not in str(public)
    assert result['provenance']['model_sha256']
    assert result['provenance']['schema_sha256']
    assert result['provenance']['policy_sha256']
    assert result['provenance']['mapping']['catalogue_sha256']
    assert result['provenance']['guidance_sha256']


def test_context_or_unknown_wording_prevents_unqualified_named_research_result():
    text = 'fever and body aches and chills and sore throat and loss of taste'
    c = confirm_symptoms(text, list(mapping(text)), None)
    result = assess(c, 22, 'F')
    assert result['research_pattern'] is None
    assert 'unsupported_or_unreviewed_context' in result['uncertainty_reasons']


def test_tone_and_explicit_duration_do_not_change_the_disease_scores():
    simple = 'fever and body aches and chills and sore throat'
    verbose = 'I am really worried. Please help, I have fever and body aches and chills and sore throat for 3 days'
    results = []
    for text in [simple, verbose]:
        results.append(assess(confirm_symptoms(text, list(mapping(text)), None), 22, 'F'))
    assert results[0]['research_rankings'] == results[1]['research_rankings']
    assert results[0]['research_pattern']['condition'] == results[1]['research_pattern']['condition'] == 'Influenza'


def test_duration_units_and_unknown_preserve_original_context_without_changing_scores():
    c = confirm_symptoms('cough for 3 weeks and fever', ['cough', 'fever'], None)
    weeks = assess(c, 22, 'F', [{'code': 'cough', 'amount': 3, 'unit': 'weeks'}])
    months = assess(c, 22, 'F', [{'code': 'cough', 'amount': 1, 'unit': 'months'}])
    unknown = assess(c, 22, 'F', [{'code': 'cough', 'unknown': True}])
    w = next(s for s in weeks['observations'] if s['code'] == 'cough')
    m = next(s for s in months['observations'] if s['code'] == 'cough')
    u = next(s for s in unknown['observations'] if s['code'] == 'cough')
    assert w['duration_min_days'] == 21 and w['duration_source'] == 'patient_correction'
    assert m['duration_min_days'] is None and m['duration_approximate'] is True
    assert u['duration_unit'] == 'unknown' and u['duration_min_days'] is None
    assert u['extracted_duration_before_correction']['duration_min_days'] == 21
    assert weeks['research_rankings'] == months['research_rankings'] == unknown['research_rankings']
    assert any('persistent cough' in step for step in months['next_steps'])
    assert not any('persistent cough' in step for step in unknown['next_steps'])


def test_snapshot_preserves_correction_origin_and_catalogue_identity():
    c = confirm_symptoms('cough but no fever', ['cough', 'fever'], None,
        [{'code': 'fever', 'status': 'present', 'subject': 'patient_or_unspecified'}])
    result = assess(c, 22, 'F')
    fever = next(s for s in result['observations'] if s['code'] == 'fever')
    assert fever['original_status'] == 'absent' and fever['status'] == 'present'
    assert fever['corrected_by_patient'] is True
    assert result['provenance']['mapping'] == c['mapping_provenance']
    assert result['provenance']['medical_review_status'] == 'pending'


def test_shared_confirmation_does_not_mutate_original_mapping():
    original = build_confirmation_output('cough but no fever')
    result = confirm_mapping(original, ['cough', 'fever'], [{'code': 'fever', 'status': 'present', 'subject': 'patient_or_unspecified'}])
    assert next(s for s in original['recognised_symptoms'] if s['code'] == 'fever')['status'] == 'absent'
    assert next(s for s in result['recognised_symptoms'] if s['code'] == 'fever')['status'] == 'present'


@pytest.fixture(scope='module')
def release_api(test_database):
    with SessionLocal() as db:
        old = {r.id: r.is_active for r in db.scalars(select(ModelVersion)).all()}
        register(db, activate=True)
        headers = {}
        for name, role in [('owner', 'patient'), ('other', 'patient'), ('admin', 'admin')]:
            user = User(email=f'{name}-{uuid.uuid4().hex}@example.com', password_hash='test-only', role=role)
            db.add(user)
            db.flush()
            headers[name] = {'Authorization': 'Bearer ' + create_access_token(user_id=str(user.id), role=role)}
        db.commit()
    try:
        with TestClient(app) as client:
            yield client, headers
    finally:
        with SessionLocal() as db:
            for row in db.scalars(select(ModelVersion)).all():
                row.is_active = old.get(row.id, False)
            db.commit()


def test_release_status_and_authenticated_history_privacy(release_api):
    client, headers = release_api
    status = client.get('/model/status').json()
    assert status['model_version'] == MODEL_VERSION
    assert status['feature_count'] == 34 and status['class_count'] == 21
    assert not status['covid_prediction_supported']
    response = client.post('/predict', headers=headers['owner'], json={'age': 22, 'sex': 'F',
        'text': 'i have fever and headache for few days with dry cough', 'confirmed_codes': ['fever', 'headache', 'cough'],
        'durations': [{'code': 'cough', 'days': 3, 'text': 'about 3 days'}]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'insufficient_information'
    assert result['predictions'] == []
    assert 'research_rankings' not in result['assessment']
    pid = result['prediction_id']
    saved = client.get(f'/history/{pid}', headers=headers['owner']).json()
    assert saved['assessment'] == result['assessment']
    assert 'research_rankings' not in str(client.get('/history', headers=headers['owner']).json())
    assert client.get(f'/history/{pid}', headers=headers['other']).status_code == 404
    assert client.get(f'/admin/history/{pid}', headers=headers['owner']).status_code == 403
    admin = client.get(f'/admin/history/{pid}', headers=headers['admin']).json()
    assert len(admin['assessment']['research_rankings']) == 3
    assert admin['assessment']['present_features'] == ['E_201', 'E_53', 'E_91']


@pytest.mark.parametrize('result,expected', [('positive', 'positive COVID test'), ('negative', 'negative COVID test'), ('unknown', 'unsure of your COVID test result'), ('not_tested', 'Flu and COVID-19 can share symptoms')])
def test_covid_test_result_is_recorded_as_context_not_a_prediction(result, expected):
    c = confirm_symptoms('fever and cough', ['fever', 'cough'], None)
    assessment = assess(c, 22, 'F', covid_test={'result': result, 'test_type': 'pcr'})
    assert assessment['covid_test_context'] == {'result': result, 'test_type': 'pcr', 'used_by_model': False}
    assert assessment['covid_test_used_by_model'] is False
    assert assessment['covid_prediction_supported'] is False
    baseline = assess(c, 22, 'F')
    assert assessment['research_rankings'] == baseline['research_rankings']
    assert assessment['status'] == baseline['status']
    assert any(expected in step for step in assessment['next_steps'])
    public = public_assessment(assessment)
    assert public['covid_test_context']['result'] == result
    assert 'research_rankings' not in public


def test_covid_test_context_round_trips_through_api_and_history(release_api):
    client, headers = release_api
    response = client.post('/predict', headers=headers['owner'], json={
        'age': 22, 'sex': 'F', 'text': 'fever and cough', 'confirmed_codes': ['fever', 'cough'],
        'covid_test': {'result': 'positive', 'test_type': 'antigen'}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['assessment']['covid_test_context']['result'] == 'positive'
    assert result['assessment']['covid_test_used_by_model'] is False
    saved = client.get(f"/history/{result['prediction_id']}", headers=headers['owner']).json()
    assert saved['assessment']['covid_test_context']['result'] == 'positive'


def test_invalid_covid_test_is_rejected(release_api):
    client, headers = release_api
    for bad in [{'result': 'invented'}, {'result': 'positive', 'test_type': 'invented'}, {'result': 'not_tested', 'extra': 'x'}]:
        response = client.post('/predict', headers=headers['owner'], json={
            'age': 22, 'sex': 'F', 'text': 'fever', 'confirmed_codes': ['fever'], 'covid_test': bad})
        assert response.status_code == 422


@pytest.mark.parametrize('text,codes,expected', [
    ('I cannot taste anything', ['taste_change'], True),
    ('my sense of taste has reduced', ['taste_change'], True),
    ('I have loss of smell', ['loss_of_smell'], True),
    ('I can smell but cannot taste anything', ['taste_change'], True),
    ('sihisi ladha', ['taste_change'], True),
    ('I have not lost taste', ['taste_change'], False),
    ('My sister has loss of taste', ['taste_change'], False),
    ('I am not sure whether my sense of taste has reduced', ['taste_change'], False),
    ('I cannot taste anything', [], False),
    ('fever and cough', ['fever', 'cough'], False),
    ('I cannot taste anything and blue lips', ['taste_change', 'blue_lips'], False),
])
def test_covid_testing_advice_is_automatic_and_only_for_current_confirmed_sensory_reports(text, codes, expected):
    confirmation = confirm_symptoms(text, codes, None)
    assessment = assess(confirmation, 22, 'F')
    assert assessment['covid_testing_recommended'] is expected
    assert bool(assessment['covid_testing_advice']) is expected
    assert assessment['covid_test_context'] is None
    assert assessment['covid_prediction_supported'] is False
    assert assessment['taste_used_by_model'] is False
    if expected:
        assert 'one possible cause' in assessment['covid_testing_advice']
        assert 'Take a COVID-19 test' in assessment['covid_testing_advice']
        assert 'not a COVID-19 diagnosis' in assessment['covid_testing_advice']
        assert assessment['covid_testing_advice'] in assessment['next_steps']
    else:
        assert not any('COVID' in step for step in assessment['next_steps'])


@pytest.mark.parametrize('text,codes', [('I cannot taste anything', ['taste_change']), ('does not taste well', []), ('I feel unwell', [])])
def test_context_only_and_vague_input_never_forces_a_disease(release_api, text, codes):
    client, headers = release_api
    response = client.post('/predict', headers=headers['owner'], json={'age': 22, 'sex': 'F', 'text': text, 'confirmed_codes': codes})
    assert response.status_code == 200
    assert response.json()['status'] == 'unsupported_input'
    assert response.json()['predictions'] == []
    assert not response.json()['assessment']['taste_used_by_model']


def test_sensory_example_survives_sql_confirmation_prediction_and_history(release_api):
    client, headers = release_api
    text = ('I have had a dry cough and fever for 3 days. '
            'Since yesterday, my sense of taste has reduced, but I can still smell normally.')
    mapped = client.post('/nlp/map', json={'text': text}, headers=headers['owner'])
    assert mapped.status_code == 200
    symptoms = {s['code']: s for s in mapped.json()['recognised_symptoms']}
    assert symptoms['taste_change']['status'] == 'present'
    assert symptoms['loss_of_smell']['status'] == 'absent'
    codes = [code for code, s in symptoms.items() if s['status'] == 'present']
    confirmed = client.post('/symptoms/confirm', json={'text': text, 'confirmed_codes': codes}, headers=headers['owner'])
    assert confirmed.status_code == 200
    response = client.post('/predict', json={'text': text, 'age': 22, 'sex': 'F', 'confirmed_codes': codes}, headers=headers['owner'])
    assert response.status_code == 200
    result = response.json()
    observations = {s['code']: s for s in result['assessment']['observations']}
    assert result['assessment']['covid_testing_recommended'] is True
    assert 'Take a COVID-19 test' in result['assessment']['covid_testing_advice']
    assert observations['taste_change']['taste_detail'] == 'reduced'
    assert observations['taste_change']['duration_text'] == 'since yesterday'
    assert observations['loss_of_smell']['status'] == 'absent'
    assert result['assessment']['research_pattern'] is None
    assert not result['assessment']['covid_prediction_supported']
    assert result['assessment']['present_features'] == ['E_201', 'E_91']
    assert client.get(f"/history/{result['prediction_id']}", headers=headers['owner']).json()['assessment'] == result['assessment']


def test_safety_endpoint_survives_model_and_database_dependencies(release_api):
    client, _ = release_api
    with patch('app.main.get_active_model_version', side_effect=RuntimeError('unavailable')):
        response = client.post('/safety/check', json={'text': 'Ninakohoa damu na siwezi kupumua vizuri'})
    assert response.status_code == 200 and response.json()['has_red_flag']
    assert 'Seek urgent' in response.json()['health_guidance']


def test_mapping_keeps_warning_guidance_when_database_mapping_fails(release_api):
    client, _ = release_api
    from app.nlp.symptom_mapper import build_confirmation_output as original

    def unavailable(text, db=None):
        if db is not None:
            raise OperationalError('test-only', {}, RuntimeError('database unavailable'))
        return original(text)

    with patch('app.main.build_confirmation_output', side_effect=unavailable):
        response = client.post('/nlp/map', json={'text': 'chest hurts whlie resting'})
    assert response.status_code == 200
    assert response.json()['has_red_flag'] is True


def test_corrupt_model_fails_closed_but_safety_endpoint_still_works(release_api, tmp_path):
    client, headers = release_api
    invalid = tmp_path / 'invalid.json'
    invalid.write_text('{}', encoding='utf-8')
    with patch('app.services.awareness.MODEL_PATH', invalid):
        response = client.post('/predict', headers=headers['owner'], json={
            'age': 22, 'sex': 'F', 'text': 'fever and cough', 'confirmed_codes': ['fever', 'cough']})
        safety = client.post('/safety/check', json={'text': 'blue lips'})
    assert response.status_code == 503
    assert 'no alternative was substituted' in response.json()['detail']
    assert safety.status_code == 200 and safety.json()['has_red_flag']


def test_invalid_fields_and_missing_authentication(release_api):
    client, headers = release_api
    payload = {'age': 22, 'sex': 'F', 'text': 'cough', 'confirmed_codes': ['cough']}
    assert client.post('/predict', json=payload).status_code == 401
    bad = {**payload, 'durations': [{'code': 'cough', 'days': -1}]}
    assert client.post('/predict', json=bad, headers=headers['owner']).status_code == 422
    bad = {**payload, 'durations': [{'code': 'invented', 'days': 2}]}
    assert client.post('/predict', json=bad, headers=headers['owner']).status_code == 400
    bad = {**payload, 'corrections': [{'code': 'cough', 'status': 'invented', 'subject': 'patient_or_unspecified'}]}
    assert client.post('/predict', json=bad, headers=headers['owner']).status_code == 422


def test_startup_without_database_schema_keeps_static_safety_available():
    with patch('app.main.check_schema', side_effect=SchemaNotReadyError('test schema unavailable')) as schema:
        with TestClient(app) as client:
            response = client.get('/health')
            assert response.status_code == 200 and response.json()['database_ready'] is False
            assert client.post('/safety/check', json={'text': 'I cannot breathe'}).json()['has_red_flag']
            schema.assert_not_called()
            assert client.get('/readiness').status_code == 503
            assert client.post('/auth/login', json={'email': 'person@example.com', 'password': 'test-only'}).status_code == 503


def test_fresh_server_import_does_not_require_legacy_model_or_condition_metadata():
    script = """
from pathlib import Path
from app.ml import model_loader
def unavailable():
    raise AssertionError('Static safety attempted to load the legacy model')
model_loader.load_model_bundle = unavailable
from app.services import health_guidance
health_guidance.CONDITION_INFO_PATH = Path('intentionally-missing-condition-metadata.json')
from app.main import app
from fastapi.testclient import TestClient
with TestClient(app) as client:
    assert client.get('/health').status_code == 200
    assert client.post('/safety/check', json={'text': 'I cannot breathe'}).json()['has_red_flag']
"""
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr


def test_api_rejects_conflicting_duration_units(release_api):
    client, headers = release_api
    payload = {'age': 22, 'sex': 'F', 'text': 'cough', 'confirmed_codes': ['cough'],
               'durations': [{'code': 'cough', 'days': 3, 'amount': 2, 'unit': 'weeks'}]}
    assert client.post('/predict', json=payload, headers=headers['owner']).status_code == 422


def test_legacy_v2_status_reports_its_actual_bundle_not_v1_dimensions(release_api):
    from types import SimpleNamespace
    from app.ml.model_loader import V2_MODEL_VERSION
    client, _ = release_api
    with patch('app.main.get_active_model_version', return_value=SimpleNamespace(version=V2_MODEL_VERSION)):
        response = client.get('/model/status')
    assert response.status_code == 200
    assert response.json()['feature_count'] == 66
    assert response.json()['class_count'] == 23
    assert response.json()['clinical_validation'] is False


def test_unavailable_legacy_v2_status_does_not_report_v1_as_loaded(release_api):
    from types import SimpleNamespace
    from app.ml.model_loader import V2_MODEL_VERSION
    client, _ = release_api
    with patch('app.main.get_active_model_version', return_value=SimpleNamespace(version=V2_MODEL_VERSION)), \
         patch('app.main.get_v2_bundle', side_effect=FileNotFoundError('missing test artifact')):
        response = client.get('/model/status')
    assert response.status_code == 503
    assert 'no V1 model was substituted' in response.json()['detail']


def test_readiness_recovers_when_the_real_test_schema_is_available(release_api):
    client, _ = release_api
    app.state.schema_ready = False
    response = client.get('/readiness')
    assert response.status_code == 200 and response.json()['status'] == 'ready'
    assert response.json()['model_version'] == MODEL_VERSION
