"""geniefy-v3 agent core — UI-free reasoning library (D1).

The agent that turns a Unity Catalog table into AI/Genie-ready comments. The
Databricks App (U5) and the deferred batch Job wrap this same library; the core
holds no session state in memory and runs no I/O itself (D17) — providers and
clients are injected.

Components (LLD-agent-core.md): Template · Profiler · ContextGatherer · Reasoner
· Judge · Gate, sequenced by the DocumentationOrchestrator. Built incrementally
as build-phase units land; `Profiler` is the first (U14).
"""
from __future__ import annotations

from geniefy_core.profiler import (
    DEFAULT_PROFILE_BATCH_SIZE,
    MIN_SUPPORTED_PROFILE_VERSION,
    PROFILE_SCHEMA_VERSION,
    ColumnProfile,
    ForeignKey,
    Profiler,
    ProfileError,
    ProfileOptions,
    ProfileProvider,
    ProfileRequest,
    ProfileResult,
    SampleSpec,
    SchemaIntrospector,
    SchemaMeta,
    TableProfile,
    TableProfileMeta,
    TableRef,
    information_schema_queries,
)

__all__ = [
    "PROFILE_SCHEMA_VERSION",
    "MIN_SUPPORTED_PROFILE_VERSION",
    "DEFAULT_PROFILE_BATCH_SIZE",
    "Profiler",
    "ProfileResult",
    "ProfileError",
    "ProfileRequest",
    "ProfileOptions",
    "SampleSpec",
    "TableRef",
    "TableProfile",
    "TableProfileMeta",
    "ColumnProfile",
    "SchemaMeta",
    "ForeignKey",
    "ProfileProvider",
    "SchemaIntrospector",
    "information_schema_queries",
]
