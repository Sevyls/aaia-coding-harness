"""Acceptance check for task 2 (tasks/negative-batch.md).

Two levels: the message bus (commands.CreateBatch -> handlers.add_batch -> domain model) and the
HTTP API (POST /add_batch -> flask_app -> message bus), both on in-memory fakes (conftest.py).
Must fail on commit 14c8479 and pass after the fix. The agent never sees this file.
"""

import pytest

from allocation.domain import commands


def create(client, ref, qty):
    return client.post("/add_batch", json={"ref": ref, "sku": "LAMP", "qty": qty, "eta": None})


def test_negative_batch_quantity_is_rejected_and_nothing_is_stored(bus):
    # The task does not prescribe an exception type: any rejection counts.
    with pytest.raises(Exception):
        bus.handle(commands.CreateBatch("b1", "LAMP", -5))

    assert bus.uow.products.get_by_batchref("b1") is None


@pytest.mark.parametrize("qty", [0, 10])
def test_zero_and_positive_batch_quantity_is_still_accepted(bus, qty):
    bus.handle(commands.CreateBatch("b1", "LAMP", qty))

    assert bus.uow.products.get("LAMP").batches[0].available_quantity == qty


def test_api_rejects_negative_batch_quantity_with_400_and_message(api):
    client, bus = api

    response = create(client, "b1", -5)

    assert response.status_code == 400, f"expected 400, got {response.status_code}"
    assert response.get_json()["message"], "the error response needs a message"
    assert bus.uow.products.get_by_batchref("b1") is None


def test_api_still_creates_a_valid_batch(api):
    client, bus = api

    assert create(client, "b1", 10).status_code == 201
    assert bus.uow.products.get("LAMP").batches[0].available_quantity == 10
