"""Entry point for the Windows build (PyInstaller starts this file)."""
import sys

from warpdown.app import main

if __name__ == "__main__":
    sys.exit(main())
