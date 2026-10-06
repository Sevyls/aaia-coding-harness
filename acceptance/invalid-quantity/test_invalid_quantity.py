"""Acceptance check for task 1 (tasks/invalid-quantity.md).

Two levels: the message bus (commands.Allocate -> handlers.allocate -> domain model) and the
HTTP API (POST /allocate -> flask_app -> message bus), both on in-memory fakes (conftest.py).
Must fail on commit 14c8479 and pass after the fix. The agent never sees this file.
"""

import pytest

from allocation.domain import commands


@pytest.mark.parametrize("qty", [0, -5])
def test_non_positive_quantity_is_rejected_and_stock_unchanged(bus, qty):
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))

    # The task does not prescribe an exception type: any rejection counts.
    with pytest.raises(Exception):
        bus.handle(commands.Allocate("o1", "LAMP", qty))

    assert bus.uow.products.get("LAMP").batches[0].available_quantity == 10


def test_positive_quantity_still_allocates(bus):
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))

    bus.handle(commands.Allocate("o1", "LAMP", 3))

    assert bus.uow.products.get("LAMP").batches[0].available_quantity == 7


def allocate(client, qty):
    return client.post("/allocate", json={"orderid": "o1", "sku": "LAMP", "qty": qty})


@pytest.mark.parametrize("qty", [0, -5])
def test_api_rejects_non_positive_quantity_with_400_and_message(api, qty):
    client, bus = api
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))

    response = allocate(client, qty)

    assert response.status_code == 400, f"expected 400, got {response.status_code}"
    assert response.get_json()["message"], "the error response needs a message"
    assert bus.uow.products.get("LAMP").batches[0].available_quantity == 10


def test_api_still_allocates_positive_quantity(api):
    client, bus = api
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))

    assert allocate(client, 3).status_code == 202
    assert bus.uow.products.get("LAMP").batches[0].available_quantity == 7
