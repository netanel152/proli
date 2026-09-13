"""Static call-arity guard for the Streamlit admin panel.

Why this is a *static* check rather than a normal unit test:

`admin_panel/views/*.py` render through Streamlit, so nothing in the unit
suite ever executes them — every other admin test targets the injectable
query/label/refresh seams underneath. A view function called with the wrong
number of arguments therefore passes CI, passes `black`, passes `flake8`
(which does not resolve call targets), and only fails when an operator opens
the page. Importing the module is not enough either: `tests/test_admin_kanban.py`
already imports `admin_panel.views.home` without rendering it, and a bad call
inside a function body raises at *call* time, not import time.

That is exactly how PRO-188 shipped: `_render_pending_review_strip(T)` was
called as `_render_pending_review_strip(T, pro_names, pro_map_name_to_id)`,
and the whole Dashboard tab died on staging with
``TypeError: _render_pending_review_strip() takes 1 positional argument but
3 were given``.

This module parses each admin-panel file and checks every call it can resolve
against the target's signature — both same-module calls and calls to names
imported from elsewhere inside `admin_panel/` (PRO-188 also changed
`admin_panel/ui/components.py` signatures, which the views call across module
boundaries). It imports nothing from the panel, so it needs neither Streamlit
nor Mongo nor `.env` — it is pure `ast`.

Deliberate limits (a miss here is acceptable, a false positive is not):

* Only calls by bare name are checked. Attribute calls (`module.f()`,
  `self.f()`) and anything reached through a variable are skipped.
* Calls using ``*args`` / ``**kwargs`` unpacking are skipped — their arity is
  not knowable statically.
* If a module-level function's name is rebound anywhere else in that file — a
  parameter, a local, a nested ``def``, a loop target, an ``except ... as`` —
  every call to that name is skipped rather than guessed at. No such rebinding
  exists in the panel today; this is what keeps the guard from crying wolf if
  one appears.
* Decorators are ignored. The only decorated panel functions are
  `@st.cache_data`, which preserves the signature; a signature-altering
  decorator would need excluding here.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_PANEL_DIR = REPO_ROOT / "admin_panel"

_FUNC_DEF = (ast.FunctionDef, ast.AsyncFunctionDef)


def _admin_panel_files():
    """Every .py file shipped in the admin panel, sorted for stable output."""
    return sorted(ADMIN_PANEL_DIR.rglob("*.py"))


def _module_level_functions(tree):
    """Map name -> def node for functions defined at module level only."""
    return {node.name: node for node in tree.body if isinstance(node, _FUNC_DEF)}


def _rebound_names(tree):
    """Names the module binds by any means other than a module-level `def`.

    Used to *exclude* call sites, not to flag them: if `foo` is both a
    module-level function and a parameter somewhere, we cannot tell which one
    a given `foo(...)` means without real scope analysis, so we check neither.
    """
    # A module-level `def` or `import` is the *canonical* binding — the thing
    # this guard resolves calls against — so neither counts as shadowing. Every
    # other binding form does, including an import inside a function body.
    canonical = {
        node
        for node in tree.body
        if isinstance(node, _FUNC_DEF + (ast.Import, ast.ImportFrom))
    }
    bound = set()

    for node in ast.walk(tree):
        if node in canonical:
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, _FUNC_DEF):
            bound.add(node.name)  # a nested def shadows the module-level one
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)

    return bound


def _admin_panel_imports(tree):
    """Map local name -> (dotted module, original name) for panel imports.

    Only `from admin_panel.x import y` is resolved. Relative imports and
    `import admin_panel.x` (whose calls are attribute calls anyway) are left
    alone.
    """
    imported = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level or not node.module:
            continue
        if node.module != "admin_panel" and not node.module.startswith("admin_panel."):
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            imported[alias.asname or alias.name] = (node.module, alias.name)
    return imported


def _disk_resolver():
    """Resolve (dotted module, name) to a def node by parsing the panel file.

    Returns a closure so the parse cache lives for one caller, not forever.
    """
    _cache = {}

    def resolve(dotted, name):
        if dotted not in _cache:
            path = REPO_ROOT / Path(*dotted.split("."))
            source_path = path.with_suffix(".py")
            if not source_path.is_file():
                source_path = path / "__init__.py"
            if not source_path.is_file():
                _cache[dotted] = {}
            else:
                tree = ast.parse(source_path.read_text(encoding="utf-8"))
                rebound = _rebound_names(tree)
                _cache[dotted] = {
                    n: fn
                    for n, fn in _module_level_functions(tree).items()
                    if n not in rebound
                }
        return _cache[dotted].get(name)

    return resolve


def _describe_signature(fn):
    """Render a def's parameter list the way a reader would write it."""
    args = fn.args
    parts = [a.arg for a in list(args.posonlyargs) + list(args.args)]
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    parts.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return f"{fn.name}({', '.join(parts)})"


def _incompatibility(fn, call):
    """Return a human-readable reason `call` cannot bind `fn`, else None."""
    args = fn.args
    positional = list(args.posonlyargs) + list(args.args)

    # Dynamic unpacking: arity is not statically knowable. Skip, don't guess.
    if any(isinstance(a, ast.Starred) for a in call.args):
        return None
    if any(kw.arg is None for kw in call.keywords):
        return None

    n_positional_given = len(call.args)
    if n_positional_given > len(positional) and args.vararg is None:
        return (
            f"passes {n_positional_given} positional argument(s) but the "
            f"function accepts {len(positional)}"
        )

    accepted = {a.arg for a in positional} | {a.arg for a in args.kwonlyargs}
    positional_only = {a.arg for a in args.posonlyargs}
    supplied = {a.arg for a in positional[:n_positional_given]}

    for kw in call.keywords:
        if kw.arg in supplied:
            return f"passes a duplicate value for {kw.arg!r}"
        if kw.arg in positional_only and args.kwarg is None:
            return f"passes positional-only {kw.arg!r} as a keyword"
        if kw.arg not in accepted and args.kwarg is None:
            return f"passes unknown keyword {kw.arg!r}"
        supplied.add(kw.arg)

    required = [a.arg for a in positional[: len(positional) - len(args.defaults)]]
    required += [
        a.arg
        for a, default in zip(args.kwonlyargs, args.kw_defaults)
        if default is None
    ]
    missing = [name for name in required if name not in supplied]
    if missing:
        return "omits required " + ", ".join(repr(m) for m in missing)

    return None


def _violations_in_source(source, label="<source>", resolve=None):
    """Collect every resolvable call whose arity cannot bind its def."""
    tree = ast.parse(source)
    rebound = _rebound_names(tree)
    local = {
        name: fn
        for name, fn in _module_level_functions(tree).items()
        if name not in rebound
    }
    imported = {
        name: target
        for name, target in _admin_panel_imports(tree).items()
        if name not in rebound
    }

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue

        fn = local.get(node.func.id)
        origin = ""
        if fn is None and node.func.id in imported:
            if resolve is None:
                continue
            dotted, original = imported[node.func.id]
            fn = resolve(dotted, original)
            origin = f" in {dotted}"
        if fn is None:
            continue

        reason = _incompatibility(fn, node)
        if reason:
            found.append(
                f"{label}:{node.lineno}: call to {node.func.id}() {reason} "
                f"— defined at line {fn.lineno}{origin} as {_describe_signature(fn)}"
            )
    return found


# --- The guard itself -------------------------------------------------------


def test_admin_panel_calls_match_their_signatures():
    """No admin-panel call may disagree with the def it targets."""
    resolve = _disk_resolver()
    violations = []
    for path in _admin_panel_files():
        violations.extend(
            _violations_in_source(
                path.read_text(encoding="utf-8"),
                label=path.relative_to(REPO_ROOT).as_posix(),
                resolve=resolve,
            )
        )

    assert not violations, (
        "Admin-panel call(s) cannot bind their target signature. Streamlit "
        "views are not executed by the unit suite, so this only surfaces as a "
        "TypeError in the operator's browser:\n  " + "\n  ".join(violations)
    )


def test_pending_review_strip_is_called_with_only_the_translations():
    """PRO-188 regression, named so a repeat points straight at the cause.

    The strip builds its own pro list from `APPROVED_PRO_FILTER`; it must not
    be handed the board's `pro_names` / `pro_map_name_to_id`, which are built
    from an unfiltered `users_collection.find()` and keyed by a `business_name`
    that self-onboarding leaves blank.
    """
    source = (ADMIN_PANEL_DIR / "views" / "home.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_render_pending_review_strip"
    ]

    assert calls, "the pending-review strip is no longer called from home.py"
    for call in calls:
        assert len(call.args) == 1 and not call.keywords, (
            f"home.py:{call.lineno}: _render_pending_review_strip() takes only "
            f"`T`, got {len(call.args)} positional argument(s)"
        )


# --- Self-tests: a static checker that matches nothing would pass forever ---


def test_checker_flags_the_pro_188_shape():
    """Too many positional arguments — the exact bug that reached staging."""
    violations = _violations_in_source(
        "def _render_strip(T):\n"
        "    return T\n"
        "\n"
        "def view(T, pro_names, pro_map):\n"
        "    _render_strip(T, pro_names, pro_map)\n"
    )
    assert len(violations) == 1
    assert "passes 3 positional argument(s)" in violations[0]
    assert "accepts 1" in violations[0]


def test_checker_flags_missing_and_unknown_arguments():
    cases = {
        "def f(a, b):\n    pass\n\ndef g():\n    f(1)\n": "omits required 'b'",
        "def f(a):\n    pass\n\ndef g():\n    f(1, nope=2)\n": "unknown keyword",
        "def f(a):\n    pass\n\ndef g():\n    f(1, a=2)\n": "duplicate value",
    }
    for source, expected in cases.items():
        violations = _violations_in_source(source)
        assert len(violations) == 1, source
        assert expected in violations[0], source


def test_checker_accepts_every_legitimate_call_shape():
    """Defaults, keywords, *args, **kwargs and unpacking must all pass."""
    sources = [
        "def f(a, b=1):\n    pass\n\ndef g():\n    f(1)\n",
        "def f(a, b=1):\n    pass\n\ndef g():\n    f(1, b=2)\n",
        "def f(a, *rest):\n    pass\n\ndef g():\n    f(1, 2, 3, 4)\n",
        "def f(a, **kw):\n    pass\n\ndef g():\n    f(1, anything=2)\n",
        "def f(a, *, k):\n    pass\n\ndef g():\n    f(1, k=2)\n",
        "def f(a, *, k=None):\n    pass\n\ndef g():\n    f(1)\n",
        # Unpacking is skipped rather than guessed at.
        "def f(a):\n    pass\n\ndef g(args):\n    f(*args)\n",
        "def f(a):\n    pass\n\ndef g(kw):\n    f(**kw)\n",
        # A call to something this module did not define is not our business.
        "def g():\n    st.markdown('hi')\n",
    ]
    for source in sources:
        assert _violations_in_source(source) == [], source


def test_checker_skips_names_that_are_rebound_somewhere_in_the_file():
    """A shadowed name is ambiguous, so it is skipped — never guessed at."""
    shadowing_sources = [
        # parameter shadows the module-level def
        "def f(a):\n    pass\n\ndef g(f):\n    f(1, 2)\n",
        # nested def shadows it
        "def f(a):\n    pass\n\ndef g():\n    def f(x, y):\n        pass\n    f(1, 2)\n",
        # plain local rebinding
        "def f(a):\n    pass\n\ndef g(other):\n    f = other\n    f(1, 2)\n",
        # loop target
        "def f(a):\n    pass\n\ndef g(items):\n    for f in items:\n        f(1, 2)\n",
    ]
    for source in shadowing_sources:
        assert _violations_in_source(source) == [], source


def test_checker_resolves_calls_imported_from_the_panel():
    """Cross-module calls are checked, not silently skipped."""
    target = ast.parse("def render_thing(a, b):\n    pass\n")
    defs = _module_level_functions(target)

    def resolve(dotted, name):
        assert dotted == "admin_panel.ui.components"
        return defs.get(name)

    source = (
        "from admin_panel.ui.components import render_thing\n"
        "\n"
        "def view():\n"
        "    render_thing(1, 2, 3)\n"
    )
    violations = _violations_in_source(source, resolve=resolve)
    assert len(violations) == 1
    assert "in admin_panel.ui.components" in violations[0]
    assert "passes 3 positional argument(s)" in violations[0]

    ok = source.replace("render_thing(1, 2, 3)", "render_thing(1, 2)")
    assert _violations_in_source(ok, resolve=resolve) == []


def test_disk_resolver_finds_a_real_panel_function():
    """The default resolver must actually reach files, not quietly return None."""
    resolve = _disk_resolver()
    fn = resolve("admin_panel.ui.components", "render_kanban_column")
    assert fn is not None, "resolver could not load a known components function"
    assert [a.arg for a in fn.args.args] == ["status", "leads", "T"]
    assert resolve("admin_panel.ui.components", "no_such_function") is None


def test_guard_actually_inspects_the_panel():
    """Cheap proof the file list and parser are not silently empty."""
    files = _admin_panel_files()
    assert len(files) >= 15, f"only found {len(files)} admin-panel files"

    home = ADMIN_PANEL_DIR / "views" / "home.py"
    tree = ast.parse(home.read_text(encoding="utf-8"))
    functions = _module_level_functions(tree)
    assert "_render_pending_review_strip" in functions
    assert "view_leads_dashboard" in functions

    # home.py must genuinely import panel functions, or the cross-module half
    # of this guard is checking nothing on the file that broke.
    assert _admin_panel_imports(tree), "home.py imports nothing from admin_panel"
