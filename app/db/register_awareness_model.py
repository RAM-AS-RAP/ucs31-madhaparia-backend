"""Register/explicitly activate the verified sparse-input research model."""
import argparse
from sqlalchemy import select
from app.core.database import SessionLocal
from app.db.models import ModelVersion
from app.services.awareness import bundle, MODEL_VERSION, MODEL_SHA256


def register(db, activate=False):
    data = bundle()
    row = db.scalar(select(ModelVersion).where(ModelVersion.version == MODEL_VERSION))
    if row is None:
        row = ModelVersion(model_name='Partial-evidence likelihood research', version=MODEL_VERSION,
            purpose='Research-only bilingual symptom awareness; named patient results withheld',
            artifact_filename='v3_partial_evidence.json', feature_count=len(data['features']), class_count=len(data['classes']),
            training_samples=443075, validation_samples=0, test_samples=0,
            parameters={'sha256': MODEL_SHA256, 'source': 'codex-version/codex-respiracare-improvements-2026-10-29', 'independent_local_clinical_validation': False},
            validation_metrics={}, test_metrics={}, is_active=False)
        db.add(row)
    elif row.parameters.get('sha256') != MODEL_SHA256:
        raise ValueError('Existing model registration has incompatible provenance; not overwritten')
    if activate:
        for other in db.scalars(select(ModelVersion)).all():
            other.is_active = False
        row.is_active = True
    db.flush()
    return row


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            row = register(db, args.activate)
            db.commit()
            print(f'{row.version}: active={row.is_active}; no clinical validation claimed')
        except Exception:
            db.rollback()
            raise
