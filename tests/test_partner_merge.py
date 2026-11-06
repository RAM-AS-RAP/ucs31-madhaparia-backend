"""Merged partner features and the actual symptom/output contract, isolated SQL only."""
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.db.models import DiseaseInformation, PredictionSession
from app.nlp.symptom_mapper import build_confirmation_output
from app.services.awareness import assess, digest_json
from app.services.symptom_confirmation import confirm_mapping
from tests.test_awareness_release import release_api


CASES = json.loads(Path(__file__).with_name('fixtures').joinpath('awareness_prompt_cases.json').read_text(encoding='utf-8'))


def evaluation(text, codes=None):
    mapping = build_confirmation_output(text)
    if codes is None:
        codes = [s['code'] for s in mapping['recognised_symptoms']
                 if s['status'] == 'present' and s['reported_subject'] != 'other_person']
    return assess(confirm_mapping(mapping, codes), 22, 'F')


@pytest.mark.parametrize('case', CASES, ids=lambda c: str(c['row']) + '-' + c['condition'])
def test_21_prompts_through_merged_api_and_owner_history(release_api, case):
    client, headers = release_api
    mapped = client.post('/nlp/map', json={'text': case['prompt']})
    assert mapped.status_code == 200, mapped.text
    codes = [s['code'] for s in mapped.json()['recognised_symptoms']
             if s['status'] == 'present' and s['reported_subject'] != 'other_person']
    response = client.post('/predict', headers=headers['owner'], json={
        'text': case['prompt'], 'age': case['age'], 'sex': case['sex'], 'confirmed_codes': codes,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == case['status']
    assert result['predictions'] == []
    assessment = result['assessment']
    assert 'research_rankings' not in assessment
    if case['status'] != 'urgent_review':
        assert (assessment['research_pattern'] or {}).get('display_name') == case['display_name']
        assert assessment['covid_testing_recommended'] == case['covid_testing_recommended']
        assert assessment['present_features'] == case['present_features']
        assert assessment['absent_features'] == case['absent_features']
    saved = client.get('/history/' + result['prediction_id'], headers=headers['owner'])
    assert saved.json()['assessment'] == assessment


def test_nlp_separate_and_vague_durations():
    result = evaluation('I have a cough for 3 weeks. I have fever for 2 days.')
    observations = {s['code']: s for s in result['observations']}
    assert observations['cough']['duration_min_days'] == 21
    assert observations['fever']['duration_min_days'] == 2
    vague = evaluation('I have a cough for a few days.')['observations'][0]
    assert vague['duration_text'] == 'for a few days'
    assert vague['duration_min_days'] is None


def test_nlp_duration_negation_and_covid_combination():
    result = evaluation('I have a cough for 3 days. I do not have fever. I can smell but cannot taste anything.')
    observations = {s['code']: s for s in result['observations']}
    assert observations['cough']['duration_min_days'] == 3
    assert observations['fever']['status'] == 'absent'
    assert observations['loss_of_smell']['status'] == 'absent'
    assert observations['taste_change']['status'] == 'present'
    assert result['covid_testing_recommended'] is True
    assert result['covid_prediction_supported'] is False
    assert result['taste_used_by_model'] is False


@pytest.mark.parametrize('text,codes', [
    ('I have a cough for 3 days, but I do not have fever.', None),
    ('I have a cough. I have not lost taste. I have not lost my sense of smell.', None),
    ('I am not sure whether my sense of taste has reduced.', None),
    ('My sister has loss of taste. I have a cough.', None),
    ('I cannot taste anything', []),
])
def test_covid_negative_scope_and_deselection_cases(text, codes):
    assert evaluation(text, codes)['covid_testing_recommended'] is False


def test_extracted_duration_does_not_change_rankings():
    short = evaluation('I have a cough for 2 days and fever.')
    long = evaluation('I have a cough for 3 weeks and fever.')
    assert short['research_rankings'] == long['research_rankings']
    assert not any('persistent cough' in s for s in short['next_steps'])
    assert any('persistent cough' in s for s in long['next_steps'])


def test_admin_system_recognises_v3_and_reserves_runtime_aliases(release_api):
    client, headers = release_api
    response = client.get('/admin/system/model', headers=headers['admin'])
    assert response.status_code == 200, response.text
    assert response.json()['state'] == 'ready'
    assert response.json()['model']['version'] == 'v3_partial_evidence'
    evidence = response.json()['model']['selection_evaluation']
    assert evidence['policy_id'] == 'ddxplus_selective_research_v1'
    assert evidence['test']['up_to_3']['accepted_rows'] == 13911
    assert 'not clinical accuracy' in evidence['scope']
    assert client.get('/admin/system/model', headers=headers['owner']).status_code == 403
    symptoms = client.get('/admin/system/symptoms', params={'q': 'fever'}, headers=headers['admin']).json()['items']
    fever = next(s for s in symptoms if s['code'] == 'fever')
    conflict = client.post(f"/admin/system/symptoms/{fever['id']}/aliases", headers=headers['admin'], json={
        'alias': 'cannot taste anything', 'language': 'en', 'expected_revision': fever['revision'],
    })
    assert conflict.status_code == 409


def test_managed_descriptions_apply_to_new_v3_snapshots_only(release_api):
    client, headers = release_api
    payload = {'text': 'I have fever, body aches, chills and a sore throat.',
               'age': 22, 'sex': 'F', 'confirmed_codes': ['fever', 'muscle_aches', 'chills', 'sore_throat']}
    before = client.post('/predict', headers=headers['owner'], json=payload).json()
    with SessionLocal() as db:
        row = db.get(DiseaseInformation, 'Influenza')
        original = row.display_name, row.short_description
        row.display_name = 'Admin-managed influenza name'
        row.short_description = 'An edited research description for the isolated merge test.'
        db.commit()
    try:
        after = client.post('/predict', headers=headers['owner'], json=payload).json()
        assessment = after['assessment']
        assert assessment['research_pattern']['display_name'] == 'FLU(influenza)'
        assert assessment['research_pattern']['description'].startswith('An edited research description')
        assert assessment['provenance']['managed_pattern_sha256'] == digest_json(assessment['research_pattern'])
        assert client.get('/history/' + before['prediction_id'], headers=headers['owner']).json()['assessment'] == before['assessment']
    finally:
        with SessionLocal() as db:
            row = db.get(DiseaseInformation, 'Influenza')
            row.display_name, row.short_description = original
            db.commit()


def test_admin_diagnostics_include_v3_unsupported_context(release_api):
    client, headers = release_api
    result = client.post('/predict', headers=headers['owner'], json={
        'text': 'fever and cough and loss of taste', 'age': 22, 'sex': 'F',
        'confirmed_codes': ['fever', 'cough', 'taste_change'],
    }).json()
    response = client.get('/admin/system/nlp', params={'source': 'assessments'}, headers=headers['admin'])
    assert response.status_code == 200, response.text
    saved = next(row for row in response.json()['items'] if row['prediction_id'] == result['prediction_id'])
    assert saved['unsupported_symptoms'] == ['taste_change']


def test_corrected_warning_survives_confirmation_and_independent_save(release_api):
    client, headers = release_api
    payload = {'text': 'I do not have blue lips', 'confirmed_codes': ['blue_lips'],
               'corrections': [{'code': 'blue_lips', 'status': 'present', 'subject': 'patient_or_unspecified'}]}
    confirmation = client.post('/symptoms/confirm', json=payload)
    assert confirmation.status_code == 200
    assert confirmation.json()['has_red_flag'] is True
    saved = client.post('/safety/reviews', json=payload, headers=headers['owner'])
    assert saved.status_code == 200, saved.text
    assert saved.json()['status'] == 'urgent_review'
    assert saved.json()['assessment']['prediction_performed'] is False


@pytest.mark.parametrize('change,status', [
    ({'durations': [{'code': 'cough', 'amount': -1}]}, 422),
    ({'durations': [{'code': 'cough', 'amount': 1.5}]}, 422),
    ({'durations': [{'code': 'cough', 'amount': True}]}, 422),
    ({'durations': [{'code': 'cough', 'amount': 522, 'unit': 'weeks'}]}, 422),
    ({'durations': [{'code': 'cough', 'amount': 121, 'unit': 'months'}]}, 422),
    ({'durations': [{'code': 'cough', 'days': 3, 'amount': 3}]}, 422),
    ({'durations': [{'code': 'cough', 'unknown': True, 'days': 3}]}, 422),
    ({'durations': [{'code': 'cough', 'days': 3}, {'code': 'cough', 'days': 4}]}, 400),
    ({'durations': [{'code': 'invented', 'days': 3}]}, 400),
    ({'durations': [{'code': 'fever', 'days': 3}]}, 400),
    ({'corrections': [{'code': 'invented', 'status': 'present', 'subject': 'patient_or_unspecified'}]}, 400),
    ({'age': True}, 422),
    ({'sex': 'invented'}, 422),
    ({'predictions': [{'condition': 'forced diagnosis'}]}, 422),
    ({'language': 'invented'}, 422),
])
def test_invalid_v3_requests_never_write_history(release_api, change, status):
    client, headers = release_api
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(PredictionSession))
    result = client.post('/predict', headers=headers['owner'], json={
        'age': 22, 'sex': 'F', 'text': 'I have a cough but no fever',
        'confirmed_codes': ['cough'], **change,
    })
    assert result.status_code == status, result.text
    assert 'prediction_id' not in result.json()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PredictionSession)) == before
