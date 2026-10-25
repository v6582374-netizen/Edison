#!/usr/bin/env python3
"""Atomically promote a built Web release without breaking already-open readers."""

import argparse
import os
import shutil
import uuid
from pathlib import Path


def promote(release: Path, live: Path) -> int:
    release = release.resolve(strict=True)
    if not (release / "index.html").is_file() or not (release / "assets").is_dir():
        raise ValueError("The new release is not a built Web application")
    if not live.is_symlink():
        raise ValueError("The live Web path must already be a release symlink")
    previous = live.resolve(strict=True)
    retained = 0
    # An open tab may lazy-load a chunk from the previous build much later.
    # Carry hashed assets forward; never overwrite anything in the new build.
    for source in (previous / "assets").iterdir():
        target = release / "assets" / source.name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)
            retained += 1
    temporary = live.with_name(live.name + ".next-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(release, target_is_directory=True)
        os.replace(temporary, live)
    finally:
        if temporary.is_symlink():
            temporary.unlink()
    return retained


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("--live", type=Path, default=Path("/srv/edison/app/web"))
    args = parser.parse_args()
    print("Previous assets retained:", promote(args.release, args.live))
