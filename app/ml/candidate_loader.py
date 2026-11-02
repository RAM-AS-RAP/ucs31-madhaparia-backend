"""Load the verified respiratory candidate without activating it."""

from importlib import metadata
import json
from pathlib import Path
import re
from threading import Lock

from app.core.errors import ServiceNotReadyError


BACKEND_ROOT = Path(__file__).resolve().parents[2]

CANDIDATE_BUNDLE_DIRECTORY = (
    BACKEND_ROOT / "app/ml/artifacts/candidate_v1/bundle"
)
CANDIDATE_RELEASE_RECORD = (
    BACKEND_ROOT
    / "docs/candidate_runtime_release/release_manifest.json"
)

CANDIDATE_MODEL_VERSION = "candidate_v1_research"
CANDIDATE_SCHEMA_ID = "respiratory_candidate_v1"
CANDIDATE_POLICY_ID = "candidate_v1_research_uncertainty_v1"
CANDIDATE_RUNTIME_VERSION = "0.1.0"
CANDIDATE_BUNDLE_ID = "respiratory_candidate_v1-runtime-0.1.0"

_cached_runtime = None
_load_lock = Lock()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read_release_record():
    release = json.loads(
        CANDIDATE_RELEASE_RECORD.read_text(encoding="utf-8")
    )

    _require(isinstance(release, dict), "Invalid candidate release record.")

    expected = {
        "status": "portable_runtime_verified",
        "runtime_version": CANDIDATE_RUNTIME_VERSION,
        "bundle_id": CANDIDATE_BUNDLE_ID,
    }

    for name, value in expected.items():
        _require(
            release.get(name) == value,
            f"Unexpected candidate release field: {name}",
        )

    _require(
        release.get("deployment_approved") is False,
        "Expected the evaluated research release.",
    )

    manifest_digest = release.get("bundle_manifest_sha256")
    _require(
        isinstance(manifest_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", manifest_digest) is not None,
        "Invalid trusted bundle-manifest digest.",
    )

    return release


def _load_portable_runtime(bundle_directory, manifest_digest):
    # Delay importing the optional candidate package until requested.
    installed_version = metadata.version("respiratory-candidate-runtime")
    _require(
        installed_version == CANDIDATE_RUNTIME_VERSION,
        "Installed candidate runtime version differs from the release.",
    )

    from respiratory_candidate_runtime.loader import load_runtime

    return load_runtime(
        bundle_directory,
        expected_manifest_sha256=manifest_digest,
    )


def _validate_runtime_identity(runtime, release):
    provenance = runtime.provenance

    expected = {
        "bundle_id": CANDIDATE_BUNDLE_ID,
        "runtime_version": CANDIDATE_RUNTIME_VERSION,
        "model_version": CANDIDATE_MODEL_VERSION,
        "schema_id": CANDIDATE_SCHEMA_ID,
        "policy_id": CANDIDATE_POLICY_ID,
        "manifest_sha256": release["bundle_manifest_sha256"],
    }

    for name, value in expected.items():
        _require(
            provenance.get(name) == value,
            f"Loaded candidate identity mismatch: {name}",
        )

    _require(
        provenance.get("environment") == release.get("environment"),
        "Loaded candidate environment differs from the release.",
    )

    features = runtime.schema["feature_names"]
    classes = runtime.classes

    _require(
        len(features) == 70 and len(set(features)) == 70,
        "Unexpected candidate feature dimensions.",
    )
    _require(
        len(classes) == 21 and len(set(classes)) == 21,
        "Unexpected candidate class dimensions.",
    )
    _require(
        callable(getattr(runtime, "predict", None)),
        "Loaded candidate has no prediction interface.",
    )


def load_candidate_runtime():
    """Return verified resources, loading once per successful process lifetime.

    Loading performs no predictions or database operations.
    Failed loading is not cached. No alternative model is substituted.
    Artifact replacement requires a process restart.
    """

    global _cached_runtime

    with _load_lock:
        if _cached_runtime is not None:
            return _cached_runtime

        try:
            release = _read_release_record()
            runtime = _load_portable_runtime(
                CANDIDATE_BUNDLE_DIRECTORY,
                release["bundle_manifest_sha256"],
            )
            _validate_runtime_identity(runtime, release)
        except Exception as error:
            raise ServiceNotReadyError(
                "The respiratory candidate is unavailable. "
                "Check its installed runtime, trusted release record "
                "and verified artifact bundle."
            ) from error

        _cached_runtime = runtime
        return runtime