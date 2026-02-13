#!/usr/bin/env python3
"""QCert launcher — sets up the working directory and starts the application."""

import os
import sys


def main():
    # Ensure working directory is the project root (where this script lives)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # Add project root to path so `qcert` package is importable
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Check dependencies
    missing = []
    for pkg in ["openpyxl", "reportlab", "PIL", "PyPDF2"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        print("Missing dependencies:", ", ".join(missing))
        print("Install them with:  pip install -r requirements.txt")
        sys.exit(1)

    from qcert.app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
