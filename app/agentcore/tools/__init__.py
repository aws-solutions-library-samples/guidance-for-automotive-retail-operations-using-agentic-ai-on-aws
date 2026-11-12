"""Per-agent tool adapters for the AgentCore runtimes (ADR 0009).

One module per agent (`a1.py` … `a7.py`). Each imports only its own agent's
service modules, so an agent's runtime bundle carries no other agent's code.
The runtime dispatcher resolves the adapter named by `registry.ToolSpec.target`
after it has validated the arguments and checked roles and scope.

Adapter signature: `fn(claims, env_prefix, *, journey=None, **declared_args) -> dict`.
`journey` is the DEALER_Journeys row the dispatcher loaded for its scope check
(journey-scoped tools only).
"""
