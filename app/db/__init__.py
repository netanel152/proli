"""Data-layer modules production runs: the index table and the backup job.

Restructure phase 3 moved both out of ``scripts/`` so that ``app/`` never
imports or runs ``scripts/`` (pinned by ``tests/test_architecture_layers.py``).
"""
