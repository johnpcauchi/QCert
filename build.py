#!/usr/bin/env python3
"""Build a standalone distributable package using PyInstaller."""

import subprocess
import sys
import os


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # Ensure example files exist
    print("Generating example files…")
    subprocess.run([sys.executable, "create_examples.py"], check=True)

    print("Running PyInstaller…")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "QCert",
        "--noconfirm",
        "--windowed",          # no console window on Windows
        "--add-data", f"examples{os.pathsep}examples",
        "--add-data", f"layouts{os.pathsep}layouts",
        "run_qcert.py",
    ]
    subprocess.run(cmd, check=True)

    print()
    print("Build complete!  Distributable folder: dist/QCert/")
    print("Zip the dist/QCert/ folder to share it.")


if __name__ == "__main__":
    main()
