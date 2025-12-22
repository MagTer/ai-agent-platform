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
        print(f"Skipping {f}")
        continue
    print(f"Fixing {f}")
    txt = p.read_text(encoding="utf-8")
    new_txt = txt.replace("List[", "list[")
    new_txt = new_txt.replace("Dict[", "dict[")
    new_txt = new_txt.replace("Tuple[", "tuple[")
    p.write_text(new_txt, encoding="utf-8")
