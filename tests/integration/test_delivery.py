import os
import smtplib
from collections.abc import Callable
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import text

from relay.api.app import app
from relay.auth.accounts import Role, User
from relay.database.session import get_engine
from relay.mail.configuration import get_mail_settings
from relay.mail.intake import MailIdentity, receive
from relay.storage.archive import Archive
from relay.workers import delivery as delivery_worker
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

        candidate_url = f"/api/v1/material-mappings/candidates/{candidates[0]['id']}"
        assert viewer.delete(candidate_url, headers=_BROWSER).status_code == 403
        assert admin.delete(candidate_url).status_code == 403
        assert admin.delete(candidate_url, headers=_BROWSER).status_code == 204
        assert admin.delete(candidate_url, headers=_BROWSER).status_code == 204
        assert admin.get(path).json() == []
        assert import_directory(tmp_path) == (2, {"zf": 1})
        (reimported,) = admin.get(path).json()
        assert reimported["id"] != candidates[0]["id"]

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
        assert (
            admin.delete(
                f"/api/v1/material-mappings/{mappings[0]['id']}",
                headers=_BROWSER,
                params={"revision": mappings[0]["revision"]},
            ).status_code
            == 204
        )
        assert admin.get(path).json() == [reimported]


def _remove_test_delivery(mail_id: int, item: str) -> None:
    with get_engine().begin() as connection:
        params = {"mail_id": mail_id, "item": item}
        attachments = "SELECT id FROM received_attachments WHERE mail_id = :mail_id"
        jobs = f"SELECT id FROM parse_jobs WHERE attachment_id IN ({attachments})"
        runs = f"SELECT id FROM parse_runs WHERE job_id IN ({jobs})"
        blocks = f"SELECT id FROM parsed_material_blocks WHERE run_id IN ({runs})"
        releases = f"SELECT id FROM delivery_releases WHERE run_id IN ({runs})"
        hashes = (
            connection.execute(
                text("SELECT sha256 FROM received_attachments WHERE mail_id = :mail_id"), params
            )
            .scalars()
            .all()
        )
        for statement in (
            f"DELETE FROM delivery_dispatch_recipients WHERE release_id IN ({releases})",
            f"DELETE FROM delivery_dispatches WHERE release_id IN ({releases})",
            f"DELETE FROM delivery_releases WHERE run_id IN ({runs})",
            f"DELETE FROM delivery_draft_rule_history WHERE run_id IN ({runs})",
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
    tmp_path: Path,
    signed_in: Callable[[TestClient, Role], User],
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
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
        ["10", "83", "12", "C62", "20261123"],
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
    monkeypatch.setenv("RELAY_ARCHIVE_DIR", str(archive.root))
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
            "confirmed_interval",
            "code_review",
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

        mapping_detail = f"{mapping_url}/{saved.json()['id']}"
        old_revision = {"revision": saved.json()["revision"]}
        assert (
            reader.delete(mapping_detail, params=old_revision, headers=_BROWSER).status_code == 403
        )
        assert client.delete(mapping_detail, params=old_revision).status_code == 403
        edited = client.put(
            mapping_detail, headers=_BROWSER, json={**mapping, **old_revision, "drawing": "D-1"}
        )
        assert edited.status_code == 200
        assert (
            client.delete(mapping_detail, params=old_revision, headers=_BROWSER).status_code == 409
        )
        revision = {"revision": edited.json()["revision"]}
        assert client.delete(mapping_detail, params=revision, headers=_BROWSER).status_code == 204
        assert client.delete(mapping_detail, params=revision, headers=_BROWSER).status_code == 204
        assert client.get(mapping_url, params={"q": item}).json() == []
        assert reader.get(draft_url).json() == sent.json()
        rematched = client.post(f"{draft_url}/refresh", headers=_BROWSER, json={"revision": 3})
        assert rematched.status_code == 200, rematched.text
        assert rematched.json()["pending_mappings"] == 1
        assert rematched.json()["materials"][0]["mapping"] is None
        assert rematched.json()["materials"][0]["entries"] == sent.json()["materials"][0]["entries"]
        with TestClient(app) as manager:
            signed_in(manager, "admin")
            assert manager.delete(f"/api/v1/users/{owner.id}", headers=_BROWSER).status_code == 204
            assert reader.get(draft_url).json() == rematched.json()
            with get_engine().connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT updated_by FROM delivery_drafts WHERE run_id = :id"),
                        {"id": draft["id"]},
                    )
                    == owner.id
                )

            assert manager.post(mapping_url, headers=_BROWSER, json=mapping).status_code == 201
            current_revision = rematched.json()["revision"]
            params = {"revision": current_revision}
            assert reader.delete(draft_url, params=params, headers=_BROWSER).status_code == 403
            assert manager.delete(draft_url, params=params).status_code == 403
            stale = manager.delete(
                draft_url, params={"revision": current_revision - 1}, headers=_BROWSER
            )
            assert stale.status_code == 409
            assert manager.delete(draft_url, params=params, headers=_BROWSER).status_code == 204
            assert reader.get(draft_url).status_code == 404
            assert reader.get(f"{draft_url}/draft.xlsx", params=params).status_code == 404
            assert reader.get("/api/v1/delivery").json() == []
            assert reader.get("/api/v1/delivery?review=true").json() == []
            dashboard_after = reader.get("/api/v1/dashboard").json()
            assert dashboard_after["delivery_drafts"] == dashboard_after["drafts_for_review"] == 0
            raw = reader.get(f"/api/v1/intake/attachments/{attachments[0]['id']}/file")
            assert raw.status_code == 200
            assert raw.content == content.getvalue()

            reopened = manager.post(source_url, headers=_BROWSER)
            assert reopened.status_code == 200
            generated = reopened.json()
            assert generated["revision"] > current_revision
            assert generated["id"] == draft["id"]
            assert generated["materials"][0]["mapping"]["internal_item"] == "0000012345"
            regenerated_entry = generated["materials"][0]["entries"][0]
            assert regenerated_entry["shipment"] is None
            assert regenerated_entry["state"] == "transport_missing"
            assert manager.delete(draft_url, params=params, headers=_BROWSER).status_code == 409
            assert reader.get(draft_url).json() == generated

            entries = generated["materials"][0]["entries"]
            interval_url = f"{draft_url}/reviews/{entries[2]['id']}"
            code_url = f"{draft_url}/reviews/{entries[3]['id']}"
            keep = {
                "revision": generated["revision"],
                "review": {"action": "keep_interval", "source_note": "Customer forecast"},
            }
            assert reader.put(interval_url, headers=_BROWSER, json=keep).status_code == 403
            assert manager.put(interval_url, json=keep).status_code == 403
            kept = manager.put(interval_url, headers=_BROWSER, json=keep).json()
            assert kept["materials"][0]["entries"][2]["state"] == "confirmed_interval"
            assert kept["pending_entries"] == generated["pending_entries"] == 2
            assert kept["pending_shipments"] == generated["pending_shipments"] == 1
            assert manager.put(interval_url, headers=_BROWSER, json=keep).status_code == 409

            date_review = {
                "revision": kept["revision"],
                "review": {
                    "action": "set_date",
                    "delivery_date": "2026-12-11",
                    "source_note": "Customer confirmed date",
                },
            }
            reply = manager.put(interval_url, headers=_BROWSER, json=date_review)
            assert reply.status_code == 422
            assert reply.json()["detail"] == "invalid_delivery_review"
            date_review["review"]["delivery_date"] = "2026-12-09"
            dated = manager.put(interval_url, headers=_BROWSER, json=date_review).json()
            interval = dated["materials"][0]["entries"][2]
            assert interval["requested_date"] is None
            assert interval["earliest_date"] == "2026-12-05"
            assert interval["period"] == "2026-12-07"
            assert interval["state"] == "transport_missing"
            assert dated["pending_entries"] == 2
            assert dated["pending_shipments"] == 2

            code_review = {
                "revision": dated["revision"],
                "review": {"action": "exclude", "source_note": "=Source confirmation"},
            }
            group_url = f"{draft_url}/reviews"
            mixed = {**code_review, "entry_ids": [entries[2]["id"], entries[3]["id"]]}
            assert manager.put(group_url, headers=_BROWSER, json=mixed).status_code == 422
            assert reader.get(draft_url).json() == dated
            group = {**code_review, "entry_ids": [entries[3]["id"], entries[4]["id"]]}
            assert reader.put(group_url, headers=_BROWSER, json=group).status_code == 403
            excluded = manager.put(group_url, headers=_BROWSER, json=group).json()
            assert excluded["materials"][0]["entries"][3]["state"] == "excluded"
            assert excluded["materials"][0]["entries"][4]["state"] == "excluded"
            assert excluded["pending_entries"] == 0
            assert excluded["pending_shipments"] == 2
            assert reader.get("/api/v1/delivery?review=true").json() == []
            assert reader.get("/api/v1/delivery").json()[0]["pending_shipments"] == 2
            download = reader.get(
                f"{draft_url}/draft.xlsx", params={"revision": excluded["revision"]}
            )
            trace = load_workbook(BytesIO(download.content))["需求与来源"]
            assert trace["F9"].value == "83"
            assert trace["U9"].value == "从客户需求排除"
            assert trace["W9"].data_type == "s"
            assert trace["W9"].value == "=Source confirmation"

            code_review["revision"] = excluded["revision"]
            code_review["review"]["action"] = "include"
            included = manager.put(code_url, headers=_BROWSER, json=code_review).json()
            assert included["materials"][0]["entries"][3]["state"] == "transport_missing"
            assert included["pending_shipments"] == 3
            assert included["pending_entries"] == 0
            assigned = manager.put(
                f"{draft_url}/shipments/{entries[3]['id']}",
                headers=_BROWSER,
                json={"revision": included["revision"], "shipment": shipment},
            ).json()
            assert assigned["materials"][0]["entries"][3]["state"] == "scheduled"
            code_review["revision"] = assigned["revision"]
            assert manager.put(code_url, headers=_BROWSER, json=code_review).status_code == 409
            reviewed_again = manager.post(
                f"{draft_url}/refresh", headers=_BROWSER, json={"revision": assigned["revision"]}
            ).json()
            assert reviewed_again["materials"][0]["entries"] == assigned["materials"][0]["entries"]

            templates = reader.get("/api/v1/templates").json()
            assert {template["code"] for template in templates} == {
                "drx_delfor",
                "vitesco_delfor",
                "jlr_delfor",
                "jlr_deljit",
                "kroschu_4905",
            }
            rules_url = "/api/v1/templates/drx/rules"
            rules = manager.get(rules_url).json()
            assert rules["active"]["version"] == reviewed_again["rule_version"] == "drx-2"
            attachment_id = attachments[0]["id"]
            sample = next(
                sample for sample in rules["samples"] if sample["attachment_id"] == attachment_id
            )
            assert sample["parser_version"] == attachments[0]["parser_version"]
            trial_values = {
                "baseline_id": rules["active"]["id"],
                "attachment_id": attachment_id,
                "parameters": {"air_days": 16, "sea_days": 84},
                "simulated_date": "2026-11-09",
                "source_note": "Approved transport schedule",
            }
            assert (
                reader.post(f"{rules_url}/preview", headers=_BROWSER, json=trial_values).status_code
                == 403
            )
            trial_response = manager.post(
                f"{rules_url}/preview", headers=_BROWSER, json=trial_values
            )
            assert trial_response.status_code == 201, trial_response.text
            trial = trial_response.json()

            def remove_test_rule() -> None:
                with get_engine().begin() as connection:
                    connection.execute(
                        text("DELETE FROM drx_rule_versions WHERE id = :id"), {"id": trial["id"]}
                    )

            request.addfinalizer(remove_test_rule)
            assert trial["trial"]["changed_eta_count"] == 1
            assert [entry["after"] for entry in trial["trial"]["simulated_etas"]] == [
                "2026-11-25",
                "2027-02-01",
            ]
            assert manager.get(rules_url).json()["active"]["version"] == "drx-2"
            assert trial["id"] not in {
                version["id"] for version in reader.get(rules_url).json()["versions"]
            }
            assert reader.get(draft_url).json() == reviewed_again

            publish_url = f"{rules_url}/{trial['id']}/publish"
            assert manager.post(publish_url).status_code == 403
            published = manager.post(publish_url, headers=_BROWSER)
            assert published.status_code == 200, published.text
            assert published.json()["published_at"]
            pinned = manager.post(
                f"{draft_url}/refresh",
                headers=_BROWSER,
                json={"revision": reviewed_again["revision"]},
            ).json()
            assert pinned["rule_version"] == "drx-2"
            assert pinned["materials"][0]["entries"][3]["eta"] == "2026-11-23"

            preview = manager.get(f"{draft_url}/rule-preview").json()
            assert preview["target"]["id"] == trial["id"]
            assert preview["impact"]["changed_etas"][0]["before"] == "2026-11-23"
            assert preview["impact"]["changed_etas"][0]["after"] == "2026-11-25"
            apply_url = f"{draft_url}/apply-rule"
            adoption = {"revision": preview["revision"], "target_id": trial["id"]}
            assert (
                manager.post(
                    apply_url,
                    headers=_BROWSER,
                    json={**adoption, "revision": reviewed_again["revision"]},
                ).status_code
                == 409
            )
            result = manager.post(apply_url, headers=_BROWSER, json=adoption)
            assert result.status_code == 200, result.text
            adopted = result.json()
            assert adopted["rule_version"] == trial["version"]
            updated_entry = adopted["materials"][0]["entries"][3]
            assert updated_entry == {**pinned["materials"][0]["entries"][3], "eta": "2026-11-25"}
            history_url = f"{draft_url}/rule-history"
            history = reader.get(history_url).json()
            assert [(entry["revision"], entry["rule_version"]) for entry in history] == [
                (pinned["revision"], "drx-2")
            ]
            past = reader.get(f"{history_url}/{pinned['revision']}/draft.xlsx")
            latest = reader.get(f"{draft_url}/draft.xlsx", params={"revision": adopted["revision"]})
            assert past.status_code == latest.status_code == 200
            assert load_workbook(BytesIO(past.content))["给生产"]["L6"].value == "=L3+14"
            latest_workbook = load_workbook(BytesIO(latest.content))
            assert latest_workbook["给生产"]["L6"].value == "=L3+16"
            assert latest_workbook["需求与来源"]["D4"].value == "drx-production-1"

            assert (
                manager.delete(
                    draft_url, headers=_BROWSER, params={"revision": adopted["revision"]}
                ).status_code
                == 204
            )
            assert (
                manager.post(source_url, headers=_BROWSER).json()["rule_version"]
                == trial["version"]
            )


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_confirmed_delivery_sends_same_workbook_with_recipient_results(
    tmp_path: Path,
    signed_in: Callable[[TestClient, Role], User],
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = "00" + uuid4().hex[:8]
    workbook = Workbook()
    for row in [
        ["DRX-DELFOR-长期交付预测"],
        ["DelforNo", "000261", "DelforCreateDate", "20261122"],
        ["poNo", "42", "BuyerItemNo", item],
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
        ["1", "1", "900", "C62", "20261218"],
    ]:
        workbook.active.append(row)
    content = BytesIO()
    workbook.save(content)
    mail = EmailMessage()
    mail["From"] = "sanitized@example.com"
    mail["Subject"] = "Delivery dispatch test"
    mail.set_content("Sanitized attachment")
    mail.add_attachment(
        content.getvalue(),
        maintype="application",
        subtype="octet-stream",
        filename="dispatch.xlsx",
    )
    archive = Archive(tmp_path / "archive")
    monkeypatch.setenv("RELAY_ARCHIVE_DIR", str(archive.root))
    monkeypatch.setenv("RELAY_MAIL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_mail_settings.cache_clear()
    request.addfinalizer(get_mail_settings.cache_clear)
    receipt = receive(
        mail.as_bytes(),
        MailIdentity(f"test:{item}", "INBOX", 1, 1, datetime.now(UTC)),
        archive,
    )
    request.addfinalizer(lambda: _remove_test_delivery(receipt.mail_id, item))
    assert process_one(archive)

    smtp = MagicMock()
    smtp.mail.return_value = (250, b"Accepted sender")
    smtp.rcpt.side_effect = [(250, b"ok"), (550, b"rejected"), (250, b"ok"), (250, b"ok")]
    smtp.data.side_effect = [(250, b"ok"), smtplib.SMTPServerDisconnected(), (250, b"ok")]
    connector = MagicMock(return_value=smtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", connector)
    group_ids: list[int] = []
    smtp_address = f"qa-{uuid4().hex[:8]}@example.com"
    test_sender = f"qa-{uuid4().hex[:8]}@163.com"

    def remove_outgoing() -> None:
        with get_engine().begin() as connection:
            connection.execute(
                text("DELETE FROM delivery_recipient_groups WHERE id = ANY(:group_ids)"),
                {"group_ids": group_ids},
            )
            connection.execute(
                text("DELETE FROM outgoing_mailbox WHERE address = ANY(:addresses)"),
                {"addresses": [smtp_address, test_sender]},
            )

    request.addfinalizer(remove_outgoing)
    with TestClient(app, headers=_BROWSER) as admin, TestClient(app, headers=_BROWSER) as viewer:
        signed_in(admin, "admin")
        signed_in(viewer, "viewer")
        attachments = admin.get(f"/api/v1/intake/emails/{receipt.mail_id}").json()["attachments"]
        draft = admin.post(f"/api/v1/delivery/attachments/{attachments[0]['id']}").json()
        path = f"/api/v1/delivery/{draft['id']}"
        releases = f"{path}/releases"
        release_input = {"revision": draft["revision"], "ship_through": "2026-11-27"}
        first_preview = admin.get(f"{path}/release-preview", params=release_input).json()
        assert first_preview["pending_mappings"] == first_preview["pending_shipments"] == 1
        assert first_preview["future_unscheduled"] == 1
        assert first_preview["blockers"][1]["cell"] == "Sheet!C5"
        assert admin.post(releases, json=release_input).json()["detail"] == "release_has_blockers"
        mapping = admin.post(
            "/api/v1/material-mappings",
            json={
                "customer": "drx",
                "customer_item": item,
                "order_no": "42",
                "internal_item": "00024",
            },
        )
        assert mapping.status_code == 201
        refreshed = admin.post(f"{path}/refresh", json={"revision": draft["revision"]}).json()
        first_entry = refreshed["materials"][0]["entries"][0]
        shipment_url = f"{path}/shipments/{first_entry['id']}"
        shipment = {"mode": "air", "ship_date": "2026-11-10", "source_note": "QA"}
        arranged = admin.put(
            shipment_url,
            json={
                "revision": refreshed["revision"],
                "shipment": shipment,
            },
        )
        assert arranged.status_code == 200, arranged.text
        release_input["revision"] = arranged.json()["revision"]
        assert viewer.post(releases, json=release_input).status_code == 403
        confirmed = admin.post(releases, json=release_input)
        assert confirmed.status_code == 201, confirmed.text
        v1 = confirmed.json()
        assert admin.post(releases, json=release_input).json() == v1
        assert v1["version"] == 1 and v1["rule_version"] == "drx-2"
        assert v1["filename"] == "德科交付计划_报文000261_V1.xlsx"
        frozen_url = f"{releases}/1/workbook.xlsx"
        frozen = viewer.get(frozen_url)
        assert (
            frozen.status_code == 200 and "filename*=UTF-8" in frozen.headers["content-disposition"]
        )
        xlsx = load_workbook(BytesIO(frozen.content))
        assert xlsx["给生产"]["A1"].value == "德科交付计划 · V1"
        assert xlsx["给生产"]["L5"].value == 800
        assert xlsx["需求与来源"]["D7"].value == "900"
        assert admin.delete(path, params={"revision": release_input["revision"]}).status_code == 409

        settings_url = "/api/v1/mailbox/outgoing"
        groups_url = "/api/v1/mailbox/recipient-groups"
        payload = {
            "address": smtp_address,
            "host": "smtp.263.net",
            "domains": ["example.com"],
            "password": "qa-only-smtp-secret",
        }
        assert viewer.put(settings_url, json=payload).status_code == 403
        assert admin.get(settings_url).json() is None
        settings = admin.put(settings_url, json=payload)
        assert settings.status_code == 200, settings.text
        assert payload["password"] not in settings.text
        assert admin.post(f"{settings_url}/connection-test").json() == {"result": "ok"}
        smtp.login.assert_called_with(smtp_address, payload["password"])
        assert connector.call_args.args == ("smtp.263.net", 465)
        bad_group = admin.post(
            groups_url,
            json={
                "name": "外部",
                "addresses": ["outside@example.org"],
            },
        )
        assert bad_group.json()["detail"] == "dispatch_address_not_allowed"
        addresses = [f"{letter}@example.com" for letter in "abc"]
        for name, members in (("QA 部门 A", addresses[:2]), ("QA 部门 B", addresses[1:])):
            result = admin.post(groups_url, json={"name": name, "addresses": members})
            assert result.status_code == 201, result.text
            group_ids.append(result.json()["id"])

        dispatches = f"{releases}/1/dispatches"
        options = admin.get(f"{dispatches}/options").json()
        assert options["verified"] is True
        assert viewer.get(dispatches).status_code == 403
        values = {
            "request_id": str(uuid4()),
            "mailbox_revision": options["mailbox_revision"],
            "group_ids": group_ids,
            "addresses": addresses,
            "subject": "德科交付计划 · V1",
            "body": "请查收确认版。",
        }
        mismatched = admin.post(dispatches, json={**values, "addresses": addresses[:1]})
        assert mismatched.json()["detail"] == "dispatch_recipients_changed"
        queued = admin.post(dispatches, json=values)
        assert queued.status_code == 202, queued.text
        assert admin.post(dispatches, json=values).json()["id"] == queued.json()["id"]
        assert (
            admin.post(
                dispatches,
                json={
                    **values,
                    "request_id": str(uuid4()),
                },
            ).json()["detail"]
            == "dispatch_no_recipients"
        )

        changed = admin.put(
            shipment_url,
            json={
                "revision": release_input["revision"],
                "shipment": {**shipment, "manual_eta": "2026-11-25"},
            },
        ).json()
        v2_input = {**release_input, "revision": changed["revision"]}
        assert (
            admin.post(releases, json=v2_input).json()["detail"] == "release_dispatch_in_progress"
        )
        assert delivery_worker.process_one()
        assert delivery_worker.process_one()
        assert delivery_worker.process_one()
        assert not delivery_worker.process_one()
        result = admin.get(dispatches).json()[0]
        assert [
            (item["address"], item["state"], item["retryable"]) for item in result["recipients"]
        ] == [
            (addresses[0], "accepted", False),
            (addresses[1], "failed", True),
            (addresses[2], "uncertain", False),
        ]
        message = message_from_bytes(smtp.data.call_args_list[0].args[0], policy=policy.default)
        (attachment,) = message.iter_attachments()
        assert attachment.get_filename() == v1["filename"]
        assert attachment.get_payload(decode=True) == frozen.content
        assert str(message["To"]) == addresses[0]

        retry_path = f"{dispatches}/{result['id']}/retry"
        retry = {
            "request_id": str(uuid4()),
            "mailbox_revision": options["mailbox_revision"],
            "addresses": [addresses[1]],
        }
        assert (
            admin.post(
                retry_path,
                json={
                    **retry,
                    "addresses": addresses[1:],
                },
            ).json()["detail"]
            == "dispatch_recipients_changed"
        )
        retry_response = admin.post(retry_path, json=retry)
        assert retry_response.status_code == 202, retry_response.text
        assert delivery_worker.process_one()
        assert not delivery_worker.process_one()
        history = admin.get(dispatches).json()
        assert history[0]["recipients"][0]["state"] == "accepted"
        assert history[1]["recipients"][1]["retryable"] is False
        v2 = admin.post(releases, json=v2_input)
        assert v2.status_code == 201, v2.text
        assert v2.json()["version"] == 2
        assert viewer.get(frozen_url).content == frozen.content
        assert viewer.get(f"{releases}/2/workbook.xlsx").content != frozen.content
        assert admin.post(dispatches, json=values).json()["detail"] == "release_superseded"
        second_send = admin.post(
            f"{releases}/2/dispatches",
            json={
                **values,
                "request_id": str(uuid4()),
                "addresses": addresses[:2],
                "group_ids": group_ids[:1],
            },
        )
        assert second_send.status_code == 202, second_send.text
        assert second_send.json()["body"].startswith("本邮件附件 V2 替代 V1")
        test_settings = {
            "address": test_sender,
            "host": "smtp.163.com",
            "domains": [],
            "password": "qa-163-smtp-secret",
            "test_recipient": addresses[2],
        }
        assert (
            admin.put(settings_url, json={**test_settings, "test_recipient": None}).status_code
            == 422
        )
        assert admin.put(settings_url, json=test_settings).status_code == 200
        assert admin.post(f"{settings_url}/connection-test").json() == {"result": "ok"}
        assert connector.call_args.args == ("smtp.163.com", 465)
        assert (
            admin.post(groups_url, json={"name": "非测试地址", "addresses": addresses}).status_code
            == 422
        )
        test_group = admin.post(groups_url, json={"name": "测试地址", "addresses": [addresses[2]]})
        assert test_group.status_code == 201, test_group.text
        group_ids.append(test_group.json()["id"])
        v2_dispatches = f"{releases}/2/dispatches"
        test_options = admin.get(f"{v2_dispatches}/options").json()
        assert (
            test_options["test_mode"]
            and [entry["id"] for entry in test_options["groups"]] == group_ids[-1:]
        )
        first_id = second_send.json()["recipients"][0]["id"]
        with get_engine().begin() as connection:
            connection.execute(
                text("""
                UPDATE delivery_dispatch_recipients SET state = 'sending', lease_until = now()
                WHERE id = :id
            """),
                {"id": first_id},
            )
        assert delivery_worker.process_one()
        assert not delivery_worker.process_one()
        updated_dispatch = admin.get(v2_dispatches).json()[0]
        assert [(entry["state"], entry["detail"]) for entry in updated_dispatch["recipients"]] == [
            ("uncertain", "smtp_worker_interrupted"),
            ("failed", "outgoing_changed"),
        ]
        assert smtp.data.call_count == 3


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
@pytest.mark.parametrize(
    ("template", "customer", "title", "number_key", "date_key", "sheet_name"),
    [
        (
            "kroschu_4905",
            "kroschu",
            "4905 - Delivery Call Off",
            "NewTransmissionNo",
            "TransferDate",
            "按交货日期汇总",
        ),
        ("jlr_delfor", "jlr", "JLR_DELFOR", "DelforNo", "DelforCreateDate", "周需求"),
        ("jlr_deljit", "jlr", "JLR_DELJIT", "DeljitNo", "DeljitCreateDate", "周需求"),
        (
            "vitesco_delfor",
            "vitesco",
            "Vitesco-DELFOR",
            "DelforNo",
            "DelforCreateDate",
            "交货预测周表",
        ),
    ],
)
def test_other_contract_templates_generate_drafts(
    tmp_path: Path,
    signed_in: Callable[[TestClient, Role], User],
    request: pytest.FixtureRequest,
    template: str,
    customer: str,
    title: str,
    number_key: str,
    date_key: str,
    sheet_name: str,
) -> None:
    item = "QA-" + uuid4().hex[:8]
    workbook = Workbook()
    sheet = workbook["Sheet"]
    sheet.append([title])
    header = [number_key, "00052", date_key, "261122"]
    if template == "vitesco_delfor":
        header = [
            number_key,
            "00052",
            date_key,
            "20261122",
            "BlanketOrderNo",
            "00042",
            "DelforType",
            "241",
        ]
    sheet.append(header)
    if template == "kroschu_4905":
        for row in [
            ["OrderNo", "00042", "CustomerItemNo", item, "QuantityUnit", "ST"],
            ["DateTime", "Qty"],
            ["261123", "100"],
            ["261130", "0"],
            ["000000", "0"],
            ["333333", "19"],
        ]:
            sheet.append(row)
    elif template == "vitesco_delfor":
        for row in [
            ["LineItemNo", "1", "BuyerItemNo", item, "ShipToNo", "42"],
            [
                "DeliveryPlanStatusIndicator",
                "QtyType",
                "Qty",
                "QtyUnit",
                "RequestedDeliveryDate",
                "EarliestDeliveryDate",
                "LastDeliveryDate",
            ],
            ["F", "113", "0", "C62", None, "20261123", "20261129"],
            ["F", "113", "5040", "C62", None, "20261207", "20261213"],
        ]:
            sheet.append(row)
    else:
        sheet.append(["poNo", "00042", "BuyerItemNo", item, "POLineNo", "00010"])
        if template == "jlr_delfor":
            sheet.append(["PreviousCumulativeQty"])
            sheet.append(["40087"])
            sheet.append(
                [
                    "Qty",
                    "QtyType",
                    "QtyUnit",
                    "RequestedDeliveryDate",
                    "HorizonStartDate",
                    "HorizonEndDate",
                ]
            )
            sheet.append(["3168", "1", "EA", "261217"])
            sheet.append(["44047", "3", "EA", None, "261221", "261227"])
            sheet.append(["44551", "3", "EA", None, "261228", "270103"])
        else:
            sheet.append(["Qty", "QtyType", "QtyUnit", "RequestedDeliveryDate"])
            sheet.append(["5809", "3", "EA", "261112"])
            sheet.append(["768", "1", "EA", "261112"])
            sheet.append(["912", "1", "EA", "261119"])
    buffer = BytesIO()
    workbook.save(buffer)
    mail = EmailMessage()
    mail["From"] = "sanitized@example.com"
    mail["Subject"] = "Plan generation test"
    mail.set_content("Sanitized attachment")
    mail.add_attachment(
        buffer.getvalue(),
        maintype="application",
        subtype="octet-stream",
        filename="plan-sample.xlsx",
    )
    archive = Archive(tmp_path / "plan-archive")
    received = receive(
        mail.as_bytes(),
        MailIdentity(f"test:{item}", "INBOX", 1, 1, datetime.now(UTC)),
        archive,
    )
    request.addfinalizer(lambda: _remove_test_delivery(received.mail_id, item))
    assert process_one(archive)
    with TestClient(app, headers=_BROWSER) as admin, TestClient(app, headers=_BROWSER) as viewer:
        signed_in(admin, "admin")
        signed_in(viewer, "viewer")
        (attachment,) = viewer.get(f"/api/v1/intake/emails/{received.mail_id}").json()[
            "attachments"
        ]
        assert attachment["can_generate_draft"] and attachment["template"] == template
        assert attachment["job_state"] == "succeeded"
        open_url = f"/api/v1/delivery/attachments/{attachment['id']}"
        assert viewer.post(open_url).status_code == 403
        opened = admin.post(open_url)
        assert opened.status_code == 200, opened.text
        draft = opened.json()
        assert draft["template"] == template and draft["pending_mappings"] == 1
        assert draft["document_no"] == "00052"
        assert admin.post(open_url).json()["id"] == draft["id"]
        assert (
            admin.post(
                "/api/v1/material-mappings",
                json={
                    "customer": customer,
                    "customer_item": item,
                    "order_no": "00042",
                    "internal_item": "00024",
                },
            ).status_code
            == 201
        )
        draft_url = f"/api/v1/delivery/{draft['id']}"
        refreshed = admin.post(f"{draft_url}/refresh", json={"revision": draft["revision"]})
        assert refreshed.status_code == 200, refreshed.text
        result = refreshed.json()
        assert result["pending_mappings"] == 0
        assert result["materials"][0]["mapping"]["internal_item"] == "00024"
        download = viewer.get(f"{draft_url}/draft.xlsx", params={"revision": result["revision"]})
        assert download.status_code == 200, download.text
        export = load_workbook(BytesIO(download.content))
        plan = export[sheet_name]
        assert plan["A5"].value == "00024" and plan["C5"].value == "00042"
        assert export["需求与来源"]["S4"].value
        if template == "kroschu_4905":
            assert [plan[f"{column}5"].value for column in "HI"] == [100, 0]
            assert plan["H4"].value == "=H3-98"
            assert result["pending_entries"] == 1
            assert export["待核对"]["I4"].value == "333333"
        elif template == "jlr_delfor":
            assert [plan[f"{column}5"].value for column in "HIJ"] == [3168, 792, 504]
            assert result["pending_entries"] == 0
        elif template == "jlr_deljit":
            assert [plan[f"{column}5"].value for column in "HI"] == [768, 912]
            assert export["需求与来源"]["L4"].value == "5809"
        else:
            assert [plan[f"{column}5"].value for column in "HIJ"] == [0, None, 5040]
            assert export["库存测算"]["F7"].value == '=IF(COUNT(E7,F5,F6)=3,E7-F5+F6,"")'
