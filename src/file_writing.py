"""Saving a file so that nobody ever sees it half-written.

Every file the sandbox keeps — usage records, settings, the model catalog,
conversations — is saved the same way: the new contents go into a scratch file
beside the real one, and the scratch file is then moved into place in a single
step. Someone reading at that moment sees either the old file or the new one,
never a mixture; and an interruption partway through (a crash, a closed laptop,
a power cut) leaves the old file as it was rather than a file that no longer
makes sense.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path


def write_in_one_step(path: Path, text: str, *, private: bool = False) -> None:
    """Save *text* as the file at *path*, replacing what was there in one step.

    The folder is made first if it does not exist yet.

    Args:
        path: The file to save. Whatever is there now is replaced.
        text: Everything the file should hold once saved.
        private: True for a file holding secrets, such as API keys. It is then
                 readable by this computer's account alone. Left False, the
                 file gets the usual permissions, which is what lets a
                 colleague read usage records kept in a shared folder.

    Raises:
        OSError: If the file could not be saved. The old file is untouched,
                 and no scratch file is left behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Beside the real file, because moving a file into place is only a single
    # step when both are on the same disk. Named after the process and thread
    # doing the writing so that two writers never share a scratch file. With
    # one fixed name, two parallel jobs fill the same scratch file at once, the
    # real file ends up holding one write laid over the other, and the second
    # move finds nothing there to move.
    scratch = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                             0o600 if private else 0o666)
        with open(descriptor, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(scratch, path)
    except BaseException:
        try:
            os.unlink(scratch)
        except OSError:
            pass
        raise
