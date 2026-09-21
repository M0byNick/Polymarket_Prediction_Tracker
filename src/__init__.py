"""Shared infrastructure for the Polymarket trader-portfolio project.

All production code imports from here. Research notebooks in ``notebooks/``
and one-shot scripts can reuse these modules instead of reimplementing
clients, DB context managers, loggers, or the slug classifier.

Modules
-------
log
    Structured, unbuffered logging helper.
db
    SQLite WAL connection context manager with additive migrations.
polymarket_client
    Narrow wrappers around Gamma + CLOB endpoints with a shared rate
    limiter.
classifier
    Slug-prefix classifier (302 prefixes → canonical category).
paper_schema
    DDL + migrations for the Stage 4 paper-trading tables.
"""
