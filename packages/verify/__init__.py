"""The verification agent: when a call reaches INTERVENE, go and check.

RingFence's other tiers observe and score. This one *acts* -- it opens a
conversation with the institution the caller claims to represent and asks
whether the call was genuine.

That makes it the only part of the system with an outward blast radius, so
it is built guard-first: ``agent.may_open_session`` ships before any
conversation logic exists, and ``tests/invariants/test_dry_run.py`` proves a
dry-run or replay can never open one.
"""
