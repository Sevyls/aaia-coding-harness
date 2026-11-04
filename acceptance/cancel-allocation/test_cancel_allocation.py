"""Acceptance check for the Stage 2 feature (tasks/cancel-allocation.toml).

Two levels: the message bus (commands.Deallocate -> handler -> domain model) and the HTTP API
(POST /deallocate -> flask_app -> message bus), both on in-memory fakes (conftest.py).
Must fail on commit 14c8479 and pass after the change. The agent never sees this file.
"""

from datetime import date

import pytest

from allocation.domain import commands


def deallocate_command():
    # Looked up at run time so the starting code fails here, not with an ImportError at collection.
    Deallocate = getattr(commands, "Deallocate", None)
    assert Deallocate is not None, "allocation.domain.commands has no Deallocate command"
    return Deallocate


def batch(bus, ref, sku="LAMP"):
    return next(b for b in bus.uow.products.get(sku).batches if b.reference == ref)


def test_deallocate_frees_the_stock(bus):
    Deallocate = deallocate_command()
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))
    bus.handle(commands.Allocate("o1", "LAMP", 3))

    bus.handle(Deallocate("o1", "LAMP"))

    assert batch(bus, "b1").available_quantity == 10
    assert batch(bus, "b1").allocated_quantity == 0, "the line is still allocated"


def test_freed_stock_is_not_reallocated_to_another_batch(bus):
    # Emitting the existing events.Deallocated would trigger handlers.reallocate.
    Deallocate = deallocate_command()
    bus.handle(commands.CreateBatch("in-stock", "LAMP", 10))
    bus.handle(commands.CreateBatch("shipment", "LAMP", 10, date(2030, 1, 1)))
    bus.handle(commands.Allocate("o1", "LAMP", 3))
    assert batch(bus, "in-stock").available_quantity == 7

    bus.handle(Deallocate("o1", "LAMP"))

    assert batch(bus, "in-stock").available_quantity == 10
    assert batch(bus, "shipment").available_quantity == 10


@pytest.mark.parametrize(
    "orderid, sku",
    [("o2", "LAMP"), ("o1", "NOPE"), ("o1", "TABLE")],
    ids=["unknown-order", "unknown-sku", "order-not-allocated-for-sku"],
)
def test_unknown_order_or_sku_is_rejected_and_stock_unchanged(bus, orderid, sku):
    Deallocate = deallocate_command()
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))
    bus.handle(commands.CreateBatch("t1", "TABLE", 5))
    bus.handle(commands.Allocate("o1", "LAMP", 3))

    # The task does not prescribe an exception type: any rejection counts.
    with pytest.raises(Exception):
        bus.handle(Deallocate(orderid, sku))

    assert batch(bus, "b1").available_quantity == 7
    assert batch(bus, "t1", "TABLE").available_quantity == 5


def deallocate(client, orderid, sku):
    return client.post("/deallocate", json={"orderid": orderid, "sku": sku})


def test_api_deallocates_with_200(api):
    client, bus = api
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))
    bus.handle(commands.Allocate("o1", "LAMP", 3))

    response = deallocate(client, "o1", "LAMP")

    assert response.status_code == 200, f"expected 200, got {response.status_code}"
    assert batch(bus, "b1").available_quantity == 10


@pytest.mark.parametrize("orderid, sku", [("o2", "LAMP"), ("o1", "NOPE")], ids=["unknown-order", "unknown-sku"])
def test_api_rejects_unknown_order_or_sku_with_400_and_message(api, orderid, sku):
    client, bus = api
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))
    bus.handle(commands.Allocate("o1", "LAMP", 3))

    response = deallocate(client, orderid, sku)

    assert response.status_code == 400, f"expected 400, got {response.status_code}"
    assert response.get_json()["message"], "the error response needs a message"
    assert batch(bus, "b1").available_quantity == 7


def test_api_still_allocates(api):
    client, bus = api
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))

    response = client.post("/allocate", json={"orderid": "o1", "sku": "LAMP", "qty": 3})

    assert response.status_code == 202, f"expected 202, got {response.status_code}"
    assert batch(bus, "b1").available_quantity == 7
