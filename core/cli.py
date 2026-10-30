"""``khavis`` console script — init / doctor / serve / --version.

This is the entry point behind ``pip install khavis``. It exists because
the packaging metadata used to point at ``scripts.start:main``, which is
not importable from an installed wheel (``scripts/`` is not packaged and
``start.py`` never existed) — every ``khavis ...`` invocation crashed.

Subcommands match what README.md / INSTALLATION.md already document:

    khavis --version
    khavis init      # write starter config/ files (never overwrites)
    khavis doctor    # check Python, config, plugin discovery
    khavis chat "…"  # send one prompt through the router (no server needed)
    khavis health    # probe pools, show circuit-breaker / quota state
    khavis serve     # run the OpenAI-compatible HTTP API
"""

from __future__ import annotations

import argparse
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

VERSION = "0.5.0"

# Minimal starter configs written by ``khavis init``. Pools here are the
# BYOK ones; keys come from .env / process env. Anything missing a key is
# loaded but marked unhealthy, so a fresh install still boots.
_INIT_POOLS = """\
# Starter pool config written by `khavis init`.
# Fill in API keys in .env (see .env.example); pools whose env var is
# unset are loaded but marked unhealthy and skipped by the router.
pools:
  - name: "OpenAI-API"
    provider_id: "openai"
    endpoint: "https://api.openai.com/v1"
    model: "gpt-4o-mini"
    env_key: "OPENAI_API_KEY"
  - name: "Claude-API"
    provider_id: "claude"
    endpoint: "https://api.anthropic.com"
    model: "claude-sonnet-4-5"
    env_key: "ANTHROPIC_API_KEY"
  - name: "Google-Gemini"
    provider_id: "gemini"
    endpoint: "https://generativelanguage.googleapis.com"
    models: ["gemini-2.5-flash", "gemini-2.5-pro"]
    env_key: "GOOGLE_API_KEY"
  - name: "Ollama-Mac"
    provider_id: "ollama"
    endpoint: "http://127.0.0.1:11434"
    model: "llama3.2"
"""

_INIT_CAPS = """\
# Starter capability routing written by `khavis init`.
# Weights are relative; the router does weighted-random selection and
# falls back to the registry's own capability tags if a tag is missing.
capabilities:
  - capability: "general"
    pools: ["OpenAI-API", "Claude-API", "Google-Gemini"]
    weight: 1.0
  - capability: "code"
    pools: ["Claude-API", "OpenAI-API", "Google-Gemini"]
    weight: 1.0
  - capability: "local"
    pools: ["Ollama-Mac"]
    weight: 1.0
"""

_INIT_AGENTS = """\
# Starter multi-agent role config (copy to config/agents.yaml to enable).
# Each role names a capability tag the capability-router resolves to a pool.
roles:
  planner:
    capability_required: "general"
  coder:
    capability_required: "code"
  critic:
    capability_required: "code"
"""


def _cmd_init(args: argparse.Namespace) -> int:
    config_dir = Path(args.config_dir) if args.config_dir else Path.cwd() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    written = []
    skipped = []
    for name, body in (
        ("pools.yaml", _INIT_POOLS),
        ("capabilities.yaml", _INIT_CAPS),
        ("agents.yaml.example", _INIT_AGENTS),
    ):
        target = config_dir / name
        if target.exists():
            skipped.append(str(target))
            continue
        target.write_text(body, encoding="utf-8")
        written.append(str(target))

    for path in written:
        print(f"[init] wrote {path}")
    for path in skipped:
        print(f"[init] kept existing {path}")
    print("[init] done. Next: fill in .env, then run `khavis doctor`.")
    return 0


_AUTH_FAILURE_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "authorized_error",
    "invalid_api_key",
    "invalid api key",
    "permission denied",
    "authentication",
)


def _looks_like_auth_failure(detail: str) -> bool:
    """Did the provider refuse our credential, as opposed to being down?

    ``health_check`` across the plugins reports *whether a key is present*, not
    whether the provider accepts it. A rotated or expired key therefore looks
    exactly like a working one until a real request comes back 401 — which is
    the single most confusing state this project can be in, because every
    status command says the pool is configured.
    """
    low = detail.lower()
    return any(m in low for m in _AUTH_FAILURE_MARKERS)


def _auth_hint(detail: str) -> str:
    """A short, actionable reason — never the credential itself."""
    low = detail.lower()
    if "invalid_api_key" in low or "invalid api key" in low:
        return "provider reports the key is invalid"
    if "401" in detail or "unauthorized" in low or "authorized_error" in low:
        return "401 — key rejected (expired, wrong account, or wrong env var)"
    if "403" in detail or "permission denied" in low:
        return "403 — key valid but lacks access to this model"
    return "credential rejected"


def _explain_upstream_failure(pool: str, detail: str) -> str:
    """Turn a raw provider error into one line the operator can act on.

    Upstream bodies carry request ids, nested JSON and sometimes a fragment of
    the request. Printing all of it buries the one fact that matters and makes
    the terminal hard to scan — the 401 above rendered as four lines of nested
    JSON with no indication that it just meant "rotate the key".
    """
    if _looks_like_auth_failure(detail):
        return f"{_auth_hint(detail)} — fix the key for {pool} in .env"
    if "429" in detail or "quota" in detail.lower():
        return "out of quota — the router will fail over to another pool on the next request"
    if isinstance(detail, str) and len(detail) > 200:
        return detail[:200] + "…"
    return detail


def _cmd_doctor(args: argparse.Namespace) -> int:
    ok = True

    # 1. Python version
    v = sys.version_info
    py_ok = v >= (3, 11)
    ok &= py_ok
    print(f"[{'ok' if py_ok else 'FAIL'}] python {v.major}.{v.minor}.{v.micro} (need 3.11+)")

    # 2. .env / config presence (informational — not a failure)
    from core.env import config_dir, find_env_file, load_env  # noqa: PLC0415

    env_file = find_env_file()
    print(
        f"[{'ok' if env_file else 'note'}] .env {'found at ' + str(env_file) if env_file else 'missing (BYOK pools will stay unhealthy)'}"
    )
    # Load .env *before* any health_check runs. Without this every plugin
    # probes against an empty os.environ and reports key_present=False, so a
    # fully-configured install reports "0 pools ready" while its keys sit
    # unused in the very file it just located. core.env deliberately does not
    # auto-load on import (it would pollute tests), so a CLI entry point has to
    # ask for it.
    if env_file:
        load_env(env_file)
    cfg_dir = config_dir()
    for name in ("pools.yaml", "capabilities.yaml"):
        p = cfg_dir / name
        cfg_ok = p.exists()
        ok &= cfg_ok
        print(f"[{'ok' if cfg_ok else 'FAIL'}] {p}")

    # 3. Plugin discovery (plugins ship with the package; YAML may live elsewhere)
    try:
        # Same loader `chat` uses — two copies of this is how the two commands
        # drifted apart in the first place.
        reg, _router = _load_router(cfg_dir)
        n = len(reg)
        disc_ok = n > 0
        ok &= disc_ok
        print(f"[{'ok' if disc_ok else 'FAIL'}] discovered {n} pools")
        if reg.errors():
            for e in reg.errors():
                print(f"       warn: {e}")

        # Partition before printing. A wall of 24 identical "unhealthy" lines
        # carries no information — the useful question is "what CAN I use?",
        # so the ready pools lead and the rest are summarised by count with
        # the actionable ones (missing key) named explicitly.
        ready: list[tuple[Any, str]] = []
        bad_key: list[tuple[str, str]] = []
        missing_key: list[str] = []
        broken: list[tuple[str, str]] = []
        for plug in reg:
            try:
                health = plug.health_check()
                if not isinstance(health, dict):
                    health = {"ok": bool(health)}
            except Exception as exc:  # noqa: BLE001 - provider code is untrusted
                health = {"ok": False, "detail": f"health_check raised: {exc!r}"}
            detail = str(health.get("detail") or "")
            if health.get("ok"):
                ready.append((plug, detail))
                continue
            if "key_pres" in detail and "False" in detail:
                missing_key.append(plug.name)
                continue
            # A key that is *present but rejected* is the case that wasted the
            # most time here: health_check only reports presence, so a rotated
            # or expired key looked identical to a working one until a real
            # request came back 401.
            if _looks_like_auth_failure(detail):
                bad_key.append((plug.name, _auth_hint(detail)))
            else:
                broken.append((plug.name, detail[:70]))

        print(f"[{'ok' if ready else 'FAIL'}] {len(ready)} pool(s) ready to use")
        for plug, detail in ready:
            print(f"       + {plug.name} {('(' + detail + ')') if detail else ''}")

        if args.deep and ready:
            # health_check is a pure config check — it never touches the
            # network, so it cannot see an expired key, a spent quota or a
            # retired model. Those only show up on a real call.
            _deep_verify(ready, reg)

        if bad_key:
            print(f"[FAIL] {len(bad_key)} pool(s) rejected their API key:")
            for name, hint in bad_key:
                print(f"       - {name}: {hint}")
            print("       the key exists but the provider refused it — rotate it in .env")
        if missing_key:
            print(
                f"[note] {len(missing_key)} pool(s) loaded but have no API key in .env: "
                f"{', '.join(missing_key)}"
            )
            print("       add the key to .env (see .env.example) to enable them")
        if broken:
            print(f"[warn] {len(broken)} pool(s) failing for other reasons:")
            for name, detail in broken:
                print(f"       - {name}: {detail}")
        if not ready:
            # A report that ends "all checks passed" while nothing is usable
            # is the most misleading thing this command can print.
            print(
                "[doctor] config is valid but NO pool can serve a request yet.\n"
                "         fix the credential or endpoint problems above, then re-run\n"
                "         `khavis doctor`."
            )
            ok = False
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[FAIL] plugin discovery: {exc!r}")

    print("[doctor]", "all checks passed" if ok else "some checks failed")
    return 0 if ok else 1


def _cmd_health(args: argparse.Namespace) -> int:
    """Probe every pool and show circuit-breaker / quota state.

    The tracker is in-process state: this command starts with a fresh,
    empty tracker, so it probes every pool once by default (``--no-refresh``
    shows the empty cold-start view). It cannot inspect or reset the
    tracker of a separately running ``khavis serve`` process.
    """
    try:
        from core.env import config_dir, find_env_file, load_env  # noqa: PLC0415
        from core.health import get_tracker, load_policy  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"health module unavailable: {exc!r}", file=sys.stderr)
        return 1

    if find_env_file():
        load_env()

    cfg_dir = config_dir()
    reg, _router = _load_router(cfg_dir)

    from core.health import get_tracker, load_policy  # noqa: PLC0415

    tracker = get_tracker(load_policy(cfg_dir / "health.yaml"))

    if args.reset:
        for plug in reg:
            tracker.force_close(plug.name)
        print(f"[health] reset {len(reg)} pool(s) in this process\n")

    if args.refresh:
        print(f"[health] probing {len(reg)} pool(s) ...\n")
        tracker.refresh_all(list(reg))
    else:
        for plug in reg:
            tracker.get(plug.name)  # list every pool, even unprobed (UNKNOWN)

    snap = tracker.snapshot()
    if not snap:
        print("[health] no pools discovered")
        return 1

    width = max((len(name) for name in snap), default=10)
    print(f"{'POOL'.ljust(width)}  {'STATE':<10} {'QUOTA':>14}  LAST ERROR")
    print("-" * (width + 60))
    for name, info in snap.items():
        remaining = info.get("quota_remaining")
        total = info.get("quota_total")
        quota = "n/a" if remaining is None else f"{remaining}/{total if total is not None else '?'}"
        err = (info.get("last_error") or "")[:48]
        print(f"{name.ljust(width)}  {info['state']:<10} {quota:>14}  {err}")

    unavailable = [n for n, i in snap.items() if i["state"] in {"open", "exhausted"}]
    if unavailable:
        print(
            f"\n[health] {len(unavailable)} pool(s) failing their probe: {', '.join(unavailable)}"
        )
        print(
            "[health] fix the cause (API key in .env, endpoint, quota) and re-run `khavis health`."
        )
    print(
        "[health] note: circuit state is per-process. This view comes from probes "
        "run just now; a running `khavis serve` keeps its own tracker (it refreshes "
        "in the background) and is not affected by --reset here."
    )
    return 0


def _load_router(cfg_dir: Path):
    """Build a registry + capability router from config, or explain why not."""
    from core.capability_router import CapabilityRouter  # noqa: PLC0415
    from core.env import _project_root  # noqa: PLC0415
    from core.registry import PluginRegistry  # noqa: PLC0415

    reg = PluginRegistry(_project_root()).discover()
    with suppress(Exception):  # config may not exist yet
        reg.apply_pools_config(cfg_dir / "pools.yaml")
    return reg, CapabilityRouter(reg)


def _deep_verify(ready: list[tuple[Any, str]], reg: Any) -> None:
    """Make one minimal real call per ready pool and report what actually works.

    ``health_check()`` only asserts "a key string, a URL string and a model
    name are configured" — it never opens a socket. So a pool whose key was
    rotated, whose quota is spent, or whose model has been retired all pass it,
    and the operator is told the pool is ready right up until a real request
    fails. That is exactly how 10 pools were reported as ready when only 3
    were.

    This costs one small call per pool, so it is opt-in (``--deep``). The
    prompt is fixed and tiny to keep the cost near zero.
    """
    print(f"\n[deep] calling {len(ready)} pool(s) with a minimal prompt...")
    verified: list[str] = []
    failed: list[tuple[str, str]] = []
    for plug, _detail in ready:
        try:
            # No max_tokens on purpose. Setting a small budget makes
            # reasoning models (gpt-5-mini, gemini-flash) spend the whole
            # allowance on internal reasoning and return an empty content
            # string, which raise_if_empty turns into "this pool is broken".
            # Measured on this install: Google-Gemini and OpenAI-API both fail
            # with max_tokens=16 and both answer fine without it. A one-word
            # prompt is cheap enough not to need a cap.
            result = plug.chat([{"role": "user", "content": "ok"}])
        except Exception as exc:  # noqa: BLE001 - upstream errors are the result
            failed.append((plug.name, _explain_upstream_failure(plug.name, str(exc))))
            continue
        choices = result.get("choices") or []
        if not choices:
            failed.append((plug.name, "returned no choices"))
            continue
        verified.append(plug.name)

    print(f"[deep] {len(verified)} pool(s) verified working")
    for name in verified:
        print(f"       ✓ {name}")
    if failed:
        print(f"[deep] {len(failed)} pool(s) passed health_check but cannot serve:")
        for name, why in failed:
            print(f"       ✗ {name}: {why}")
        print("[deep] fix or remove these — they will fail every real request.")


def _cmd_chat(args: argparse.Namespace) -> int:
    """Send one prompt through the router and print the reply.

    This is the shortest path from "I have a prompt" to "I have an answer".
    Before this existed the only ways in were a Python script or a running
    HTTP server plus curl — which meant the router could not be tried at all
    without starting a daemon first.
    """
    from core.env import config_dir, find_env_file, load_env  # noqa: PLC0415

    env_file = find_env_file()
    if not env_file:
        print(
            "[chat] no .env found — every BYOK pool will be skipped.\n"
            "       run `khavis init` then fill in config/pools.yaml + .env",
            file=sys.stderr,
        )
    else:
        # Same reason as in doctor: probes and the router read os.environ, so
        # this has to happen before either runs.
        load_env(env_file)

    cfg_dir = config_dir()
    try:
        reg, router = _load_router(cfg_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[chat] could not load config: {exc!r}", file=sys.stderr)
        return 1

    if args.refresh:
        with suppress(Exception):
            from core.health import get_tracker, load_policy  # noqa: PLC0415

            get_tracker(load_policy(cfg_dir / "health.yaml")).refresh_all(list(reg))

    messages = [{"role": "user", "content": args.prompt}]

    from core.failover import NoHealthyPool, call_with_failover  # noqa: PLC0415

    pinned = None
    if args.pool:
        pinned = reg.get(args.pool)
        if pinned is None:
            print(
                f"[chat] unknown pool {args.pool!r}. Known: {', '.join(p.name for p in reg)}",
                file=sys.stderr,
            )
            return 1

    try:
        # Goes through the breaker, so a dead pool is recorded and skipped
        # rather than surfacing its raw error. This is the same path the HTTP
        # API takes; calling plugin.chat() directly — as this did — meant the
        # CLI inherited none of mechanism 1, so an expired key on the first
        # choice ended the request even with nine other pools healthy.
        plugin, result, attempts = call_with_failover(
            router,
            args.capability,
            messages,
            pinned=pinned,
            max_attempts=args.max_attempts,
        )
    except NoHealthyPool as exc:
        print(f"[chat] {exc}", file=sys.stderr)
        print("       run `khavis doctor` to see which pools are failing.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - upstream errors are expected here
        detail = str(exc)
        print(
            f"[chat] failed after trying {args.max_attempts} pool(s): "
            f"{_explain_upstream_failure('', detail)}",
            file=sys.stderr,
        )
        print(
            "       run `khavis doctor` for the per-pool reason.",
            file=sys.stderr,
        )
        return 1

    if args.json:
        import json  # noqa: PLC0415

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    choices = result.get("choices") or []
    text = ""
    if choices:
        message = choices[0].get("message") or {}
        text = str(message.get("content") or "")
    print(text or "(empty response)")

    if not args.quiet:
        usage = result.get("usage") or {}
        meta = f"pool={plugin.name} attempts={attempts}"
        if result.get("model"):
            meta += f" model={result['model']}"
        if usage:
            meta += f" tokens={usage.get('total_tokens', '?')}"
        print(f"\n-- {meta}", file=sys.stderr)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        from server.openai_api import main as serve_main  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"khavis serve requires the server extra: {exc}", file=sys.stderr)
        print("Install with: pip install 'khavis[bot]'  (fastapi + uvicorn)", file=sys.stderr)
        return 1
    # server.openai_api.main() parses --host/--port itself.
    sys.argv = ["khavis-serve"]
    if args.host:
        sys.argv += ["--host", args.host]
    if args.port:
        sys.argv += ["--port", str(args.port)]
    serve_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    # Fail fast and loudly. On Python < 3.10 the symptoms are cryptic and
    # misleading — `zip(strict=...)` raises "zip() takes no keyword arguments"
    # and pydantic raises "Unable to evaluate type annotation 'int | None'",
    # both of which look like application bugs rather than a version mismatch.
    if sys.version_info < (3, 11):  # noqa: UP036
        print(
            f"K.H.A.V.I.S. requires Python 3.11+, but you are running "
            f"{sys.version_info.major}.{sys.version_info.minor}.\n"
            f"  fix: brew install python@3.12\n"
            f"       python3.12 -m venv .venv && source .venv/bin/activate\n"
            f"       pip install -e '.[dev]'",
            file=sys.stderr,
        )
        return 1

    parser = argparse.ArgumentParser(
        prog="khavis",
        description="K.H.A.V.I.S. — plugin-based router for multiple LLM providers.",
    )
    parser.add_argument("--version", action="version", version=f"khavis {VERSION}")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="write starter config/ files (never overwrites)")
    p_init.add_argument(
        "--config-dir", default=None, help="target config directory (default: project config/)"
    )
    p_init.set_defaults(func=_cmd_init)

    p_doc = sub.add_parser("doctor", help="check Python, config and plugin discovery")
    p_doc.add_argument(
        "--deep",
        action="store_true",
        help=(
            "also make one minimal real call per ready pool. health_check only "
            "checks configuration, so expired keys, spent quota and retired "
            "models all pass it — this is what catches them. Costs one tiny "
            "request per pool."
        ),
    )
    p_doc.set_defaults(func=_cmd_doctor)

    p_chat = sub.add_parser("chat", help="send one prompt through the router")
    p_chat.add_argument("prompt", help="the prompt to send")
    p_chat.add_argument(
        "--pool",
        default=None,
        help="pin a specific pool (default: route by capability)",
    )
    p_chat.add_argument(
        "--capability",
        default="推理",
        help="capability tag to route by when --pool is not given (default: 推理)",
    )
    p_chat.add_argument(
        "--json",
        action="store_true",
        help="print the raw provider response instead of just the text",
    )
    p_chat.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the trailing pool/model/token line on stderr",
    )
    p_chat.add_argument(
        "--max-attempts",
        type=int,
        default=4,
        help="how many pools to try before giving up (default: 4)",
    )
    p_chat.add_argument(
        "--no-refresh",
        dest="refresh",
        action="store_false",
        help="skip the up-front pool health probe (faster, may pick a dead pool)",
    )
    p_chat.set_defaults(func=_cmd_chat, refresh=True)

    p_serve = sub.add_parser("serve", help="run the OpenAI-compatible HTTP API")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.set_defaults(func=_cmd_serve)

    p_health = sub.add_parser("health", help="probe pools and show circuit-breaker / quota state")
    p_health.add_argument(
        "--refresh",
        dest="refresh",
        action="store_true",
        default=True,
        help="probe every pool before printing (default; kept for compatibility)",
    )
    p_health.add_argument(
        "--no-refresh",
        dest="refresh",
        action="store_false",
        help="skip probing (shows this process's empty cold-start state)",
    )
    p_health.add_argument(
        "--reset",
        action="store_true",
        help="force-close every circuit in this process's tracker before probing",
    )
    p_health.set_defaults(func=_cmd_health)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
