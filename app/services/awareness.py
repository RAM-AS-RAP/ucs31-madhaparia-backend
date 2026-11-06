"""Versioned awareness assessment; research scores are never patient diagnoses."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from app.core.errors import ServiceNotReadyError
from app.ml.v3_predictor import MODEL_PATH, MODEL_VERSION, load_v3_bundle, predict_v3
from app.nlp.reported_context import has_unreviewed_wording

MODEL_SHA256 = "ae709ef5ddf6dd7a101fe426b610c3e83ef58aa93cb895ca13e7500220dc889c"
POLICY_ID = "awareness_no_unvalidated_names_v1"
SCHEMA_ID = 'ddxplus_native34_partial_v1'
POLICY_PATH = MODEL_PATH.parent / 'research_selection_policy.json'
CONDITION_PATH = Path(__file__).resolve().parents[1] / 'data/condition_information.json'
WARNING_FEATURES = {'E_45', 'E_66', 'E_194', 'E_190', 'E_14'}
ASSESSMENT_CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PREDICTOR_CODE_SHA256 = hashlib.sha256(MODEL_PATH.parent.parent.joinpath('v3_predictor.py').read_bytes()).hexdigest()


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def selection_policy():
    if not POLICY_PATH.exists():
        return {'policy_id': POLICY_ID, 'mode': 'withhold_all', 'sha256': None}
    try:
        raw = POLICY_PATH.read_bytes()
        policy = json.loads(raw)
        if policy['model_sha256'] != MODEL_SHA256 or policy['clinical_validation'] is not False or policy['dataset'] != 'DDXPlus-synthetic':
            raise ValueError('Policy identity does not match the research model')
        if policy['mode'] not in {'withhold_all', 'selective_research'}:
            raise ValueError('Unknown research policy mode')
        if policy['mode'] == 'selective_research':
            if not math.isfinite(policy['min_score']) or not math.isfinite(policy['min_margin']) or not 0 < policy['min_score'] < 1 or not 0 <= policy['min_margin'] < 1 or policy['minimum_positive_features'] < 2:
                raise ValueError('Invalid research selection thresholds')
            for split in ['validation', 'test']:
                for view in ['up_to_3', 'up_to_3_plus_3_negative', 'full_supported']:
                    evidence = policy[split][view]
                    if type(evidence['accepted_profile_groups']) is not int or evidence['accepted_profile_groups'] < 100 or not math.isfinite(evidence['group_accuracy_wilson_lower_95']) or not 0.90 <= evidence['group_accuracy_wilson_lower_95'] <= 1:
                        raise ValueError('Research policy has not passed its recorded source gates')
        return {**policy, 'sha256': hashlib.sha256(raw).hexdigest()}
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ServiceNotReadyError('Research selection policy is unavailable or incompatible; no permissive threshold was substituted.') from error


def normalize_duration(value):
    amount, unit, days = value.get('amount'), value.get('unit', 'days'), value.get('days')
    if unit not in {'days', 'weeks', 'months'}:
        raise ValueError('Unsupported duration unit.')
    if value.get('unknown'):
        if amount is not None or days is not None:
            raise ValueError('Unknown duration must not carry a numeric value.')
        return {'duration_text': value.get('text') or 'Not sure', 'duration_min_days': None, 'duration_max_days': None,
                'duration_amount': None, 'duration_unit': 'unknown', 'duration_source': 'patient_correction'}
    if amount is not None:
        if days is not None or type(amount) is not int or not 0 <= amount <= {'days': 3650, 'weeks': 521, 'months': 120}[unit]:
            raise ValueError('Invalid or conflicting duration values.')
        exact_days = amount * (7 if unit == 'weeks' else 1) if unit != 'months' else None
    else:
        if days is not None and (type(days) is not int or not 0 <= days <= 3650 or unit != 'days'):
            raise ValueError('Invalid legacy duration in days.')
        amount, exact_days = days, days
    text = value.get('text') or (f'{amount} {unit}' if amount is not None else None)
    if not text:
        raise ValueError('Supply duration information or explicitly mark it unknown.')
    return {'duration_text': text, 'duration_min_days': exact_days, 'duration_max_days': exact_days,
            'duration_amount': amount, 'duration_unit': unit if amount is not None else 'unknown',
            'duration_approximate': unit == 'months', 'duration_source': 'patient_correction'}


def normalize_covid_test(value):
    if value is None:
        return None
    result, test_type = value.get('result'), value.get('test_type')
    if result not in {'positive', 'negative', 'unknown', 'not_tested'}:
        raise ValueError('Invalid COVID test result.')
    if test_type not in {None, 'pcr', 'antigen', 'other', 'unknown'}:
        raise ValueError('Invalid COVID test type.')
    return {'result': result, 'test_type': test_type, 'used_by_model': False}


def bundle():
    try:
        if hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError('Research artifact differs from its recorded release')
        return load_v3_bundle()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ServiceNotReadyError('The partial-evidence research model is unavailable; no alternative was substituted.') from error


def public_assessment(assessment):
    result = deepcopy(assessment)
    if isinstance(result, dict):
        result.pop('research_rankings', None)
    return result


def assess(confirmation, age, sex, durations=None, language='en', covid_test=None):
    covid_test = normalize_covid_test(covid_test)
    symptoms = confirmation['recognised_symptoms']
    states = {s['code']: s.get('status', 'present') for s in symptoms}
    for s in symptoms:
        if s['reported_subject'] == 'other_person' or (s['status'] == 'present' and not s['confirmed']):
            states[s['code']] = 'unknown'
    context = []
    for s in symptoms:
        context.append({k: s.get(k) for k in ['code', 'display_name', 'matched_text', 'status', 'reported_subject', 'confirmed',
            'duration_text', 'duration_min_days', 'duration_max_days', 'taste_detail', 'context_only',
            'original_status', 'original_subject', 'corrected_by_patient']})
        context[-1]['duration_source'] = 'report_text' if s.get('duration_text') else 'unknown'
    by_code = {s['code']: s for s in context}
    seen = set()
    for value in durations or []:
        code = value['code']
        if code in seen or code not in by_code or states[code] != 'present':
            raise ValueError('Duration must refer to a unique, current, confirmed patient symptom.')
        seen.add(code)
        by_code[code]['extracted_duration_before_correction'] = {k: by_code[code].get(k) for k in ['duration_text', 'duration_min_days', 'duration_max_days']}
        by_code[code].update(normalize_duration(value))
    urgent = confirmation['has_red_flag']
    covid_testing_recommended = not urgent and any(
        s['code'] in {'taste_change', 'loss_of_smell'} and states[s['code']] == 'present'
        for s in symptoms)
    present, absent = set(), set()
    rankings = []
    pattern = None
    data = None
    policy = {'policy_id': 'independent_safety_v2', 'sha256': None, 'mode': 'safety'}
    status = 'urgent_review' if urgent else 'insufficient_information'
    reasons = ['safety_warning'] if urgent else ['research_model_not_clinically_validated', 'named_results_withheld']
    if not urgent:
        data = bundle()
        policy = selection_policy()
        supported = set(data['features'])
        for s in symptoms:
            if s['reported_subject'] == 'other_person':
                continue
            features = set(s['features']) & supported
            if states[s['code']] == 'present':
                present.update(features)
            elif states[s['code']] == 'absent':
                absent.update(features)
        # Shared parent features (e.g. pain) cannot be both positive and negative.
        absent -= present
        if present:
            prediction = predict_v3(sorted(present), sorted(absent), age, sex, top_k=3)
            rankings = prediction['predictions']
            unknown_wording = has_unreviewed_wording(confirmation.get('unmatched_wording', []))
            unsupported_context = any(s['confirmed'] and s.get('context_only') for s in symptoms)
            native_positive_count = sum('_@_' not in f for f in present)
            margin = rankings[0]['raw_relative_score'] - rankings[1]['raw_relative_score']
            if (policy['mode'] == 'selective_research' and not unknown_wording and not unsupported_context and not present & WARNING_FEATURES
                    and native_positive_count >= policy['minimum_positive_features']
                    and rankings[0]['raw_relative_score'] >= policy['min_score'] and margin >= policy['min_margin']):
                information = json.loads(CONDITION_PATH.read_bytes())[rankings[0]['condition']]
                pattern = {'condition': rankings[0]['condition'], 'display_name': information['display_name'],
                           'description': information['short_description'], 'meaning': 'A tentative simulated-data research pattern, not a diagnosis'}
                status = 'research_pattern'
                reasons = ['research_model_not_clinically_validated']
            elif unknown_wording or unsupported_context:
                reasons.append('unsupported_or_unreviewed_context')
        else:
            status = 'unsupported_input'
            reasons.append('no_supported_present_features')
    sw = language == 'sw'
    messages = {
        'urgent_review': ('Seek urgent medical assessment for the reported warning signs. If symptoms are severe or worsening rapidly, contact local emergency services.',
                          'Tafuta huduma ya afya haraka kwa dalili za hatari. Zikiwa kali au zinazidi haraka, wasiliana na huduma za dharura za eneo lako.'),
        'insufficient_information': ('These symptoms do not reliably identify one cause. The research model is not clinically validated, so no disease name or personal disease probability is presented.',
                                     'Dalili hizi hazitambui sababu moja kwa uhakika. Modeli ya utafiti haijathibitishwa kitabibu, kwa hiyo hatutoi jina la ugonjwa wala uwezekano wako wa kuugua.'),
        'unsupported_input': ('The reported information is not sufficient for a supported model assessment. Context has been recorded, not converted into invented symptoms.',
                              'Taarifa ulizotoa hazitoshi kwa tathmini inayoungwa mkono na modeli. Muktadha umehifadhiwa bila kubuni dalili.'),
        'research_pattern': ('The reported supported evidence met a selection rule evaluated on simulated source records. This is a tentative research comparison, not confirmation that you have this condition.',
                             'Taarifa zinazoungwa mkono zimetimiza kanuni iliyotathminiwa kwa rekodi za kutengenezwa. Huu ni ulinganisho wa utafiti tu, si uthibitisho kuwa una ugonjwa huu.'),
    }
    advice = [] if urgent else [
        'Seek medical assessment if symptoms persist, worsen or concern you.' if not sw else 'Tafuta tathmini ya kitabibu ikiwa dalili zinaendelea, zinazidi au zinakutia wasiwasi.',
        'Seek urgent care for severe breathing difficulty, blue lips, confusion or coughing blood.' if not sw else 'Tafuta huduma haraka kwa shida kali ya kupumua, midomo kuwa bluu, kuchanganyikiwa au kukohoa damu.',
    ]
    if any(states[s['code']] == 'present' and s['code'] == 'cough' and (
            (s.get('duration_min_days') is not None and s['duration_min_days'] >= 14)
            or (s.get('duration_unit') == 'months' and (s.get('duration_amount') or 0) >= 1)) for s in context):
        advice.insert(0, 'A persistent cough warrants clinical assessment, which may include TB testing. Duration alone does not diagnose or exclude TB.' if not sw else 'Kikohozi cha muda mrefu kinahitaji tathmini ya kitabibu, ambayo inaweza kujumuisha kipimo cha TB. Muda pekee hautambui wala kuondoa TB.')
    if not urgent and covid_test:
        covid_advice = {
            'positive': ('You reported a positive COVID test. That result is recorded as reported context; this model did not predict it. Flu and other respiratory infections can still overlap, so clinical assessment matters.'
                         if not sw else 'Uliripoti kipimo chako cha COVID kuwa chanya. Matokeo hayo yamehifadhiwa kama taarifa, si utabiri wa modeli. Mafua na maambukizi mengine ya kupumua bado yanaweza kuingiliana, kwa hiyo tathmini ya kitabibu ni muhimu.'),
            'negative': ('You reported a negative COVID test. That result is recorded as reported context; a negative test does not rule out flu or another respiratory infection.'
                         if not sw else 'Uliripoti kipimo chako cha COVID kuwa hasi. Matokeo hayo yamehifadhiwa kama taarifa; kipimo hasi hakuondoi mafua au maambukizi mengine ya kupumua.'),
            'unknown': ('You are unsure of your COVID test result. Testing may help distinguish COVID from flu; this model does not predict COVID.'
                        if not sw else 'Huna uhakika wa matokeo ya kipimo chako cha COVID. Kipimo kinaweza kusaidia kutofautisha COVID na mafua; modeli hii haitabiri COVID.'),
            'not_tested': ('Flu and COVID-19 can share symptoms. Taste or smell changes do not establish COVID; testing may be needed. This model does not predict COVID.'
                           if not sw else 'Mafua ya influenza na COVID-19 yanaweza kuwa na dalili zinazofanana. Mabadiliko ya ladha au harufu hayathibitishi COVID; kipimo kinaweza kuhitajika. Modeli hii haitabiri COVID.'),
        }
        advice.append(covid_advice.get((covid_test or {}).get('result', 'not_tested'), covid_advice['not_tested']))
    covid_testing_advice = None
    if covid_testing_recommended:
        covid_testing_advice = (
            'COVID-19 is one possible cause of your reported taste or smell change. Take a COVID-19 test and seek medical advice if symptoms persist or worsen. Other conditions can cause these changes too; this is a testing recommendation, not a COVID-19 diagnosis.'
            if not sw else
            'COVID-19 ni mojawapo ya sababu zinazowezekana za mabadiliko ya ladha au harufu uliyoripoti. Pima COVID-19 na utafute ushauri wa kitabibu ikiwa dalili zinaendelea au zinazidi. Hali nyingine pia zinaweza kusababisha mabadiliko haya; huu ni ushauri wa kupima, si utambuzi wa COVID-19.')
        advice.append(covid_testing_advice)
    limits = 'Simulated-data research, not a diagnosis. Duration and taste are context-only. Unmentioned evidence remains unknown.' if not sw else 'Utafiti wa data zilizotengenezwa, si utambuzi wa ugonjwa. Muda na ladha ni muktadha tu. Dalili zisizotajwa hubaki hazijulikani.'
    provenance = {'model_sha256': MODEL_SHA256 if data else None, 'schema_id': SCHEMA_ID if data else None,
        'assessment_code_sha256': ASSESSMENT_CODE_SHA256, 'predictor_code_sha256': PREDICTOR_CODE_SHA256 if data else None,
        'schema_sha256': digest_json({'features': data['features'], 'classes': data['classes']}) if data else None,
        'mapping': deepcopy(confirmation.get('mapping_provenance')), 'policy_sha256': policy['sha256'],
        'guidance_version': 'awareness_guidance_v2', 'guidance_sha256': digest_json({'summary': messages[status][1 if sw else 0], 'next_steps': advice, 'limitations': limits, 'pattern': pattern}),
        'condition_information_sha256': hashlib.sha256(CONDITION_PATH.read_bytes()).hexdigest(),
        'medical_review_status': 'pending', 'language_review_status': 'pending',
        'sources': ['https://www.cdc.gov/flu/about/index.html', 'https://www.cdc.gov/covid/signs-symptoms/index.html', 'https://www.who.int/news-room/fact-sheets/detail/tuberculosis']}
    return {
        'contract_version': 2, 'status': status, 'model_version': None if urgent else MODEL_VERSION,
        'policy_id': policy['policy_id'], 'policy_mode': policy['mode'], 'clinical_validation': False, 'language': language,
        'summary': messages[status][1 if sw else 0], 'next_steps': advice,
        'observations': context, 'confirmed_codes': confirmation['confirmed_codes'],
        'unmatched_wording': deepcopy(confirmation.get('unmatched_wording', [])),
        'has_unmatched_wording': has_unreviewed_wording(confirmation.get('unmatched_wording', [])),
        'unsupported_symptoms': [s['code'] for s in symptoms if s['confirmed'] and s.get('context_only')],
        'present_features': sorted(present), 'absent_features': sorted(absent),
        'duration_used_by_model': False, 'taste_used_by_model': False, 'covid_prediction_supported': False,
        'covid_test_context': covid_test, 'covid_test_used_by_model': False,
        'covid_testing_recommended': covid_testing_recommended, 'covid_testing_advice': covid_testing_advice,
        'uncertainty_reasons': reasons, 'research_rankings': rankings,
        'research_pattern': pattern, 'provenance': provenance,
        'limitations': limits,
        'flu_scope': 'FLU(influenza) is one class. Seasonal describes circulation; the common cold is different. Influenza types/subtypes are not identified by this model.' if not sw else 'FLU(influenza) ni darasa moja. Mafua ya kawaida si influenza. Modeli hii haitambui aina ndogo za virusi vya influenza.',
    }
