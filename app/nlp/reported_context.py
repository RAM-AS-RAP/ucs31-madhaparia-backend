"""Patient-report context; never infer a diagnosis from a phrase.

Clause/state handling adapts the existing local ML mapper's approach. New
English/Swahili coverage is software-tested, not clinically/linguistically validated.
"""
import re
from rapidfuzz import fuzz


EXTRA_SYMPTOMS = {
    "taste_change": {"code": "taste_change", "display_name": "Loss, reduction or change of taste", "features": [], "kind": "context", "is_red_flag": False},
    "sneezing": {"code": "sneezing", "display_name": "Sneezing", "features": [], "kind": "context", "is_red_flag": False},
}

EXTRA_ALIASES = {
    "en": {
        "taste_change": ["loss of taste", "lost taste", "lost my taste", "lost sense of taste", "cannot taste", "can't taste", "can't taste anything", "cannot taste anything", "reduced taste", "sense of taste has reduced", "sense of taste is reduced", "taste has reduced", "sense of taste has decreased", "food has less taste", "cannot taste as well", "food tastes bland", "metallic taste", "everything tastes different", "does not taste well"],
        "sneezing": ["sneezing", "sneeze"],
        "shortness_of_breath": ["struggling to breathe", "unable to breathe", "cannot catch my breath"],
        "loss_of_smell": ["can smell normally", "can smell", "can still smell normally", "can still smell"],
    },
    "sw": {
        "taste_change": ["sihisi ladha", "ladha imepungua", "ladha imebadilika", "sipati ladha"],
        "loss_of_smell": ["sihisi harufu", "sipati harufu"],
        "cough": ["ninakohoa", "nimekuwa nikikohoa", "nakohoa"],
        "coughing_blood": ["ninakohoa damu", "nimekohoa damu", "nakohoa damu"],
        "fever": ["mwili wangu una joto"],
        "headache": ["kichwa kinaniuma"],
        "sore_throat": ["koo linaniuma", "maumivu ya koo"],
        "runny_blocked_nose": ["pua inatiririka", "pua imeziba"],
        "tiredness": ["nimechoka"],
        "shortness_of_breath": ["napumua kwa shida", "siwezi kupumua vizuri", "siwezi kupumua", "ninashindwa kupumua"],
        "blue_lips": ["midomo yangu imekuwa bluu", "midomo imegeuka bluu", "midomo yangu imegeuka bluu"],
        "muscle_aches": ["misuli inauma", "mwili unauma"],
        "chills": ["ninahisi baridi", "ninatetemeka"],
        "weight_loss": ["nimepungua uzito bila kutaka"],
        "loss_of_appetite": ["sina hamu ya kula"],
        "productive_cough": ["nakohoa makohozi"],
        "heavy_sweating": ["ninatokwa na jasho usiku"],
        "chest_pain": ["kifua kinaniuma"],
        "sneezing": ["kupiga chafya", "ninapiga chafya"],
    },
}

CLAUSES = r"[.!?;]|\b(?:but|however|lakini)\b|\b(?:and|na)\s+(?=(?:i|my|am|nina|sina|mama|baba)\b)"
OTHER = r"\b(?:my\s+(?:mother|father|sister|brother|child|son|daughter|friend|wife|husband|mom|dad)|(?:mama|baba|dada|kaka|mtoto)\s+yangu)\b"
PATIENT = r"\b(?:i|me|myself|nina|sina|ninakohoa|nimekuwa|ninahisi)\b"
REVIEWED_NARRATIVE_WORDS = set("""i me my myself am is are have having feel feeling and but or with also no not without
    do don't dont sure unsure whether maybe perhaps please help doctor really very worried scared anxious angry damn
    just probably nothing important understand these current symptoms for since a an few some one two three four five
    six seven eight nine ten days day weeks week months month yesterday today morning
    nina niko na lakini sana naomba msaada nimekuwa dalili zangu ni wasiwasi labda sijui sina uhakika kama
    kwa siku chache wiki miezi tangu jana asubuhi moja mbili tatu nne tano""".split())


def has_unreviewed_wording(unmatched):
    # Ignore only explicitly reviewed communication/time words, not unknown
    # medical content such as rash, pregnancy, exposure or test results.
    return any(word not in REVIEWED_NARRATIVE_WORDS and not word.isdigit()
               for item in unmatched for word in re.findall(r"\b[\w']+\b", item['unmatched_text'].lower()))


def aliases():
    return [{"alias": alias, "code": code, "language": language}
            for language, concepts in EXTRA_ALIASES.items()
            for code, words in concepts.items() for alias in words]


def _state(fragment, start, end, intrinsic_phrases=()):
    before, after = fragment[:start], fragment[end:]
    # These negatives are part of a positive concept, not denial of later symptoms.
    before = re.sub(r"\b(?:no appetite|not hungry|don'?t feel like eating|sina hamu ya kula|without trying)\b", ' ', before)
    for phrase in intrinsic_phrases:
        before = re.sub(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', ' ', before)
    others = list(re.finditer(OTHER, before))
    patients = list(re.finditer(PATIENT, before))
    if others and (not patients or others[-1].start() > patients[-1].start()):
        return "unknown", "other_person"
    if re.search(r"\b(?:i had|nilikuwa na)\b", before):
        return ("absent" if re.search(r"\b(?:not now|not anymore|no longer|sasa sina)\b", after)
                else "present" if re.search(r"\bstill\b", after) else "unknown"), "patient_or_unspecified"
    if re.search(r"\b(?:not sure|unsure|don'?t know|do not know|maybe|perhaps|sijui|sina uhakika|labda)\b", before):
        return "unknown", "patient_or_unspecified"
    negations = list(re.finditer(r"\b(?:no|not|never|without|don'?t|do not|doesn'?t|didn'?t|sina|hana|hakuna|sipigi)\b", before))
    if negations and not re.search(r"\b(?:i have|i am|nina)\b", before[negations[-1].end():]):
        return "absent", "patient_or_unspecified"
    return "present", "patient_or_unspecified"


def report_context(text, matched_text, code, alias_index, matches=()):
    normalized = text.lower().replace("’", "'")
    words = {matched_text.lower()} | {a["alias"] for a in alias_index if a["code"] == code}
    # Use the recognised phrase, including its typo, rather than leaking its
    # intrinsic 'no/not/without' into the state of a later symptom.
    intrinsic_phrases = [m['matched_alias'] for m in matches
                         if m['code'] in {'loss_of_appetite', 'weight_loss'}
                         and re.search(r"\b(?:no|not|without|don't|sina)\b", m['canonical_alias'])]
    mentions = []
    for fragment in re.split(CLAUSES, normalized):
        occupied = []
        for alias in sorted(words, key=len, reverse=True):
            for occurrence in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", fragment):
                start, end = occurrence.span()
                if any(start < right and end > left for left, right in occupied):
                    continue
                # A cough inside 'coughing blood' or fever inside 'homa ya mafua'
                # is not a separate mention when that longer concept owns the span.
                owned_by_other = False
                for other in alias_index:
                    phrase = other['alias']
                    if other['code'] == code or len(phrase) <= len(alias) or alias not in phrase:
                        continue
                    left = fragment.find(phrase, max(0, start - len(phrase) + 1))
                    if left >= 0 and left <= start and left + len(phrase) >= end:
                        owned_by_other = True
                        break
                if owned_by_other:
                    continue
                occupied.append((start, end))
                state, subject = _state(fragment, start, end, intrinsic_phrases)
                if code == 'loss_of_smell' and alias in EXTRA_ALIASES['en']['loss_of_smell'] and state == 'present':
                    state = 'absent'
                mentions.append((state, subject, fragment[end:], fragment[:start]))
    patient = [m for m in mentions if m[1] != "other_person"]
    states = {m[0] for m in patient}
    state = states.pop() if len(states) == 1 else "unknown"
    subject = "other_person" if mentions and not patient else "patient_or_unspecified"
    if len(patient) == 1 and state == 'unknown' and re.search(r'\bi had\b', normalized) and re.search(r'\b(?:but not now|not anymore|no longer)\b', normalized):
        state = 'absent'
    # A leading onset stays within its clause; it never becomes an invented day count.
    durations = [extract_duration(m[2]) or (extract_duration(m[3]) if re.match(r'\s*(?:since yesterday|tangu jana)\b', m[3]) else None)
                 for m in patient if m[0] == "present"]
    durations = [d for d in durations if d]
    duration = durations[0] if durations and all(d == durations[0] for d in durations) else None
    result = {"status": state, "reported_subject": subject,
              "duration_text": duration["text"] if duration and state == "present" else None,
              "duration_min_days": duration["days"] if duration and state == "present" else None,
              "duration_max_days": duration["days"] if duration and state == "present" else None}
    if code == "taste_change":
        phrase = matched_text.lower()
        result["taste_detail"] = ("unclear" if "does not taste well" in phrase else "altered" if any(w in phrase for w in ["metallic", "different", "imebadilika"])
                                   else "reduced" if any(w in phrase for w in ["less", "reduced", "decreased", "as well", "bland", "imepungua"]) else "loss")
        if result["taste_detail"] == "unclear":
            result["status"] = "unknown"
    return result


def extract_duration(text):
    local = re.split(r"[,;.!?]|\b(?:and|but|lakini|na)\b", text.lower(), maxsplit=1)[0][:100]
    numbers = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
               "eight": 8, "nine": 9, "ten": 10, "moja": 1, "mbili": 2, "tatu": 3, "nne": 4, "tano": 5}
    quantity = r"\d+|" + "|".join(numbers)
    match = re.search(r"\b(?:for\s+)?(" + quantity + r")\s+(days?|weeks?|months?)\b|\b(?:kwa\s+)?(siku|wiki|miezi)\s+(" + quantity + r")\b", local)
    if match:
        value, unit = (match[1], match[2]) if match[1] else (match[4], match[3])
        days = (int(value) if value.isdigit() else numbers[value]) * (7 if unit.startswith("week") or unit == "wiki" else 30 if unit.startswith("month") or unit == "miezi" else 1)
        # Months are approximate context, not an asserted exact day count.
        return {"text": match[0], "days": days if 0 < days <= 3650 and not (unit.startswith("month") or unit == "miezi") else None}
    vague = re.search(r"\b(?:for (?:a )?few days|for some days|kwa siku chache|since yesterday|tangu jana)\b", local)
    return {"text": vague[0], "days": None} if vague else None


def precise_features(symptom):
    code, phrase = symptom["code"], symptom["matched_text"].lower()
    if code in {"headache", "chest_pain"}:
        return ["E_53"]
    if code == "wheezing" and phrase in {"tight chest", "kifua kubana"}:
        return []
    if code == "productive_cough" and phrase in {"phlegm", "mucus", "makohozi", "nakohoa makohozi", "wet cough", "productive cough", "coughing up phlegm", "coughing up mucus"}:
        return []
    return symptom["features"]


def qualifying_safety_matches(text, alias_index, matched_codes):
    """Keep a misspelled rest qualifier from being hidden by exact generic pain.

    Only one adjacent-letter transposition is accepted; qualifiers are not
    inferred from unrelated words or a lowered general fuzzy threshold.
    """
    if 'chest_pain' not in matched_codes or 'chest_pain_at_rest' in matched_codes:
        return []
    tokens = list(re.finditer(r"\b[\w']+\b", text))
    for item in alias_index:
        if item['code'] != 'chest_pain_at_rest':
            continue
        expected = item['alias'].split()
        for start in range(len(tokens) - len(expected) + 1):
            window = tokens[start:start + len(expected)]
            candidate = text[window[0].start():window[-1].end()]
            if candidate.split() != [t[0] for t in window]:
                continue
            changed = [(actual, wanted) for actual, wanted in zip(candidate.split(), expected) if actual != wanted]
            if len(changed) != 1:
                continue
            actual, wanted = changed[0]
            if len(actual) != len(wanted):
                continue
            different = [i for i, (a, b) in enumerate(zip(actual, wanted)) if a != b]
            if len(different) != 2 or different[1] != different[0] + 1:
                continue
            i, j = different
            if actual[i] != wanted[j] or actual[j] != wanted[i]:
                continue
            return [{'code': item['code'], 'matched_alias': candidate, 'canonical_alias': item['alias'],
                     'language': item['language'], 'match_type': 'fuzzy',
                     'score': round(fuzz.ratio(candidate, item['alias']), 1),
                     '_span': (window[0].start(), window[-1].end())}]
    return []
