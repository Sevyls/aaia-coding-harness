Allocating an order line with a quantity of zero or less is currently accepted and even
increases the available stock of the batch. Reject such allocations by raising an exception
from the allocation flow, and make sure the batch's available quantity does not change.

The HTTP endpoint POST /allocate must answer such a request with status 400 and a JSON body
with a "message", like it already does for an unknown SKU, instead of an internal server error.
