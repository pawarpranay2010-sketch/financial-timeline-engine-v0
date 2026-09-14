"""
Platrixa — minimal CLI (Phase 12, section 7)
============================================

    python -m platrixa process input.json
    python -m platrixa process input.json --provider local
    python -m platrixa process input.json --rules pack.yaml
    python -m platrixa process input.json --hook mypkg.rules:MyHook
    python -m platrixa process --text "Purchased furniture for cash ₹15,000" --json

The CLI follows the same path as every other consumer:

    CLI → public API (platrixa.Platrixa.process) → Kernel.process

It contains NO accounting, validation, grounding, or rule logic of its own.
Output is machine-readable JSON (``PlatrixaResult.to_dict()``, which
serializes the Kernel result deterministically).

Exit codes are a deterministic view of the Kernel state:

    0 — VERIFIED
    1 — REVIEW_REQUIRED
    2 — BLOCKED
    3 — any fail-closed failure state (MODEL_UNAVAILABLE, VALIDATION_FAILED,
        GROUNDING_FAILED, FORBIDDEN_OUTPUT, UNSUPPORTED_TRANSACTION) or an
        InputError/ProviderError/configuration error

No interactive shell, daemon, auth, server, or configuration wizard.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

EXIT_VERIFIED = 0
EXIT_REVIEW_REQUIRED = 1
EXIT_BLOCKED = 2
EXIT_FAILURE = 3


def _load_input(args: argparse.Namespace) -> str:
    """
    Resolve the transaction input from --text or a JSON file.

    The JSON file may be a bare string (a single transaction) or an object
    with a ``raw_input`` field — mirroring the HTTP API's request contract.
    No other shapes are accepted; there is no competing input schema.
    """
    if args.text is not None:
        return args.text
    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            payload: Any = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: cannot read input file {args.input!r}: {exc}")
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        raw = payload.get("raw_input")
        if isinstance(raw, str):
            return raw
        raise SystemExit(
            'error: JSON object input must contain a string field "raw_input"'
        )
    raise SystemExit(
        "error: JSON input must be a transaction string or an object with "
        'a "raw_input" field'
    )


def _build_config(args: argparse.Namespace) -> Any:
    """Translate CLI flags into a PlatrixaConfig (configuration only)."""
    from platrixa import PlatrixaConfig

    hooks: List[Any] = []
    for spec in args.hook or []:
        hooks.append(_resolve_hook(spec))
    return PlatrixaConfig(
        provider=args.provider,
        rule_pack=args.rules,
        rule_hooks=tuple(hooks),
    )


def _resolve_hook(spec: str) -> Any:
    """
    Resolve ``module.path:ClassName`` to a hook instance via importlib.

    No eval/exec; construction errors propagate (fail closed).
    """
    import importlib

    if ":" not in spec:
        raise SystemExit(
            f"error: --hook must be 'module.path:ClassName', got {spec!r}"
        )
    module_name, _, class_name = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
        hook_cls = getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise SystemExit(f"error: cannot resolve hook {spec!r}: {exc}")
    try:
        return hook_cls()
    except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
        raise SystemExit(f"error: cannot construct hook {spec!r}: {exc}")


def _run(args: argparse.Namespace) -> int:
    from platrixa import Platrixa, PlatrixaError

    raw_input = _load_input(args)
    client = Platrixa(config=_build_config(args))
    try:
        result = client.process(raw_input)
    except PlatrixaError as exc:
        print(
            json.dumps(
                {
                    "status": "INPUT_ERROR"
                    if type(exc).__name__ == "InputError"
                    else "PROVIDER_ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return EXIT_FAILURE

    payload: Dict[str, Any] = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))

    if result.status == "VERIFIED":
        return EXIT_VERIFIED
    if result.status == "REVIEW_REQUIRED":
        return EXIT_REVIEW_REQUIRED
    if result.status == "BLOCKED":
        return EXIT_BLOCKED
    return EXIT_FAILURE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m platrixa",
        description=(
            "Process one financial transaction through the Platrixa Kernel. "
            "The CLI is a thin boundary over the public Platrixa API."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the public interface version and exit.",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser(
        "process",
        help="Process one transaction and print the result as JSON.",
    )
    p.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Path to a JSON file containing the transaction "
        '(a string, or {"raw_input": "..."}).',
    )
    p.add_argument(
        "--text",
        default=None,
        help="Pass the transaction text directly instead of a JSON file.",
    )
    p.add_argument(
        "--provider",
        choices=("auto", "local", "remote"),
        default="auto",
        help="Provider selection (default: auto — runtime env decides).",
    )
    p.add_argument(
        "--rules",
        default=None,
        help="Path to a declarative YAML rule pack (Phase 10 format).",
    )
    p.add_argument(
        "--hook",
        action="append",
        default=None,
        metavar="MODULE:CLASS",
        help="RuleHook class to load, e.g. examples.rules.custom_hooks:HolidayBudgetRule. "
        "Repeatable.",
    )
    p.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "version", False):
        from platrixa import __version__

        print(json.dumps({"platrixa": __version__}))
        return EXIT_VERIFIED
    if args.command == "process":
        if args.input is None and args.text is None:
            parser.error("process requires either INPUT or --text")
        return _run(args)
    if not args.command:
        parser.print_help()
        return EXIT_VERIFIED
    parser.error("unknown command")  # pragma: no cover
    return EXIT_FAILURE  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
