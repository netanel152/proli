---
description: Generate a Gemini token usage report per professional (FinOps)
allowed-tools: Bash(python:*), Bash(venv/Scripts/python.exe:*), Read
---

Generate a token usage report for professionals.

Run the FinOps script with the project venv interpreter:
`venv/Scripts/python.exe scripts/finops_report.py` (Windows) or `venv/bin/python scripts/finops_report.py` (POSIX).

This will display a table of all professionals and their total Gemini tokens consumed, sorted by highest usage. Useful for monitoring API costs and pro activity.
