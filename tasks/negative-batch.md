Creating a stock batch with a negative quantity is currently accepted. Reject such a batch by
raising an exception from the batch creation flow, and make sure no batch is stored.
A quantity of zero or more stays valid.

The HTTP endpoint POST /add_batch must answer such a request with status 400 and a JSON body
with a "message" instead of an internal server error.
