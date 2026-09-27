Allocating an order line with a quantity of zero or less is currently accepted and even
increases the available stock of the batch. Reject such allocations: the allocate handler
must raise a new exception handlers.InvalidQuantity (similar to InvalidSku), and the
batch's available quantity must not change.
