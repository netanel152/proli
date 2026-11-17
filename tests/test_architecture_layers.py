"""Dependency-direction rules for the codebase (docs/RESTRUCTURE_PLAN.md §4, phase 0).

A pure-``ast`` check: nothing is imported or executed. Each rule carries an
exception table holding the violations that exist **today**. The tables are a
ratchet. A new violation fails the build, and so does an exception that no
longer occurs: when a later change removes one, its row is deleted in the same
PR, so a table can only shrink.

The rules:

1. ``app.core`` never imports ``app.services``, ``app.providers``,
   ``app.api``, ``app.scheduler``, ``app.worker`` or ``app.main``. Core is
   infrastructure; it knows nothing about leads or pros.
2. ``app.providers`` never imports ``app.services`` or the runtime entry points.
3. ``app.services`` never imports the runtime entry points (``app.api``,
   ``app.scheduler``, ``app.worker``, ``app.main``, ``app.core.arq_worker``).
4. ``app/`` never imports ``admin_panel`` and never imports or runs ``scripts/``.
   ``scripts/`` imports ``app/``, never the reverse.
5. ``admin_panel`` imports from ``app.services`` only through an allowlist,
   and never imports the runtime entry points.
6. A first-party (``app.*``) import inside a function needs a ``# cycle:``
   comment on its line saying which import cycle it breaks. Third-party lazy
   imports (``sentry_sdk``, ``subprocess``...) are not policed.

Exceptions are keyed by (file, imported module) with a count, not by line
number, so unrelated edits that move a line do not touch this file.
"""

import ast
import re
from collections import Counter
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

_RUNTIME = ("app.api", "app.scheduler", "app.worker", "app.main")


def _files(top: str) -> list[Path]:
    return sorted(p for p in (_REPO_ROOT / top).rglob("*.py") if "venv" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _under(name: str, prefixes) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


class _Import:
    __slots__ = ("module", "in_function", "lineno")

    def __init__(self, module: str, in_function: bool, lineno: int):
        self.module = module
        self.in_function = in_function
        self.lineno = lineno


def _imports(path: Path) -> list[_Import]:
    """Every absolute import in ``path``, flagged when it sits inside a function.

    ``from app.services import workflow_service`` is reported as
    ``app.services.workflow_service``: the module actually loaded.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[_Import] = []

    def visit(node, depth):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                visit(child, depth + 1)
                continue
            if isinstance(child, ast.Import):
                for alias in child.names:
                    found.append(_Import(alias.name, depth > 0, child.lineno))
            elif isinstance(child, ast.ImportFrom) and not child.level and child.module:
                for alias in child.names:
                    found.append(
                        _Import(f"{child.module}.{alias.name}", depth > 0, child.lineno)
                    )
            visit(child, depth)

    visit(tree, 0)
    return found


def _module_of(name: str) -> str:
    """Trim a ``from X import name`` record back to the module ``X`` when
    ``name`` is an attribute rather than a submodule, so the exception tables
    key on modules. Resolved against the filesystem: the longest dotted prefix
    that is a ``.py`` file or a package wins."""
    parts = name.split(".")
    while len(parts) > 1:
        candidate = _REPO_ROOT.joinpath(*parts)
        if (
            candidate.with_suffix(".py").is_file()
            or (candidate / "__init__.py").is_file()
        ):
            return ".".join(parts)
        parts.pop()
    return name


def _violations(tops, predicate) -> Counter:
    hits: Counter = Counter()
    for top in tops:
        for path in _files(top):
            for imp in _imports(path):
                if predicate(_rel(path), imp):
                    hits[(_rel(path), _module_of(imp.module))] += 1
    return hits


def _assert_matches(actual: Counter, allowed: dict, rule: str) -> None:
    allowed = Counter(allowed)
    new = {k: v for k, v in actual.items() if v > allowed.get(k, 0)}
    stale = {k: v for k, v in allowed.items() if actual.get(k, 0) < v}
    msg = []
    if new:
        msg.append(
            f"{rule}: new violation(s) — fix the import, don't widen the table:\n"
            + "\n".join(
                f"  {f} imports {m} (x{n})" for (f, m), n in sorted(new.items())
            )
        )
    if stale:
        msg.append(
            f"{rule}: exception(s) no longer occur — shrink the table in this PR:\n"
            + "\n".join(
                f"  {f} -> {m}: allowed {n}, found {actual.get((f, m), 0)}"
                for (f, m), n in sorted(stale.items())
            )
        )
    assert not msg, "\n\n".join(msg)


# ---------------------------------------------------------------------------
# Rule 1 — app.core is infrastructure
# ---------------------------------------------------------------------------

_CORE_FORBIDDEN = ("app.services", "app.providers") + _RUNTIME

# arq_worker is the worker's entry wiring living in core; phase 3 moves it to
# app/worker/ and empties this table.
_CORE_EXCEPTIONS = {
    ("app/core/arq_worker.py", "app.scheduler"): 1,
    ("app/core/arq_worker.py", "app.services.workflow_service"): 3,
}


def test_core_does_not_import_upper_layers():
    actual = _violations(
        ["app/core"], lambda f, imp: _under(imp.module, _CORE_FORBIDDEN)
    )
    _assert_matches(actual, _CORE_EXCEPTIONS, "app.core -> upper layer")


# ---------------------------------------------------------------------------
# Rule 2 / 3 — providers and services don't reach up
# ---------------------------------------------------------------------------


def test_providers_do_not_import_services_or_runtime():
    actual = _violations(
        ["app/providers"],
        lambda f, imp: _under(imp.module, ("app.services",) + _RUNTIME),
    )
    _assert_matches(actual, {}, "app.providers -> services/runtime")


def test_services_do_not_import_runtime_entry_points():
    actual = _violations(
        ["app/services"],
        lambda f, imp: _under(imp.module, _RUNTIME + ("app.core.arq_worker",)),
    )
    _assert_matches(actual, {}, "app.services -> runtime")


# ---------------------------------------------------------------------------
# Rule 4 — app/ never depends on admin_panel/ or scripts/
# ---------------------------------------------------------------------------

# Emptied by restructure phase 3 (the index table moved to app/db/indexes.py).
_APP_IMPORTS_SCRIPTS_EXCEPTIONS: dict = {}


def test_app_does_not_import_admin_panel():
    actual = _violations(["app"], lambda f, imp: _under(imp.module, ("admin_panel",)))
    _assert_matches(actual, {}, "app -> admin_panel")


def test_app_does_not_import_scripts():
    actual = _violations(["app"], lambda f, imp: _under(imp.module, ("scripts",)))
    _assert_matches(actual, _APP_IMPORTS_SCRIPTS_EXCEPTIONS, "app -> scripts")


_SCRIPT_PATH = re.compile(r"scripts(/[\w./-]+\.(py|sh))?")

# Emptied by restructure phase 3: the nightly backup used to run
# scripts/backup.py by file path; it now runs `python -m app.db.backup`.
_APP_RUNS_SCRIPTS_EXCEPTIONS: dict = {}


def test_app_does_not_run_scripts_by_path():
    """A string literal that *is* a path into scripts/ (not prose that
    mentions one): exactly ``"scripts"`` or ``"scripts/<file>.py|.sh"``."""
    hits: Counter = Counter()
    for path in _files("app"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _SCRIPT_PATH.fullmatch(node.value)
            ):
                hits[(_rel(path), "scripts")] += 1
    _assert_matches(hits, _APP_RUNS_SCRIPTS_EXCEPTIONS, "app runs scripts/")


# ---------------------------------------------------------------------------
# Rule 5 — admin_panel reaches app.services only through an allowlist
# ---------------------------------------------------------------------------

# Reusing a service is what keeps the panel from duplicating logic (the
# one-click assignment goes through admin_flow.assign_lead_to_pro), so this is
# an allowlist of modules, not a ban. Adding one is a reviewed decision.
_ADMIN_SERVICE_ALLOWLIST = {
    "app.services.admin_flow",
    "app.services.cloudinary_client_service",
    "app.services.context_manager_service",
    "app.services.geocoding_service",
    "app.services.lead_manager_service",
    "app.services.matching_service",
    "app.services.state_manager_service",
}


def test_admin_panel_imports_only_allowlisted_services():
    used = set()
    stray = []
    for path in _files("admin_panel"):
        for imp in _imports(path):
            if _under(imp.module, ("app.services",)):
                module = _module_of(imp.module)
                used.add(module)
                if module not in _ADMIN_SERVICE_ALLOWLIST:
                    stray.append(f"  {_rel(path)}:{imp.lineno} imports {module}")
    assert (
        not stray
    ), "admin_panel imports a service outside the allowlist:\n" + "\n".join(stray)
    unused = _ADMIN_SERVICE_ALLOWLIST - used
    assert not unused, f"allowlisted but no longer imported — remove: {sorted(unused)}"


def test_admin_panel_does_not_import_runtime_entry_points():
    actual = _violations(
        ["admin_panel"],
        lambda f, imp: _under(imp.module, _RUNTIME + ("app.core.arq_worker",)),
    )
    _assert_matches(actual, {}, "admin_panel -> runtime")


# ---------------------------------------------------------------------------
# Rule 6 — a first-party import inside a function says which cycle it breaks
# ---------------------------------------------------------------------------

# Today's function-level first-party imports. Most of dispatch_guards' are the
# ``workflow_service as wf`` service-locator cycle (plan §2.2); several others
# break no cycle at all (plan §2.6). New code either imports at module level or
# marks the line ``# cycle: <which>``.
_INNER_IMPORT_EXCEPTIONS = {
    ("app/api/routes/health.py", "app.core.database"): 1,
    ("app/core/text_matching.py", "app.core.messages"): 1,
    ("app/providers/whatsapp/cloud_api.py", "app.providers.whatsapp.delivery"): 1,
    ("app/providers/whatsapp/delivery.py", "app.providers.whatsapp"): 1,
    ("app/providers/whatsapp/quality.py", "app.providers.whatsapp"): 1,
    ("app/scheduler.py", "app.core.logger"): 2,
    ("app/scheduler.py", "app.core.redis_client"): 4,
    ("app/scheduler.py", "app.core.sentry"): 2,
    ("app/services/admin_flow.py", "app.services.matching_service"): 1,
    ("app/services/conversation_pipeline.py", "app.services.monitor_service"): 1,
    ("app/services/conversation_pipeline.py", "app.services.workflow_service"): 1,
    ("app/services/customer_flow.py", "app.services.monitor_service"): 2,
    ("app/services/dispatch_guards.py", "app.core.redis_client"): 1,
    ("app/services/dispatch_guards.py", "app.services.admin_flow"): 1,
    ("app/services/dispatch_guards.py", "app.services.workflow_service"): 22,
    ("app/services/geocoding_service.py", "app.core.sync_bridge"): 1,
    ("app/services/pro_flow.py", "app.core.database"): 1,
    ("app/services/pro_flow.py", "app.core.messages"): 1,
    ("app/services/pro_flow.py", "app.services.monitor_service"): 2,
    ("app/services/workflow_service.py", "app.services.notification_service"): 1,
}

_CYCLE_MARK = re.compile(r"#\s*cycle:")


def test_function_level_first_party_imports_are_marked_or_known():
    hits: Counter = Counter()
    for path in _files("app"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for imp in _imports(path):
            if not imp.in_function or not _under(imp.module, ("app",)):
                continue
            if _CYCLE_MARK.search(lines[imp.lineno - 1]):
                continue
            hits[(_rel(path), _module_of(imp.module))] += 1
    _assert_matches(hits, _INNER_IMPORT_EXCEPTIONS, "function-level app.* import")


# ---------------------------------------------------------------------------
# The checker itself
# ---------------------------------------------------------------------------


def test_checker_sees_function_level_and_module_level_imports(tmp_path):
    src = tmp_path / "probe.py"
    src.write_text(
        "import os\n"
        "from app.core import config\n"
        "def f():\n"
        "    from app.services import workflow_service as wf\n"
        "    async def g():\n"
        "        import app.scheduler\n"
    )
    got = [(i.module, i.in_function) for i in _imports(src)]
    assert got == [
        ("os", False),
        ("app.core.config", False),
        ("app.services.workflow_service", True),
        ("app.scheduler", True),
    ]


@pytest.mark.parametrize(
    "name, module",
    [
        (
            "app.services.workflow_service.process_incoming_message",
            "app.services.workflow_service",
        ),
        ("app.providers.whatsapp.get_whatsapp", "app.providers.whatsapp"),
        ("scripts.create_indexes.create_all_indexes", "scripts.create_indexes"),
        ("app.core.database", "app.core.database"),
    ],
)
def test_module_of_strips_attributes(name, module):
    assert _module_of(name) == module


@pytest.mark.parametrize(
    "value, is_path",
    [
        ("scripts", True),
        ("scripts/backup.py", True),
        ("scripts/cleanup_worktrees.sh", True),
        ("template's status with scripts/submit_whatsapp_templates.py ", False),
        ("seed scripts", False),
    ],
)
def test_script_path_literal_detection(value, is_path):
    assert bool(_SCRIPT_PATH.fullmatch(value)) is is_path
