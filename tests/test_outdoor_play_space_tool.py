from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from due_diligence_reporter import server


def test_apply_outdoor_play_space_skill_uploads_and_registers_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    monkeypatch.setattr(server, "_resolve_outdoor_play_space_skill_dir", lambda: skill_dir)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "--skip-drive-upload" in cmd
        output_dir = Path(cmd[cmd.index("--output-dir") + 1])
        artifact_dir = output_dir / "123-main-st"
        artifact_dir.mkdir(parents=True)
        json_path = artifact_dir / "play_space.json"
        md_path = artifact_dir / "play_space.md"
        png_path = artifact_dir / "open_space_map.png"
        html_path = artifact_dir / "open_space_map.html"
        json_path.write_text(
            json.dumps(
                {
                    "on_site_verdict": "pass",
                    "off_site_verdict": "not_required",
                    "confidence": "B",
                    "required_outdoor_sf": 5400,
                    "safety_flags": [],
                }
            ),
            encoding="utf-8",
        )
        md_path.write_text("# Outdoor Play Space Report\n", encoding="utf-8")
        png_path.write_bytes(b"png")
        html_path.write_text("<html></html>", encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps(
                {
                    "json": str(json_path),
                    "markdown": str(md_path),
                    "open_space_map_png": str(png_path),
                    "open_space_map_html": str(html_path),
                }
            ),
            stderr="",
        )

    gc = MagicMock()

    def fake_upload(folder_id: str, file_name: str, file_bytes: bytes, *, mime_type: str) -> dict[str, str]:
        return {
            "id": f"drive-{Path(file_name).suffix.lstrip('.')}",
            "webViewLink": f"https://drive/{file_name}",
        }

    gc.upload_file_to_folder.side_effect = fake_upload
    monkeypatch.setattr(server, "_make_google_client", lambda: gc)
    monkeypatch.setattr(
        server,
        "_get_or_create_m1_folder",
        lambda _gc, _folder_id: {"id": "m1", "name": "M1 - Acquire Property"},
    )
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    register_rhodes = MagicMock(return_value={"status": "registered"})
    monkeypatch.setattr(server, "register_rhodes_document_for_upload", register_rhodes)

    result = asyncio.run(
        server.apply_outdoor_play_space_skill(
            site_name="Alpha Test",
            site_id="SITE1",
            address="123 Main St, Austin, TX",
            drive_folder_url="https://drive.google.com/drive/folders/root",
            student_count=54,
        )
    )

    assert result["status"] == "success"
    assert result["source_type"] == "outdoor_play_space_report"
    assert result["exec"]["play_area_score"] == 1
    assert result["report_data_fields"]["exec.play_area_score"] == "1"
    assert gc.upload_file_to_folder.call_count == 4
    assert register_rhodes.call_count == 4
    assert all(
        call.kwargs["ddr_doc_type"] == "outdoor_play_space_report"
        for call in register_rhodes.call_args_list
    )
    assert result["supporting_documents"]
    assert result["source_note_lines"] == [
        "play_area_score -> 1 -> Outdoor Play Space Report - Alpha Test (Markdown)",
        (
            "play_area_comment -> On-site outdoor play option passes. "
            "Required area: 5400 SF. -> Outdoor Play Space Report - Alpha Test (Markdown)"
        ),
    ]


def test_apply_outdoor_play_space_skill_groups_registration_handoff(
    monkeypatch,
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    monkeypatch.setattr(server, "_resolve_outdoor_play_space_skill_dir", lambda: skill_dir)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "--skip-drive-upload" in cmd
        output_dir = Path(cmd[cmd.index("--output-dir") + 1])
        artifact_dir = output_dir / "123-main-st"
        artifact_dir.mkdir(parents=True)
        json_path = artifact_dir / "play_space.json"
        md_path = artifact_dir / "play_space.md"
        png_path = artifact_dir / "open_space_map.png"
        html_path = artifact_dir / "open_space_map.html"
        json_path.write_text(
            json.dumps(
                {
                    "on_site_verdict": "pass",
                    "off_site_verdict": "not_required",
                    "confidence": "B",
                    "required_outdoor_sf": 5400,
                    "safety_flags": [],
                }
            ),
            encoding="utf-8",
        )
        md_path.write_text("# Outdoor Play Space Report\n", encoding="utf-8")
        png_path.write_bytes(b"png")
        html_path.write_text("<html></html>", encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps(
                {
                    "json": str(json_path),
                    "markdown": str(md_path),
                    "open_space_map_png": str(png_path),
                    "open_space_map_html": str(html_path),
                }
            ),
            stderr="",
        )

    gc = MagicMock()

    def fake_upload(folder_id: str, file_name: str, file_bytes: bytes, *, mime_type: str) -> dict[str, str]:
        return {
            "id": f"drive-{Path(file_name).suffix.lstrip('.')}",
            "webViewLink": f"https://drive/{file_name}",
        }

    gc.upload_file_to_folder.side_effect = fake_upload
    monkeypatch.setattr(server, "_make_google_client", lambda: gc)
    monkeypatch.setattr(
        server,
        "_get_or_create_m1_folder",
        lambda _gc, _folder_id: {"id": "m1", "name": "M1 - Acquire Property"},
    )
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    register_rhodes = MagicMock(
        return_value={
            "status": "failed",
            "reason": "rhodes_error",
            "error": "Action requires confirmation",
            "rhodes_doc_type": "other",
            "rhodes_quality_bar": "outdoorRecreation",
        }
    )
    handoff_calls: list[dict[str, Any]] = []

    def fake_handoff(**kwargs: Any) -> dict[str, Any]:
        handoff_calls.append(kwargs)
        return {
            "status": "created",
            "rhodes_note_id": "NOTE1",
            "documents": [
                {"file_id": str(document["drive_file_id"])}
                for document in kwargs["documents"]
            ],
            "document_count": len(kwargs["documents"]),
            "rhodes_registration_status": "pending_user_action",
            "human_followup_required": True,
            "human_followup_type": "document_registration",
            "remaining_work": [],
        }

    monkeypatch.setattr(server, "register_rhodes_document_for_upload", register_rhodes)
    monkeypatch.setattr(
        server,
        "create_document_registration_handoff_for_uploads",
        fake_handoff,
    )

    result = asyncio.run(
        server.apply_outdoor_play_space_skill(
            site_name="Alpha Test",
            site_id="SITE1",
            address="123 Main St, Austin, TX",
            drive_folder_url="https://drive.google.com/drive/folders/root",
            student_count=54,
        )
    )

    assert result["status"] == "success"
    assert register_rhodes.call_count == 4
    assert all(
        call.kwargs["handoff_on_registration_failure"] is False
        for call in register_rhodes.call_args_list
    )
    assert len(handoff_calls) == 1
    assert handoff_calls[0]["site_id"] == "SITE1"
    assert handoff_calls[0]["site_name"] == "Alpha Test"
    assert handoff_calls[0]["site_address"] == "123 Main St, Austin, TX"
    assert len(handoff_calls[0]["documents"]) == 4
    assert result["document_registration_handoff"]["rhodes_note_id"] == "NOTE1"
    assert {
        artifact["rhodes_registration"]["status"]
        for artifact in result["artifacts"].values()
    } == {"pending_user_action"}
    assert {
        document["registration_status"]
        for document in result["supporting_documents"]
    } == {"pending_user_action"}


def test_apply_outdoor_play_space_skill_requires_png(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    monkeypatch.setattr(server, "_resolve_outdoor_play_space_skill_dir", lambda: skill_dir)

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        output_dir = Path(cmd[cmd.index("--output-dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "play_space.json"
        md_path = output_dir / "play_space.md"
        html_path = output_dir / "open_space_map.html"
        json_path.write_text(
            json.dumps({"on_site_verdict": "pass", "confidence": "B"}),
            encoding="utf-8",
        )
        md_path.write_text("# Outdoor Play Space Report\n", encoding="utf-8")
        html_path.write_text("<html></html>", encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps(
                {
                    "json": str(json_path),
                    "markdown": str(md_path),
                    "open_space_map_png": str(output_dir / "missing.png"),
                    "open_space_map_html": str(html_path),
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = asyncio.run(
        server.apply_outdoor_play_space_skill(
            site_name="Alpha Test",
            site_id="SITE1",
            address="123 Main St, Austin, TX",
            drive_folder_url="https://drive.google.com/drive/folders/root",
            student_count=54,
        )
    )

    assert result["status"] == "error"
    assert result["error"] == "Outdoor Play Space PNG missing"
