"""Presentation overrides and privacy-preserving NLP diagnostic recording."""
import logging
from copy import deepcopy

from sqlalchemy import select

from app.core.database import SessionLocal
from app.db.models import DiseaseInformation, NlpMappingEvent

logger = logging.getLogger(__name__)


def apply_disease_information(predictions, db):
    """Apply current descriptions before saving; never rewrite historical results."""
    result = deepcopy(predictions)
    if not result:
        return result
    information = {item.condition: item for item in db.scalars(select(DiseaseInformation).where(
        DiseaseInformation.condition.in_([item["condition"] for item in result])
    )).all()}
    for item in result:
        info = information.get(item["condition"])
        if info:
            item.update(display_name=info.display_name, description=info.short_description)
    return result


def record_mapping_event(output=None, *, db=None):
    """Best-effort transaction: diagnostics must not break the patient flow.

    Store counts only. No raw text, exception details, IP addresses or identities.
    Historical errors were not recorded and cannot be reconstructed.
    """
    if output is None:
        kind, recognised, unmatched = "mapping_error", 0, 0
    else:
        recognised = len(output.get("recognised_symptoms", []))
        unmatched = len(output.get("unmatched_wording", []))
        if recognised and not unmatched:
            return
        kind = "no_match" if not recognised else "unmatched_wording"
    try:
        event = NlpMappingEvent(kind=kind, recognised_count=recognised, unmatched_count=unmatched)
        if db is not None:
            # Called only after the read-only mapping request. End its read
            # transaction before logging; never hold one pool connection while
            # waiting to acquire a second connection for the diagnostic write.
            db.rollback()
            db.add(event)
            db.commit()
        else:
            with SessionLocal() as session:
                session.add(event)
                session.commit()
    except Exception:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        logger.warning("NLP diagnostic recording unavailable.")
