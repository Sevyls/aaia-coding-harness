"""Acceptance check for the Stage 1 task (tasks/invalid-quantity.md).

Runs through the message bus: commands.Allocate -> handlers.allocate -> domain model.
Must fail on commit 14c8479 and pass after the fix. The agent never sees this file.
"""

import pytest

from allocation import bootstrap
from allocation.adapters import notifications, repository
from allocation.domain import commands
from allocation.service_layer import handlers, unit_of_work


class FakeRepository(repository.AbstractRepository):
    def __init__(self):
        super().__init__()
        self._products = set()

    def _add(self, product):
        self._products.add(product)

    def _get(self, sku):
        return next((p for p in self._products if p.sku == sku), None)

    def _get_by_batchref(self, batchref):
        return next((p for p in self._products for b in p.batches if b.reference == batchref), None)


class FakeUnitOfWork(unit_of_work.AbstractUnitOfWork):
    def __init__(self):
        self.products = FakeRepository()

    def _commit(self):
        pass

    def rollback(self):
        pass


class FakeNotifications(notifications.AbstractNotifications):
    def send(self, destination, message):
        pass


def make_bus():
    return bootstrap.bootstrap(
        start_orm=False,
        uow=FakeUnitOfWork(),
        notifications=FakeNotifications(),
        publish=lambda *args: None,
    )


@pytest.mark.parametrize("qty", [0, -5])
def test_non_positive_quantity_is_rejected_and_stock_unchanged(qty):
    bus = make_bus()
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))

    assert hasattr(handlers, "InvalidQuantity"), "handlers.InvalidQuantity does not exist"
    with pytest.raises(handlers.InvalidQuantity):
        bus.handle(commands.Allocate("o1", "LAMP", qty))

    assert bus.uow.products.get("LAMP").batches[0].available_quantity == 10


def test_positive_quantity_still_allocates():
    bus = make_bus()
    bus.handle(commands.CreateBatch("b1", "LAMP", 10))

    bus.handle(commands.Allocate("o1", "LAMP", 3))

    assert bus.uow.products.get("LAMP").batches[0].available_quantity == 7
