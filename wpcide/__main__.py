"""Launches the local sound editor.

    python -m wpcide

Starts a server on the loopback address and opens the browser at it.
Nothing is uploaded anywhere: the page and the program that serves it
are both on this machine.
"""

import argparse
import sys
import traceback

from wpcide import paths
from wpcide.server import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser window")
    arguments = parser.parse_args()
    serve(port=arguments.port, open_browser=not arguments.no_browser)


if __name__ == "__main__":
    try:
        main()
    except Exception:          # noqa: BLE001
        # Double-clicked, the console closes the instant the program
        # ends, taking the reason with it. Packaged, keep it on screen.
        traceback.print_exc()
        if paths.frozen():
            input("\nEl editor no ha podido arrancar. Pulsa Enter para cerrar.")
        sys.exit(1)
