"""Test candidate loading, failure handling and process caching."""

import copy
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.errors import ServiceNotReadyError
from app.ml import candidate_loader as loader


@pytest.fixture
def setup_loader(tmp_path, monkeypatch):
    release = json.loads(
        loader.CANDIDATE_RELEASE_RECORD.read_text(encoding="utf-8")
    )
    release_path = tmp_path / "release.json"
    release_path.write_text(json.dumps(release), encoding="utf-8")

    runtime = SimpleNamespace(
        provenance={
            "bundle_id": loader.CANDIDATE_BUNDLE_ID,
            "runtime_version": loader.CANDIDATE_RUNTIME_VERSION,
            "model_version": loader.CANDIDATE_MODEL_VERSION,
            "schema_id": loader.CANDIDATE_SCHEMA_ID,
            "policy_id": loader.CANDIDATE_POLICY_ID,
            "manifest_sha256": release["bundle_manifest_sha256"],
            "environment": copy.deepcopy(release["environment"]),
        },
        schema={"feature_names": [f"feature_{i}" for i in range(70)]},
        classes=tuple(f"condition_{i}" for i in range(21)),
        predict=Mock(),
    )

    portable_load = Mock(return_value=runtime)
    monkeypatch.setattr(loader, "_cached_runtime", None)
    monkeypatch.setattr(loader, "CANDIDATE_RELEASE_RECORD", release_path)
    monkeypatch.setattr(loader, "_load_portable_runtime", portable_load)

    return release, release_path, runtime, portable_load


def test_loads_with_trusted_digest_and_does_not_predict(setup_loader):
    release, _, runtime, portable_load = setup_loader

    assert loader.load_candidate_runtime() is runtime

    portable_load.assert_called_once_with(
        loader.CANDIDATE_BUNDLE_DIRECTORY,
        release["bundle_manifest_sha256"],
    )
    runtime.predict.assert_not_called()


def test_successful_loading_is_cached(setup_loader):
    _, _, runtime, portable_load = setup_loader

    assert loader.load_candidate_runtime() is runtime
    assert loader.load_candidate_runtime() is runtime
    portable_load.assert_called_once()


def test_concurrent_calls_share_one_loaded_runtime(setup_loader):
    _, _, runtime, portable_load = setup_loader

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda _: loader.load_candidate_runtime(), range(16))
        )

    assert all(result is runtime for result in results)
    portable_load.assert_called_once()


def test_loading_is_independent_of_working_directory(
    setup_loader, tmp_path, monkeypatch
):
    _, _, runtime, _ = setup_loader
    monkeypatch.chdir(tmp_path)

    assert loader.CANDIDATE_BUNDLE_DIRECTORY.is_absolute()
    assert loader.load_candidate_runtime() is runtime


@pytest.mark.parametrize(
    "field, value",
    [
        ("status", "pending"),
        ("runtime_version", "9.9.9"),
        ("bundle_id", "different_bundle"),
        ("deployment_approved", True),
        ("bundle_manifest_sha256", "invalid"),
        ("bundle_manifest_sha256", None),
    ],
)
def test_invalid_release_is_rejected_before_loading(
    setup_loader, field, value
):
    release, path, _, portable_load = setup_loader
    release[field] = value
    path.write_text(json.dumps(release), encoding="utf-8")

    with pytest.raises(ServiceNotReadyError) as caught:
        loader.load_candidate_runtime()

    assert isinstance(caught.value.__cause__, ValueError)
    portable_load.assert_not_called()


def test_missing_release_is_service_unavailable(setup_loader):
    _, path, _, portable_load = setup_loader
    path.unlink()

    with pytest.raises(ServiceNotReadyError) as caught:
        loader.load_candidate_runtime()

    assert isinstance(caught.value.__cause__, FileNotFoundError)
    portable_load.assert_not_called()


def test_malformed_release_is_service_unavailable(setup_loader):
    _, path, _, portable_load = setup_loader
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ServiceNotReadyError):
        loader.load_candidate_runtime()

    portable_load.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("Missing bundle"),
        ImportError("Missing runtime"),
        ValueError("Fingerprint mismatch"),
        RuntimeError("Incompatible model"),
    ],
)
def test_loading_failures_are_not_cached(setup_loader, failure):
    _, _, runtime, portable_load = setup_loader
    portable_load.side_effect = [failure, runtime]

    with pytest.raises(ServiceNotReadyError) as caught:
        loader.load_candidate_runtime()

    assert caught.value.__cause__ is failure
    assert loader._cached_runtime is None
    assert loader.load_candidate_runtime() is runtime
    assert portable_load.call_count == 2


@pytest.mark.parametrize(
    "field",
    [
        "bundle_id",
        "runtime_version",
        "model_version",
        "schema_id",
        "policy_id",
        "manifest_sha256",
        "environment",
    ],
)
def test_loaded_identity_mismatch_is_rejected(setup_loader, field):
    _, _, runtime, _ = setup_loader
    runtime.provenance[field] = "wrong"

    with pytest.raises(ServiceNotReadyError):
        loader.load_candidate_runtime()

    assert loader._cached_runtime is None


@pytest.mark.parametrize("invalid_part", ["features", "classes", "predict"])
def test_invalid_runtime_structure_is_rejected(setup_loader, invalid_part):
    _, _, runtime, _ = setup_loader

    if invalid_part == "features":
        runtime.schema["feature_names"] = ["duplicate"] * 70
    elif invalid_part == "classes":
        runtime.classes = ("duplicate",) * 21
    else:
        runtime.predict = None

    with pytest.raises(ServiceNotReadyError):
        loader.load_candidate_runtime()

    assert loader._cached_runtime is None


def test_wrong_installed_version_is_rejected(monkeypatch):
    monkeypatch.setattr(loader.metadata, "version", lambda name: "9.9.9")

    with pytest.raises(ValueError, match="Installed candidate runtime version"):
        loader._load_portable_runtime(
            loader.CANDIDATE_BUNDLE_DIRECTORY,
            "0" * 64,
        )