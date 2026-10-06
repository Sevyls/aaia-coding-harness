"""In-memory fakes shared by the acceptance checks of all tasks.

The harness runs each task's directory with --rootdir /acceptance, so this file is loaded.
The agent never sees these files.
"""

import importlib
import sys

import pytest

from allocation import bootstrap
from allocation.adapters import notifications, repository
from allocation.service_layer import unit_of_work


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


@pytest.fixture
def bus():
    """The real message bus and handlers on in-memory fakes."""
    return bootstrap.bootstrap(
        start_orm=False,
        uow=FakeUnitOfWork(),
        notifications=FakeNotifications(),
        publish=lambda *args: None,
    )


@pytest.fixture
def api(monkeypatch, bus):
    """The real Flask app, wired to the fake bus instead of Postgres and Redis."""
    monkeypatch.setattr(bootstrap, "bootstrap", lambda *args, **kwargs: bus)
    sys.modules.pop("allocation.entrypoints.flask_app", None)
    flask_app = importlib.import_module("allocation.entrypoints.flask_app")
    yield flask_app.app.test_client(), bus
    sys.modules.pop("allocation.entrypoints.flask_app", None)
