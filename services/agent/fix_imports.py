import pathlib

files = [
    "src/modules/embedder/__init__.py",
    "src/modules/rag/__init__.py",
    "src/modules/fetcher/__init__.py",
    "src/core/db/models.py",
    "src/core/command_loader.py",
    "src/interfaces/protocols.py",
]

for f in files:
    p = pathlib.Path(f)
    if not p.exists():
        continue
    print(f"Cleaning imports in {f}")
    txt = p.read_text(encoding="utf-8")
    new_txt = (
        txt.replace(", List", "")
        .replace("List, ", "")
        .replace("from typing import List", "from typing import ")
    )
    new_txt = (
        new_txt.replace(", Dict", "")
        .replace("Dict, ", "")
        .replace("from typing import Dict", "from typing import ")
    )
    new_txt = (
        new_txt.replace(", Tuple", "")
        .replace("Tuple, ", "")
        .replace("from typing import Tuple", "from typing import ")
    )

    # Remove any trailing comma from import if it was last
    # e.g. from typing import Any,
    # This is rough but likely effective enough.

    p.write_text(new_txt, encoding="utf-8")
