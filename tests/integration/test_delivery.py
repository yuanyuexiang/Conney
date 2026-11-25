import os
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import text

from relay.api.app import app
from relay.auth.accounts import Role, User
from relay.database.session import get_engine
from relay.mail.intake import MailIdentity, receive
from relay.storage.archive import Archive
from relay.workers.import_materials import import_directory
from relay.workers.parser import process_one

_BROWSER = {"X-Relay-Request": "1"}


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_imported_material_waits_for_confirmed_mapping(
    tmp_path: Path, signed_in: Callable[[TestClient, Role], User], request: pytest.FixtureRequest
) -> None:
    item = "00" + uuid4().hex[:8]

    def cleanup() -> None:
        with get_engine().begin() as connection:
            connection.execute(
                text("DELETE FROM material_candidates WHERE customer_item = :item"), {"item": item}
            )
            connection.execute(
                text("DELETE FROM material_mappings WHERE customer_item = :item"), {"item": item}
            )

    request.addfinalizer(cleanup)
    for filename, document_date in [("a-new.xlsx", "20261122"), ("z-old.xlsx", "20261115")]:
        workbook = Workbook()
        workbook.active.title = "交付明细"
        workbook.create_sheet("字段路径对照")
        sheet = workbook["交付明细"]
        sheet.append(["ZF DELFOR 交付预测"])
        sheet.append(["报文日期\nDocDate", document_date, "收货方编号\nShipToNo", "06124"])
        sheet.append(["客户件号\nCustomerPartNo", item])
        workbook.save(tmp_path / filename)
        workbook.close()

    path = f"/api/v1/material-mappings/candidates?customer=zf&q={item}"
    assert import_directory(tmp_path) == (2, {"zf": 1})
    with TestClient(app) as viewer, TestClient(app) as admin:
        signed_in(viewer, "viewer")
        signed_in(admin, "admin")
        assert viewer.get(path).status_code == 403
        response = admin.get(path)
        assert response.status_code == 200
        candidates = response.json()
        assert len(candidates) == 1
        assert candidates[0] == {
            "id": candidates[0]["id"],
            "customer": "zf",
            "customer_item": item,
            "order_no": None,
            "order_line": None,
            "ship_to": "06124",
            "supplier_item": None,
            "source_filename": "a-new.xlsx",
            "source_cell": "交付明细!B3",
            "source_date": "2026-11-22",
        }
        assert import_directory(tmp_path) == (2, {"zf": 1})
        assert admin.get(path).json() == candidates

        saved = admin.post(
            "/api/v1/material-mappings",
            headers=_BROWSER,
            json={
                "customer": "zf",
                "customer_item": item,
                "internal_item": "0000024",
                "ship_to": "06124",
            },
        )
        assert saved.status_code == 201
        assert import_directory(tmp_path) == (2, {"zf": 1})
        assert admin.get(path).json() == []
        mappings = admin.get(f"/api/v1/material-mappings?customer=zf&q={item}").json()
        assert [mapping["internal_item"] for mapping in mappings] == ["0000024"]


def _remove_test_delivery(mail_id: int, item: str) -> None:
    with get_engine().begin() as connection:
        params = {"mail_id": mail_id, "item": item}
        attachments = "SELECT id FROM received_attachments WHERE mail_id = :mail_id"
        jobs = f"SELECT id FROM parse_jobs WHERE attachment_id IN ({attachments})"
        runs = f"SELECT id FROM parse_runs WHERE job_id IN ({jobs})"
        blocks = f"SELECT id FROM parsed_material_blocks WHERE run_id IN ({runs})"
        hashes = (
            connection.execute(
                text("SELECT sha256 FROM received_attachments WHERE mail_id = :mail_id"), params
            )
            .scalars()
            .all()
        )
        for statement in (
            f"DELETE FROM delivery_drafts WHERE run_id IN ({runs})",
            f"DELETE FROM parsed_quantity_records WHERE block_id IN ({blocks})",
            f"DELETE FROM parsed_material_blocks WHERE run_id IN ({runs})",
            f"DELETE FROM parse_runs WHERE job_id IN ({jobs})",
            f"DELETE FROM parse_jobs WHERE attachment_id IN ({attachments})",
            "DELETE FROM received_attachments WHERE mail_id = :mail_id",
            "DELETE FROM received_mail WHERE id = :mail_id",
            "DELETE FROM material_mappings WHERE customer_item = :item",
        ):
            connection.execute(text(statement), params)
        connection.execute(
            text(
                "DELETE FROM archive_objects AS blob WHERE blob.sha256 = ANY(:hashes) "
                "AND NOT EXISTS (SELECT 1 FROM received_attachments WHERE sha256 = blob.sha256)"
            ),
            {"hashes": hashes},
        )


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_delivery_draft_from_archive_to_generation(
    tmp_path: Path, signed_in: Callable[[TestClient, Role], User], request: pytest.FixtureRequest
) -> None:
    item = "00" + uuid4().hex[:8]
    workbook = Workbook()
    sheet = workbook["Sheet"]
    for row in [
        ["DRX-DELFOR-长期交付预测"],
        ["DelforNo", "000260", "DelforCreateDate", "20261122"],
        ["poNo", "0000042", "BuyerItemNo", item, "SupplierItemNumber", "SUP-1"],
        [
            "DeliveryPlanStatusIndicator",
            "QtyType",
            "Qty",
            "QtyUnit",
            "RequestedDeliveryDate",
            "HorizonStartDate",
            "HorizonEndDate",
        ],
        ["1", "1", "800", "C62", "20261127"],
        ["1", "1", "0", "C62", "20261204"],
        ["4", "1", "300", "C62", None, "20261205", "20261210"],
        ["10", "83", "15", "C62", "20261116"],
    ]:
        sheet.append(row)
    content = BytesIO()
    workbook.save(content)
    mail = EmailMessage()
    mail["From"] = "sanitized@example.com"
    mail["Subject"] = "DRX delivery draft"
    mail.set_content("Test attachment")
    mail.add_attachment(
        content.getvalue(),
        maintype="application",
        subtype="octet-stream",
        filename="forecast.xlsx",
    )
    archive = Archive(tmp_path / "archive")
    identity = MailIdentity(f"test:{item}", "INBOX", 1, 1, datetime.now(UTC))
    received = receive(mail.as_bytes(), identity, archive)
    request.addfinalizer(lambda: _remove_test_delivery(received.mail_id, item))
    assert process_one(archive)

    with TestClient(app) as client, TestClient(app) as reader:
        owner = signed_in(client, "admin")
        signed_in(reader, "viewer")
        attachments = client.get(f"/api/v1/intake/emails/{received.mail_id}").json()["attachments"]
        assert attachments[0]["template"] == "drx_delfor", attachments
        source_url = f"/api/v1/delivery/attachments/{attachments[0]['id']}"
        assert reader.post(source_url, headers=_BROWSER).status_code == 403
        assert client.post(source_url).status_code == 403
        opened = client.post(source_url, headers=_BROWSER)
        assert opened.status_code == 200, opened.text
        draft = opened.json()
        draft_url = f"/api/v1/delivery/{draft['id']}"
        material = draft["materials"][0]
        assert draft["pending_mappings"] == 1
        assert client.post(source_url, headers=_BROWSER).json()["id"] == draft["id"]
        assert reader.get(draft_url).status_code == 200
        assert reader.get("/api/v1/material-mappings").status_code == 403
        assert [entry["state"] for entry in material["entries"]] == [
            "transport_missing",
            "recorded_zero",
            "period_review",
            "code_review",
        ]
        entry_id = material["entries"][0]["id"]
        shipment_url = f"{draft_url}/shipments/{entry_id}"
        shipment = {
            "ship_date": "2026-11-09",
            "mode": "air",
            "source_note": "=SUM(1,2)",
        }
        assert (
            client.put(
                shipment_url, headers=_BROWSER, json={"revision": 1, "shipment": shipment}
            ).status_code
            == 409
        )

        mapping_url = "/api/v1/material-mappings"
        mapping = {
            "customer": "drx",
            "customer_item": item,
            "order_no": "42",
            "internal_item": "0000012345",
            "supplier_item": "SUP-1",
            "units_per_carton": 800,
            "cartons_per_pallet": 12,
            "safety_stock": 0,
        }
        saved = client.post(mapping_url, headers=_BROWSER, json=mapping)
        assert saved.status_code == 201, saved.text
        assert (
            client.post(
                mapping_url, headers=_BROWSER, json={**mapping, "order_no": "00042"}
            ).status_code
            == 409
        )
        listed = client.get(mapping_url, params={"customer": "drx", "q": item}).json()
        assert [result["order_no"] for result in listed] == ["42"]

        refreshed = client.post(f"{draft_url}/refresh", headers=_BROWSER, json={"revision": 1})
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["materials"][0]["mapping_state"] == "matched"
        assert refreshed.json()["materials"][0]["order_no"] == "0000042"
        sent = client.put(
            shipment_url, headers=_BROWSER, json={"revision": 2, "shipment": shipment}
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["materials"][0]["entries"][0]["eta"] == "2026-11-23"
        assert sent.json()["materials"][0]["entries"][0]["quantity_cell"] == "Sheet!C5"
        assert client.get(f"{draft_url}/draft.xlsx?revision=2").status_code == 409
        result = reader.get(f"{draft_url}/draft.xlsx?revision=3")
        assert result.status_code == 200
        assert "application/vnd.openxmlformats" in result.headers["Content-Type"]
        exported = load_workbook(BytesIO(result.content))
        production = exported["给生产"]
        assert production["C5"].value == "0000012345"
        assert production["F5"].value == "=D5*E5"
        assert production["J5"].value == "0000042"
        assert production["K5"].value == 0
        assert production["L5"].value == 800
        assert production["L6"].value == "=L3+14"
        source = exported["需求与来源"]
        assert source["K6"].value == "Sheet!C5"
        assert source["D7"].value == "0"
        assert source["R6"].value == shipment["source_note"]
        assert source["R6"].data_type == "s"
        assert client.get("/api/v1/delivery?q=no-such-document").json() == []
        assert client.get("/api/v1/delivery?q=000260").json()[0]["id"] == draft["id"]
        assert any(
            row["id"] == draft["id"] for row in client.get("/api/v1/delivery?review=true").json()
        )
        dashboard = reader.get("/api/v1/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json()["parsed_today"] >= 1
        assert dashboard.json()["delivery_drafts"] == dashboard.json()["drafts_for_review"] == 1
        with TestClient(app) as manager:
            signed_in(manager, "admin")
            assert manager.delete(f"/api/v1/users/{owner.id}", headers=_BROWSER).status_code == 204
        assert reader.get(draft_url).json() == sent.json()
        with get_engine().connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT updated_by FROM delivery_drafts WHERE run_id = :id"),
                    {"id": draft["id"]},
                )
                == owner.id
            )
