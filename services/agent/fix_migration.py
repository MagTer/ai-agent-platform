import os
import re

# Konfiguration
ROOT_DIR = os.getcwd()
SRC_DIR = os.path.join(ROOT_DIR, "src")
PYPROJECT_PATH = os.path.join(ROOT_DIR, "pyproject.toml")
APP_PATH = os.path.join(SRC_DIR, "core", "core", "app.py")


def fix_imports():
    print("🔍 Söker igenom filer för att laga imports...")
    count = 0
    for root, _dirs, files in os.walk(SRC_DIR):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                with open(path, encoding="utf-8") as f:
                    content = f.read()

                new_content = re.sub(r"from agent\.", "from core.", content)
                new_content = re.sub(r"import agent\.", "import core.", new_content)

                if content != new_content:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    print(f"   ✅ Fixade imports i: {path}")
                    count += 1
    print(f"✨ Totalt fixade filer: {count}")


def update_pyproject():
    print("⚙️ Uppdaterar pyproject.toml...")
    if not os.path.exists(PYPROJECT_PATH):
        print("   ⚠️ Hittade ingen pyproject.toml, hoppar över.")
        return

    with open(PYPROJECT_PATH, encoding="utf-8") as f:
        content = f.read()

    if 'include = "agent"' in content:
        new_packages = """packages = [
    {include = \"core\", from = \"src\"},
    {include = \"orchestrator\", from = \"src\"},
    {include = \"interfaces\", from = \"src\"},
    {include = \"stack\", from = \"src\"}
]"""
        if re.search(r"packages\s*=\s*\[.*?\]", content, re.DOTALL):
            content = re.sub(
                r"packages\s*=\s*\[.*?\]", new_packages, content, flags=re.DOTALL
            )
        else:
            content += "\n" + new_packages

        with open(PYPROJECT_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print("   ✅ pyproject.toml uppdaterad.")
    else:
        print("   ℹ️ Behövde inte uppdatera pyproject.toml (redan fixad?)")


def wire_up_adapter():
    print("🔌 Kopplar in Open WebUI-adaptern i app.py...")
    if not os.path.exists(APP_PATH):
        print(f"   ⚠️ Kunde inte hitta app.py på {APP_PATH}")
        return

    with open(APP_PATH, encoding="utf-8") as f:
        content = f.read()

    if "openwebui_adapter" in content:
        print("   ℹ️ Adaptern verkar redan vara inkopplad.")
        return

    import_statement = (
        "from interfaces.http.openwebui_adapter import router as openwebui_router"
    )

    # Insert safe import
    if "from fastapi import" in content:
        content = content.replace(
            "from fastapi import", f"{import_statement}\nfrom fastapi import"
        )
    else:
        content = import_statement + "\n" + content

    # Wire up router
    if "return app" in content:
        new_content = "app.include_router(openwebui_router)\n    return app"
        content = content.replace("return app", new_content)
        with open(APP_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print("   ✅ Adaptern inkopplad och redo.")
    else:
        print(
            "   ⚠️ Hittade inte 'return app' i filen, kunde inte automatiskt koppla in."
        )


if __name__ == "__main__":
    print("🚀 Startar 'Universal Agent' Reparation...")
    fix_imports()
    update_pyproject()
    wire_up_adapter()
    print("\n✅ Klart!")
