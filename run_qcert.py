#!/usr/bin/env python3
"""QCert launcher — sets up the working directory and starts the application."""

import os
import sys


def _pause_on_error():
    """Keep the console window open so Windows users can read the error."""
    if sys.platform == "win32":
        input("\nPress Enter to exit...")


def main():
    # Ensure working directory is the project root (where this script lives)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # Add project root to path so `qcert` package is importable
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Check dependencies — the import name may differ from the pip name
    deps = [
        ("openpyxl", "openpyxl"),
        ("reportlab", "reportlab"),
        ("PIL", "Pillow"),
        ("PyPDF2", "PyPDF2"),
        ("customtkinter", "customtkinter"),
    ]
    missing = []
    for import_name, pip_name in deps:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print("Missing dependencies:", ", ".join(missing))
        print("Install them with:  pip install -r requirements.txt")
        _pause_on_error()
        sys.exit(1)

    try:
        from qcert.app import main as app_main
        app_main()
    except Exception as exc:
        print(f"\nQCert failed to start: {exc}")
        _pause_on_error()
        sys.exit(1)


if __name__ == "__main__":
    main()
