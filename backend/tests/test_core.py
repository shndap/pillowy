from datetime import date, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.main import (
    Intake,
    InventoryTransaction,
    Medication,
    NotificationLog,
    Schedule,
    SessionLocal,
    User,
    app,
    record_schedule,
    supply_warning,
)


@pytest.fixture(autouse=True)
def clean_database():
    session = SessionLocal()
    for model in (NotificationLog, InventoryTransaction, Intake, Schedule, Medication, User):
        session.execute(delete(model))
    session.commit()
    session.close()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def add_medication(client, name="Magnesium", inventory=10):
    response = client.post("/medications", json={"name": name, "inventory": inventory})
    assert response.status_code == 200
    medication_id = response.json()["id"]
    response = client.post(
        f"/medications/{medication_id}/schedules",
        json={"period": "Morning", "at": "08:00:00", "quantity": 2},
    )
    assert response.status_code == 200
    return medication_id, response.json()["id"]


def test_take_dose_decreases_inventory_and_creates_intake(client):
    medication_id, schedule_id = add_medication(client)

    response = client.post(f"/schedules/{schedule_id}/take")

    assert response.status_code == 200
    assert response.json() == {"recorded": True}
    meds = client.get("/medications").json()
    assert meds[0]["id"] == medication_id
    assert meds[0]["inventory"] == 8
    assert len(client.get("/history").json()) == 1


def test_repeated_take_is_idempotent(client):
    _, schedule_id = add_medication(client)

    assert client.post(f"/schedules/{schedule_id}/take").json() == {"recorded": True}
    assert client.post(f"/schedules/{schedule_id}/take").json() == {"recorded": False}
    assert client.get("/medications").json()[0]["inventory"] == 8
    assert len(client.get("/history").json()) == 1


def test_take_all_only_records_outstanding_schedules(client):
    medication_id = client.post("/medications", json={"name": "Vitamin D", "inventory": 5}).json()["id"]
    first = client.post(f"/medications/{medication_id}/schedules", json={"period": "Morning", "at": "08:00:00", "quantity": 1}).json()["id"]
    second = client.post(f"/medications/{medication_id}/schedules", json={"period": "Morning", "at": "08:05:00", "quantity": 2}).json()["id"]

    client.post(f"/schedules/{first}/take")
    response = client.post("/today/take-all?period=Morning")

    assert response.json()["recorded"] == 1
    assert client.get("/medications").json()[0]["inventory"] == 2
    assert len(client.get("/history").json()) == 2


def test_insufficient_inventory_rolls_back(client):
    _, schedule_id = add_medication(client, inventory=1)

    response = client.post(f"/schedules/{schedule_id}/take")

    assert response.status_code == 409
    assert client.get("/medications").json()[0]["inventory"] == 1
    assert client.get("/history").json() == []


def test_purchase_increases_inventory(client):
    medication_id, _ = add_medication(client, inventory=3)

    response = client.post(f"/inventory/{medication_id}/purchase", json={"quantity": 30})

    assert response.json() == {"inventory": 33}


def test_scheduler_requires_secret(client):
    assert client.post("/internal/scheduler/tick", headers={"X-Scheduler-Secret": "wrong"}).status_code == 403


def test_scheduler_sends_seven_day_supply_reminder_once(client):
    medication_id = client.post("/medications", json={"name": "Omega 3", "inventory": 7}).json()["id"]
    client.post(f"/medications/{medication_id}/schedules", json={"period": "Morning", "at": "00:00:00", "quantity": 1})
    headers = {"X-Scheduler-Secret": "change-me"}

    first = client.post("/internal/scheduler/tick", headers=headers)
    second = client.post("/internal/scheduler/tick", headers=headers)

    assert first.status_code == 200
    assert first.json()["processed"] == 1
    assert second.json()["processed"] == 0
    session = SessionLocal()
    try:
        assert session.query(NotificationLog).filter_by(notification_type="LOW_STOCK").count() == 1
    finally:
        session.close()


def test_warning_uses_total_daily_quantity_for_two_daily_doses(client):
    medication_id = client.post("/medications", json={"name": "Magnesium", "inventory": 20}).json()["id"]
    client.post(f"/medications/{medication_id}/schedules", json={"period": "Morning", "at": "08:00:00", "quantity": 1})
    client.post(f"/medications/{medication_id}/schedules", json={"period": "Evening", "at": "20:00:00", "quantity": 1})
    session = SessionLocal()
    try:
        medication = session.get(Medication, medication_id)
        medication.inventory = 14
        session.commit()
        assert supply_warning(session, medication) == "Magnesium has 14 pills left — about 7 days of supply."
    finally:
        session.close()
