"""Compatibility import for the candidate-owned native entrypoint."""

from maintenance.native_case_entrypoint import *  # noqa: F401,F403


if __name__ == "__main__":
    from maintenance.native_case_entrypoint import main

    raise SystemExit(main())
