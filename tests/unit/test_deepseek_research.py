"""The provider boundary: what is accepted, what is rejected, and what is worth asking twice.

The retry tests carry most of the weight here. A client that retries everything eventually turns
a model that keeps citing evidence it was never given into a model that gets away with it once,
and that single lucky response is the one that lands in the evidence file.
"""