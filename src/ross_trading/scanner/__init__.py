"""Ross-trading Scanner package.

Phase 2 — Section 3.1 hard-filter pipeline. This package will grow to
contain (roughly in this order):

* ``filters`` — pure-function primitives for the five Section 3.1 hard
  filters plus the soft news signals (Atom A1, this module).
* ``ranking`` — top-N selector by % gain (A2).
* ``scanner`` — orchestrator that composes filters + ranking (A2).
* ``loop`` — async tick driver (A3).

Atoms are introduced one PR at a time so each is independently reviewable.
"""
