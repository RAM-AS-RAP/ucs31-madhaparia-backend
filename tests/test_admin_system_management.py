import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main as main
from app.core.database import SessionLocal
from app.core.security import create_access_token, hash_password
from app.db.models import (AdminAccountAction, AdminSystemActivity, DiseaseInformation,
    Feedback, ModelVersion, NlpMappingEvent, PredictionSession, Symptom, SymptomAlias, User)
from app.nlp.symptom_mapper import build_confirmation_output
from app.services.system_management import apply_disease_information, record_mapping_event

client = TestClient(main.app)
BASE = "/admin/system"


@pytest.fixture(scope="module")
def identities(test_database):
    with SessionLocal() as db:
        password = hash_password("SyntheticSystemPass123!")
        users = [User(email=f"system-{uuid.uuid4().hex}@example.com", password_hash=password, role=role) for role in ("admin", "patient")]
        db.add_all(users)
        db.commit()
        return [{"id": user.id, "headers": {"Authorization": "Bearer " + create_access_token(str(user.id), user.role)}} for user in users]


def get(path, admin, **params):
    return client.get(BASE + path, headers=admin["headers"], params=params)


def symptom(admin):
    return next(row for row in get("/symptoms", admin, q="fever").json()["items"] if row["code"] == "fever")


@pytest.mark.parametrize("identity", ["guest", "patient", "forged_admin"])
def test_every_read_and_write_requires_a_real_administrator(identities, identity):
    admin, patient = identities
    headers = {} if identity == "guest" else patient["headers"]
    if identity == "forged_admin":
        headers = {"Authorization": "Bearer " + create_access_token(str(patient["id"]), "admin")}
    target = uuid.uuid4()
    requests = [("GET", path, None) for path in ("/model", "/symptoms", "/diseases", "/feedback", "/nlp", "/activity")]
    requests += [("PATCH", f"/symptoms/{target}", {"expected_revision": 0, "review_note": "test"}),
        ("POST", f"/symptoms/{target}/aliases", {"expected_revision": 0, "alias": "testword", "language": "en"}),
        ("DELETE", f"/symptoms/{target}/aliases/{target}", {"expected_revision": 0}),
        ("PATCH", "/diseases?condition=URTI", {"expected_revision": 0, "display_name": "Example", "short_description": "Example description"}),
        ("PATCH", f"/feedback/{target}", {"expected_revision": 0, "review_note": "", "review_status": "reviewed"})]
    for method, path, body in requests:
        response = client.request(method, BASE + path, headers=headers, **({"json": body} if body else {}))
        assert response.status_code == (401 if identity == "guest" else 403), (path, response.text)


def test_model_uses_active_registry_and_reports_configuration_failures(identities):
    admin, _ = identities
    with SessionLocal() as db:
        active = db.scalars(select(ModelVersion).where(ModelVersion.is_active.is_(True))).all()
        assert len(active) == 1
        record = active[0]
        original = record.is_active
        try:
            result = get("/model", admin)
            assert result.status_code == 200
            assert result.headers["cache-control"] == "no-store"
            assert result.json()["model"]["version"] == record.version
            assert result.json()["model"]["test_metrics"] == record.test_metrics
            record.is_active = False
            db.commit()
            assert get("/model", admin).json() == {"state": "no_active_model", "model": None}
        finally:
            record.is_active = original
            db.commit()


def test_model_reports_runtime_failure_without_raw_exception_details(identities):
    admin, _ = identities
    with SessionLocal() as db:
        record = db.scalar(select(ModelVersion).where(ModelVersion.is_active.is_(True)))
        original = record.artifact_filename
        try:
            record.artifact_filename = "private-inconsistent-artifact"
            db.commit()
            result = get("/model", admin)
            assert result.status_code == 200
            assert result.json()["state"] == "runtime_unavailable"
            assert "private-inconsistent" not in result.text
        finally:
            record.artifact_filename = original
            db.commit()


def test_symptom_notes_preserve_frozen_metadata_and_reject_stale_writes(identities):
    admin, _ = identities
    row = symptom(admin)
    payload = {"expected_revision": row["revision"], "review_note": "Reviewed bilingual wording."}
    response = client.patch(BASE + f"/symptoms/{row['id']}", headers=admin["headers"], json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["review_note"] == payload["review_note"]
    assert response.json()["features"] == row["features"]
    assert response.json()["is_red_flag"] == row["is_red_flag"]
    assert client.patch(BASE + f"/symptoms/{row['id']}", headers=admin["headers"], json=payload).status_code == 409
    for field, value in [("code", "other"), ("features", []), ("is_red_flag", False), ("display_name", "changed")]:
        assert client.patch(BASE + f"/symptoms/{row['id']}", headers=admin["headers"], json={**payload, field: value}).status_code == 422


def test_custom_alias_updates_live_mapping_and_removal_is_audited(identities):
    admin, _ = identities
    row = symptom(admin)
    word = "pyro" + uuid.uuid4().hex[:12]
    response = client.post(BASE + f"/symptoms/{row['id']}/aliases", headers=admin["headers"], json={"alias": f"  {word.upper()}  ", "language": "sw", "expected_revision": row["revision"]})
    assert response.status_code == 201, response.text
    updated = response.json()
    custom = next(item for item in updated["aliases"] if item["alias"] == word)
    assert custom["protected"] is False
    with SessionLocal() as db:
        output = build_confirmation_output(word, db)
        assert "fever" in [item["code"] for item in output["recognised_symptoms"]]
    result = client.request("DELETE", BASE + f"/symptoms/{row['id']}/aliases/{custom['id']}", headers=admin["headers"], json={"expected_revision": updated["revision"]})
    assert result.status_code == 200, result.text
    assert word not in [item["alias"] for item in result.json()["aliases"]]
    with SessionLocal() as db:
        assert db.scalar(select(AdminSystemActivity).where(AdminSystemActivity.target_id == "fever", AdminSystemActivity.action == "alias_removed"))


def test_alias_validation_baseline_protection_and_global_collision(identities):
    admin, _ = identities
    row = symptom(admin)
    path = BASE + f"/symptoms/{row['id']}/aliases"
    for value in ("not fever", "sina homa", "<script>", "12", "\u0000evil"):
        assert client.post(path, headers=admin["headers"], json={"alias": value, "language": "en", "expected_revision": row["revision"]}).status_code == 422
    assert client.post(path, headers=admin["headers"], json={"alias": "cough", "language": "sw", "expected_revision": row["revision"]}).status_code == 409
    baseline = next(item for item in row["aliases"] if item["protected"])
    result = client.request("DELETE", path + f"/{baseline['id']}", headers=admin["headers"], json={"expected_revision": row["revision"]})
    assert result.status_code == 403


def test_alias_race_only_one_identical_revision_can_succeed(identities):
    admin, _ = identities
    row = symptom(admin)
    def insert(index):
        return client.post(BASE + f"/symptoms/{row['id']}/aliases", headers=admin["headers"], json={"alias": "race" + uuid.uuid4().hex[:10], "language": "en", "expected_revision": row["revision"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(insert, [1, 2])) == [201, 409]


def test_disease_edits_apply_to_new_predictions_only_and_validate_fields(identities):
    admin, _ = identities
    with SessionLocal() as db:
        original = db.get(DiseaseInformation, "URTI")
        saved = (original.display_name, original.short_description)
        revision = original.revision
    payload = {"display_name": "Updated URTI label", "short_description": "Updated awareness information for this test.", "expected_revision": revision}
    try:
        result = client.patch(BASE + "/diseases", params={"condition": "URTI"}, headers=admin["headers"], json=payload)
        assert result.status_code == 200, result.text
        predictions = [{"condition": "URTI", "display_name": "Old title", "description": "Original description", "rank": 1}]
        with SessionLocal() as db:
            updated = apply_disease_information(predictions, db)
        assert updated[0]["display_name"] == payload["display_name"]
        assert predictions[0]["display_name"] == "Old title"
        assert client.patch(BASE + "/diseases", params={"condition": "URTI"}, headers=admin["headers"], json=payload).status_code == 409
        assert client.patch(BASE + "/diseases", params={"condition": "URTI"}, headers=admin["headers"], json={**payload, "short_description": " " * 12}).status_code == 422
    finally:
        with SessionLocal() as db:
            original = db.get(DiseaseInformation, "URTI")
            original.display_name, original.short_description = saved
            db.commit()


@pytest.fixture
def feedback_record(identities):
    _, patient = identities
    with SessionLocal() as db:
        session = PredictionSession(user_id=patient["id"], age=30, sex="F", input_text="Synthetic feedback fixture", confirmed_codes=[], model_features=[], status="prediction_complete", has_red_flag=False, predictions=[])
        db.add(session); db.flush()
        row = Feedback(prediction_session_id=session.id, was_helpful=False, comment="Synthetic review comment")
        db.add(row); db.commit()
        return {"id": str(row.id), "prediction_id": str(session.id), "revision": row.revision}


def test_feedback_review_filters_and_original_comment_are_preserved(identities, feedback_record):
    admin, _ = identities
    row = feedback_record
    path = BASE + f"/feedback/{row['id']}"
    payload = {"review_status": "resolved", "review_note": "Reviewed the wording issue.", "expected_revision": row["revision"]}
    result = client.patch(path, headers=admin["headers"], json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["comment"] == "Synthetic review comment"
    assert result.json()["reviewed_by"] == str(admin["id"])
    assert result.json()["reviewed_at"]
    assert client.patch(path, headers=admin["headers"], json=payload).status_code == 409
    records = get("/feedback", admin, q="Synthetic review", status="resolved", helpful="false").json()
    assert any(item["id"] == row["id"] for item in records["items"])
    assert not get("/feedback", admin, q="Synthetic review", helpful="true").json()["items"]
    assert get("/feedback", admin, q="%nonexistent%literal%").json()["total"] == 0


def test_nlp_event_recording_has_no_patient_text_or_exception_details(identities):
    admin, _ = identities
    record_mapping_event({"recognised_symptoms": [], "unmatched_wording": [{"unmatched_text": "private symptom text"}]})
    record_mapping_event()
    records = get("/nlp", admin, source="events", kind="mapping_error").json()
    assert records["total"] >= 1
    assert all(row["kind"] == "mapping_error" for row in records["items"])
    assert "private symptom text" not in str(records)
    assert set(records["items"][0]) == {"id", "kind", "created_at", "recognised_count", "unmatched_count"}


def test_nlp_saved_assessments_keep_unsupported_context(identities):
    admin, patient = identities
    with SessionLocal() as db:
        row = PredictionSession(user_id=patient["id"], age=30, sex="M", input_text="Synthetic unsupported wording", confirmed_codes=[], model_features=[], status="unsupported_input", has_red_flag=False, predictions=[], assessment={"unsupported_symptoms": ["custom_issue"], "unmatched_wording": [{"unmatched_text": "fixture phrase"}], "has_unmatched_wording": True})
        db.add(row); db.commit()
        target = str(row.id)
    result = get("/nlp", admin, source="assessments")
    assert result.status_code == 200, result.text
    saved = next(item for item in result.json()["items"] if item["id"] == target)
    assert saved["unsupported_symptoms"] == ["custom_issue"]
    assert saved["unmatched_wording"][0]["unmatched_text"] == "fixture phrase"


def test_activity_includes_previous_account_actions_and_does_not_log_private_notes(identities, feedback_record):
    admin, patient = identities
    with SessionLocal() as db:
        db.add(AdminAccountAction(actor_user_id=admin["id"], target_user_id=patient["id"], action="block"))
        db.commit()
    result = get("/activity", admin)
    assert result.status_code == 200
    assert any(row["target_type"] == "account" for row in result.json()["items"])
    assert "password" not in result.text
    assert "Reviewed the wording" not in result.text
    assert client.delete(BASE + "/activity", headers=admin["headers"]).status_code == 405


def test_failed_audit_rolls_back_the_content_edit(identities):
    admin, _ = identities
    row = symptom(admin)
    with patch("app.admin_system_routes.audit", side_effect=RuntimeError("synthetic audit failure")):
        with TestClient(main.app, raise_server_exceptions=False) as isolated:
            result = isolated.patch(BASE + f"/symptoms/{row['id']}", headers=admin["headers"], json={"review_note": "Must roll back", "expected_revision": row["revision"]})
    assert result.status_code == 500
    current = symptom(admin)
    assert current["revision"] == row["revision"]
    assert current["review_note"] == row["review_note"]


def test_validation_does_not_echo_untrusted_content(identities):
    admin, _ = identities
    row = symptom(admin)
    secret = "<private-input>"
    result = client.patch(BASE + f"/symptoms/{row['id']}", headers=admin["headers"], json={"review_note": secret, "expected_revision": row["revision"]})
    assert result.status_code == 422
    assert secret not in result.text


def test_mapping_diagnostics_reuse_the_request_session(identities):
    from sqlalchemy import func
    output = {"input_text": "synthetic text", "recognised_symptoms": [], "unmatched_wording": [{"unmatched_text": "synthetic text"}], "has_red_flag": False, "requires_confirmation": True}
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(NlpMappingEvent))
    with patch("app.services.system_management.SessionLocal", side_effect=AssertionError("A second session must not be needed")), \
         patch.object(main, "build_confirmation_output", return_value=output):
        result = client.post("/nlp/map", json={"text": "synthetic text"})
    assert result.status_code == 200 and result.json() == output
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(NlpMappingEvent)) == before + 1


def test_diagnostic_commit_failure_does_not_break_mapping(identities):
    from sqlalchemy.orm import Session
    output = {"input_text": "synthetic text", "recognised_symptoms": [], "unmatched_wording": [], "has_red_flag": False, "requires_confirmation": False}
    with patch.object(main, "build_confirmation_output", return_value=output), \
         patch.object(Session, "commit", side_effect=RuntimeError("Synthetic logging failure")):
        result = client.post("/nlp/map", json={"text": "synthetic text"})
    assert result.status_code == 200 and result.json() == output
