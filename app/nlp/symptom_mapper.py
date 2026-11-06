"""
Map English, Swahili and mixed-language symptom text
to canonical respiratory symptom codes.

This module uses the validated English and Swahili aliases
stored in the NLP folder.
"""

import json
import hashlib
import re
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ServiceNotReadyError
from app.db.models import Symptom, SymptomAlias

from rapidfuzz import fuzz, process
from app.nlp.reported_context import EXTRA_SYMPTOMS, aliases as context_aliases, report_context, precise_features, qualifying_safety_matches


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAPPER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CONTEXT_RULES_SHA256 = hashlib.sha256(Path(__file__).with_name('reported_context.py').read_bytes()).hexdigest()

ENGLISH_ALIASES_PATH = (
    PROJECT_ROOT
    / "nlp"
    / "english_aliases.json"
)

SWAHILI_ALIASES_PATH = (
    PROJECT_ROOT
    / "nlp"
    / "swahili_aliases.json"
)

CANONICAL_SYMPTOMS_PATH = (
    PROJECT_ROOT
    / "nlp"
    / "canonical_symptoms.json"
)

FUZZY_THRESHOLD = 90
SUGGESTION_THRESHOLD = 70

FILLER_WORDS = {
    "i",
    "im",
    "i'm",
    "have",
    "has",
    "having",
    "had",
    "a",
    "an",
    "the",
    "and",
    "but",
    "or",
    "my",
    "me",
    "am",
    "is",
    "are",
    "feel",
    "feeling",
    "with",
    "also",
    "for",
    "since",
    "days",
    "day",
    "weeks",
    "week",
    "months",
    "month",
    "yesterday",
    "today",
    "few",
    "some",
    "now",
    "still",
    "dry",
    "wet",
    "no",
    "not",
    "never",
    "without",
    "don't",
    "dont",
    "doesn't",
    "didn't",
    "was",
    "were",
    "been",
    "being",
    "did",
    "does",
    "do",
    "while",
    "whether",
    "sure",
    "can",
    "cannot",
    "can't",
    "could",
    "should",
    "would",
    "nina",
    "niko",
    "na",
    "lakini",
    "kuwa",
    "sana",
    "siku",
    "wiki",
    "miezi",
    "jana",
    "asubuhi",
    "tangu",
    "kwa",
    "sina",
    "hana",
    "hakuna",
}

def load_json(path):
    with open(
        path,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def normalize_text(text):
    """
    Convert text to lowercase and normalize whitespace.
    """

    text = text.lower().strip()

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text


def build_alias_index(
    db: Session | None = None,
):
    """
    Build the searchable English and Swahili alias index.

    The API uses the persistent database catalogue.
    JSON files remain available for standalone use.
    """

    alias_index = []

    if db is not None:
        rows = db.execute(
            select(
                SymptomAlias.alias,
                Symptom.code,
                SymptomAlias.language,
            ).join(
                Symptom,
                SymptomAlias.symptom_id
                == Symptom.id,
            )
        ).all()

        for row in rows:
            alias_index.append(
                {
                    "alias": normalize_text(
                        row.alias
                    ),
                    "code": row.code,
                    "language": row.language,
                }
            )

    else:
        english_aliases = load_json(
            ENGLISH_ALIASES_PATH
        )

        swahili_aliases = load_json(
            SWAHILI_ALIASES_PATH
        )

        for code, aliases in (
            english_aliases.items()
        ):
            for alias in aliases:
                alias_index.append(
                    {
                        "alias": normalize_text(
                            alias
                        ),
                        "code": code,
                        "language": "en",
                    }
                )

        for code, aliases in (
            swahili_aliases.items()
        ):
            for alias in aliases:
                alias_index.append(
                    {
                        "alias": normalize_text(
                            alias
                        ),
                        "code": code,
                        "language": "sw",
                    }
                )

    existing = {(a['code'], a['alias']) for a in alias_index}
    alias_index.extend(a for a in context_aliases() if (a['code'], a['alias']) not in existing)
    alias_index.sort(
        key=lambda item: len(
            item["alias"]
        ),
        reverse=True,
    )

    return alias_index


def find_fuzzy_matches(
    normalized_text,
    alias_index,
    matched_codes,
    occupied_spans,
):
    """
    Find misspelled or near-matching symptom phrases.

    Fuzzy matching is only used after exact alias matching.
    This reduces the chance of replacing a correct exact match
    with a weaker approximate match.
    """

    fuzzy_matches = []

    words = list(
        re.finditer(
            r"\b[\w']+\b",
            normalized_text,
        )
    )

    for item in alias_index:
        code = item["code"]

        if code in matched_codes:
            continue

        alias = item["alias"]

        alias_word_count = len(
            alias.split()
        )

        if alias_word_count == 0:
            continue

        if len(words) < alias_word_count:
            continue

        best_score = 0
        best_candidate = None
        best_span = None

        for start_index in range(
            len(words)
            - alias_word_count
            + 1
        ):
            end_index = (
                start_index
                + alias_word_count
                - 1
            )

            start = words[
                start_index
            ].start()

            end = words[
                end_index
            ].end()

            overlaps = any(
                start < existing_end
                and end > existing_start
                for (
                    existing_start,
                    existing_end,
                )
                in occupied_spans
            )

            if overlaps:
                continue

            candidate = (
                normalized_text[
                    start:end
                ]
            )

            score = fuzz.ratio(
                candidate,
                alias,
            )

            if score > best_score:
                best_score = score
                best_candidate = candidate
                best_span = (
                    start,
                    end,
                )

        is_close_transposition = (
            best_candidate is not None
            and len(best_candidate) == len(alias)
            and len(alias) >= 5
            and best_candidate[:2] == alias[:2]
            and best_score >= 80
        )

        if (
            best_candidate is not None
            and (
                best_score >= FUZZY_THRESHOLD
                or is_close_transposition
            )
        ):
            fuzzy_matches.append(
                {
                    "code": code,
                    "matched_alias": (
                        best_candidate
                    ),
                    "canonical_alias": alias,
                    "language": item[
                        "language"
                    ],
                    "match_type": "fuzzy",
                    "score": round(
                        best_score,
                        1,
                    ),
                    "_span": best_span,
                }
            )

            matched_codes.add(code)

            occupied_spans.append(
                best_span
            )

    return fuzzy_matches

def find_symptoms(
    text,
    db: Session | None = None,
):
    """
    Find canonical symptoms in English, Swahili,
    or mixed-language free text.

    Exact matching is attempted first.
    Fuzzy matching is then used for remaining
    misspelled or near-matching symptom phrases.
    """

    normalized_text = normalize_text(
        text
    )

    alias_index = build_alias_index(
        db
    )

    matches = []
    matched_codes = set()
    occupied_spans = []

    # -------------------------
    # Exact matching
    # -------------------------

    for item in alias_index:
        alias = item["alias"]

        pattern = (
            r"(?<!\w)"
            + re.escape(alias)
            + r"(?!\w)"
        )

        for match in re.finditer(
            pattern,
            normalized_text,
        ):
            start, end = match.span()

            overlaps = any(
                start < existing_end
                and end > existing_start
                for (
                    existing_start,
                    existing_end,
                )
                in occupied_spans
            )

            if overlaps:
                continue

            code = item["code"]

            if code in matched_codes:
                continue

            matches.append(
                {
                    "code": code,
                    "matched_alias": alias,
                    "canonical_alias": alias,
                    "language": item[
                        "language"
                    ],
                    "match_type": "exact",
                    "score": 100.0,
                }
            )

            matched_codes.add(
                code
            )

            occupied_spans.append(
                (
                    start,
                    end,
                )
            )

    # -------------------------
    # Fuzzy matching
    # -------------------------

    # A long, slightly misspelled safety phrase must not be hidden by its
    # shorter exact generic-pain match.
    for specific in qualifying_safety_matches(normalized_text, alias_index, matched_codes):
        occupied_spans.append(specific.pop('_span'))
        matched_codes.add(specific['code'])
        matches.append(specific)

    fuzzy_matches = (
        find_fuzzy_matches(
            normalized_text,
            alias_index,
            matched_codes,
            occupied_spans,
        )
    )

    for match in fuzzy_matches:
        match.pop(
            "_span",
            None,
        )

    matches.extend(
        fuzzy_matches
    )

    return matches

def find_unmatched_text(
    text,
    matches,
):
    """
    Return meaningful words or phrases that were not
    recognised as supported symptoms.
    """

    remaining_text = normalize_text(
        text
    )

    # Remove phrases that were already recognised.
    for match in matches:
        matched_alias = match[
            "matched_alias"
        ]

        pattern = (
            r"(?<!\w)"
            + re.escape(
                matched_alias
            )
            + r"(?!\w)"
        )

        remaining_text = re.sub(
            pattern,
            " ",
            remaining_text,
        )

    # Treat common English/Swahili connectors as separators.
    remaining_text = re.sub(
        r"\b(and|but|or|na|lakini)\b",
        ",",
        remaining_text,
    )

    segments = re.split(
        r"[,;.!?]+",
        remaining_text,
    )

    unmatched = []

    for segment in segments:
        words = re.findall(
            r"\b[\w']+\b",
            segment,
        )

        meaningful_words = [
            word
            for word in words
            if word not in FILLER_WORDS
            and not word.isdigit()
        ]

        if not meaningful_words:
            continue

        phrase = " ".join(
            meaningful_words
        )

        if phrase not in unmatched:
            unmatched.append(
                phrase
            )

    return unmatched

def suggest_symptoms(
    unmatched_phrases,
    alias_index,
    limit=3,
):
    """
    Suggest supported symptoms for unmatched wording.

    Suggestions are for user review only and are not
    automatically treated as confirmed symptoms.
    """

    suggestions = []

    alias_lookup = {
        item["alias"]: item
        for item in alias_index
    }

    alias_choices = list(
        alias_lookup.keys()
    )

    for phrase in unmatched_phrases:
        results = process.extract(
            phrase,
            alias_choices,
            scorer=fuzz.ratio,
            limit=limit,
        )

        phrase_suggestions = []

        seen_codes = set()

        for (
            alias,
            score,
            _,
        ) in results:

            if score < SUGGESTION_THRESHOLD:
                continue

            item = alias_lookup[
                alias
            ]

            code = item["code"]

            if code in seen_codes:
                continue

            phrase_suggestions.append(
                {
                    "code": code,
                    "suggested_alias": alias,
                    "language": item[
                        "language"
                    ],
                    "score": round(
                        score,
                        1,
                    ),
                }
            )

            seen_codes.add(
                code
            )

        suggestions.append(
            {
                "unmatched_text": phrase,
                "suggestions": (
                    phrase_suggestions
                ),
            }
        )

    return suggestions

def build_confirmation_output(
    text,
    db: Session | None = None,
):
    """
    Build the structured output that can be sent to the
    frontend for symptom review and confirmation.
    """

    if db is not None:
        catalogue_is_empty = (
            db.scalar(
                select(SymptomAlias.id).limit(1)
            )
            is None
        )

        if catalogue_is_empty:
            raise ServiceNotReadyError(
                "The symptom catalogue has not been "
                "loaded into the database. "
                "Run: python -m app.db.seed_symptom_catalog"
            )

    matches = find_symptoms(
        text,
        db,
    )

    alias_index = build_alias_index(
        db
    )

    unmatched_phrases = (
        find_unmatched_text(
            text,
            matches,
        )
    )

    suggestions = (
        suggest_symptoms(
            unmatched_phrases,
            alias_index,
        )
    )

    if db is not None:
        database_symptoms = db.scalars(
            select(Symptom)
        ).all()

        canonical_index = {
            symptom.code: {
                "code": symptom.code,
                "display_name": (
                    symptom.display_name
                ),
                "features": symptom.features,
                "kind": symptom.kind,
                "is_red_flag": (
                    symptom.is_red_flag
                ),
            }
            for symptom in database_symptoms
        }

    else:
        canonical_symptoms = load_json(
            CANONICAL_SYMPTOMS_PATH
        )

        canonical_index = {
            entry["code"]: entry
            for entry in canonical_symptoms
        }

    canonical_index.update(EXTRA_SYMPTOMS)
    recognised_symptoms = []

    for match in matches:
        code = match["code"]

        if code not in canonical_index:
            raise KeyError(
                f"Canonical symptom not found: "
                f"{code}"
            )

        symptom = canonical_index[
            code
        ]

        recognised_symptoms.append(
            {
                "code": code,
                "display_name": symptom[
                    "display_name"
                ],
                "features": symptom[
                    "features"
                ],
                "kind": symptom[
                    "kind"
                ],
                "is_red_flag": symptom[
                    "is_red_flag"
                ],
                "matched_text": match[
                    "matched_alias"
                ],
                "language": match[
                    "language"
                ],
                "match_type": match[
                    "match_type"
                ],
                "match_score": match[
                    "score"
                ],
                "confirmed": False,
            }
        )

    for symptom in recognised_symptoms:
        symptom.update(report_context(text, symptom['matched_text'], symptom['code'], alias_index, matches))
        symptom['features'] = precise_features(symptom)
        symptom['context_only'] = not bool(symptom['features'])

    has_red_flag = any(
        symptom["is_red_flag"]
        and symptom['status'] != 'absent'
        and symptom['reported_subject'] != 'other_person'
        for symptom
        in recognised_symptoms
    )

    catalogue = {
        'symptoms': [{key: value.get(key) for key in ['code', 'display_name', 'features', 'kind', 'is_red_flag']}
                     for _, value in sorted(canonical_index.items())],
        'aliases': sorted(alias_index, key=lambda a: (a['code'], a['language'], a['alias'])),
    }
    provenance = {
        'mapping_id': 'awareness_context_v2',
        'catalogue_sha256': hashlib.sha256(json.dumps(catalogue, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest(),
        'mapper_sha256': MAPPER_SHA256,
        'context_rules_sha256': CONTEXT_RULES_SHA256,
    }

    return {
        "input_text": text,
        "recognised_symptoms": (
            recognised_symptoms
        ),
        "unmatched_wording": suggestions,
        "has_red_flag": has_red_flag,
        "mapping_version": 'awareness_context_v2',
        'mapping_provenance': provenance,
        "requires_confirmation": (
            bool(recognised_symptoms)
            or bool(suggestions)
        ),
    }

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print(
            'python nlp/symptom_mapper.py '
            '"I have fever and a sore throat"'
        )
        return

    text = " ".join(
        sys.argv[1:]
    )

    matches = find_symptoms(
        text
    )

    alias_index = build_alias_index()

    unmatched_phrases = (
        find_unmatched_text(
            text,
            matches,
        )
    )

    suggestions = (
        suggest_symptoms(
            unmatched_phrases,
            alias_index,
        )
    )

    print()
    print(
        "Input:",
        text,
    )

    print()
    print(
        "Recognised symptoms:"
    )

    if matches:
        for match in matches:
            print(
                "-",
                match["code"],
                "|",
                match["matched_alias"],
                "|",
                match["language"],
                "|",
                match["match_type"],
                "|",
                match["score"],
            )
    else:
        print(
            "No supported symptoms found."
        )

    print()
    print(
        "Unmatched wording:"
    )

    if not suggestions:
        print(
            "None."
        )

    for item in suggestions:
        print(
            "-",
            item["unmatched_text"],
        )

        if not item["suggestions"]:
            print(
                "  No close supported "
                "symptom suggestion."
            )
            continue

        print(
            "  Suggestions:"
        )

        for suggestion in (
            item["suggestions"]
        ):
            print(
                "  -",
                suggestion["code"],
                "|",
                suggestion[
                    "suggested_alias"
                ],
                "|",
                suggestion["language"],
                "|",
                suggestion["score"],
            )


if __name__ == "__main__":
    main()
