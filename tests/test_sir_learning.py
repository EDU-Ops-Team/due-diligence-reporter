from __future__ import annotations

from unittest.mock import patch

from due_diligence_reporter.provenance import ProvenanceVerdict
from due_diligence_reporter.sir_learning import build_sir_learning_review


def _verdict(label: str) -> ProvenanceVerdict:
    return ProvenanceVerdict(label, 0.95, "test", f"{label} test verdict")


def test_builds_ready_review_when_ai_and_cds_sirs_exist() -> None:
    files = [
        {
            "id": "ai",
            "name": "alpha-keller_2026-05-14_SIR.docx",
            "doc_type": "sir",
            "modifiedTime": "2026-05-14T10:00:00Z",
        },
        {
            "id": "cds",
            "name": "Alpha Keller CDS SIR.pdf",
            "doc_type": "sir",
            "modifiedTime": "2026-05-15T10:00:00Z",
        },
    ]

    def fake_classify(file_info, *_args, **_kwargs):
        return _verdict("ai_generated" if file_info["id"] == "ai" else "vendor")

    with patch("due_diligence_reporter.sir_learning.classify_provenance", fake_classify):
        review = build_sir_learning_review(files, gc=None)

    data = review.to_dict()
    assert data["status"] == "ready_for_review"
    assert data["ai_sir"]["file_id"] == "ai"
    assert data["cds_sir"]["file_id"] == "cds"
    assert "source retrieval gap" in data["review_dimensions"]


def test_waits_for_cds_sir_when_only_ai_sir_exists() -> None:
    files = [
        {
            "id": "ai",
            "name": "alpha-keller_2026-05-14_SIR.docx",
            "doc_type": "sir",
        }
    ]

    with patch(
        "due_diligence_reporter.sir_learning.classify_provenance",
        return_value=_verdict("ai_generated"),
    ):
        review = build_sir_learning_review(files, gc=None)

    assert review.status == "waiting_for_cds_sir"
    assert review.ai_sir is not None
    assert review.cds_sir is None
