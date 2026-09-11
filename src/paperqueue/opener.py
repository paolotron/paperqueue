"""Open a URL in the user's browser without disturbing the terminal."""

from __future__ import annotations

import shutil
import subprocess
import webbrowser


def open_url(url: str) -> None:
    opener = shutil.which("xdg-open")
    if opener:
        subprocess.Popen(
            [opener, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return
    webbrowser.open(url, new=2)
