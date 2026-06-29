from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from due_diligence_reporter import server


def test_save_skill_report_registers_generated_support_doc(monkeypatch) -> None:
    gc = MagicMock()
    gc.create_document.return_value = {
        "id": "doc-1",
        "webViewLink": "https://drive/doc-1",
    }
    monkeypatch.setattr(server, "_make_google_client", lambda: gc)
    monkeypatch.setattr(
        server,
        "_get_or_create_m1_folder",
        lambda _gc, _folder_id: {"id": "m1", "name": "M1 - Acquire Property"},
    )
    register_rhodes = MagicMock(return_value={"status": "registered"})
    monkeypatch.setattr(server, "register_rhodes_document_for_upload", register_rhodes)

    result = asyncio.run(
        server.save_skill_report(
            skill_name="School Approval",
            site_name="Alpha Test",
            site_id="SITE1",
            drive_folder_url="https://drive.google.com/drive/folders/root",
            ddr_doc_type="school_approval_report",
            skill_data={"status": "success", "score": 95},
        )
    )

    assert result["status"] == "success"
    assert result["rhodes_registration"]["status"] == "registered"
    register_rhodes.assert_called_once()
    assert register_rhodes.call_args.kwargs["site_id"] == "SITE1"
    assert register_rhodes.call_args.kwargs["ddr_doc_type"] == "school_approval_report"
    assert register_rhodes.call_args.kwargs["drive_file_id"] == "doc-1"
