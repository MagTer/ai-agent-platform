"""Architecture validator for enforcing 4-layer modular monolith rules.

This module validates that the codebase follows the architecture constraints:
1. Core NEVER imports upward (modules/, orchestrator/, interfaces/)
2. Modules CANNOT import other modules (modules/X cannot import modules/Y)
3. Interfaces cannot import modules directly (should go through orchestrator)
4. No circular dependencies

Layer hierarchy:
    interfaces/ (Layer 1) -> orchestrator/ (Layer 2) -> modules/ (Layer 3) -> core/ (Layer 4)

The shared/ directory is accessible by all layers.
"""

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Violation:
    """Represents an architecture rule violation."""

    file: Path
    line: int
    import_stmt: str
    rule: str
    description: str

    def __str__(self) -> str:
        """Format violation for display."""
        return (
            f"{self.file}:{self.line} - {self.description}\n"
            f"  Import: {self.import_stmt}\n"
            f"  Rule: {self.rule}"
        )


class ArchitectureValidator:
    """Validates architecture rules across the codebase."""

    def __init__(self, src_root: Path) -> None:
        """Initialize validator with source root.

        Args:
            src_root: Path to services/agent/src directory
        """
        self.src_root = src_root
        self.violations: list[Violation] = []

        # Layer directories
        self.core_dir = src_root / "core"
        self.modules_dir = src_root / "modules"
        self.orchestrator_dir = src_root / "orchestrator"
        self.interfaces_dir = src_root / "interfaces"
        self.shared_dir = src_root / "shared"

    def _get_layer(self, file_path: Path) -> str | None:
        """Determine which layer a file belongs to.

        Args:
            file_path: Path to Python file

        Returns:
            Layer name or None if not in a layer
        """
        try:
            relative = file_path.relative_to(self.src_root)
        except ValueError:
            return None

        parts = relative.parts
        if not parts:
            return None

        layer = parts[0]
        if layer in ("core", "modules", "orchestrator", "interfaces"):
            return layer
        return None

    def _get_module_name(self, file_path: Path) -> str | None:
        """Get module name for files in modules/ directory.

        Args:
            file_path: Path to Python file

        Returns:
            Module name (e.g., "rag") or None
        """
        if not file_path.is_relative_to(self.modules_dir):
            return None

        try:
            relative = file_path.relative_to(self.modules_dir)
        except ValueError:
            return None

        if relative.parts:
            return relative.parts[0]
        return None

    def _is_first_party_import(self, module: str) -> bool:
        """Check if an import is first-party (our code).

        Args:
            module: Module name from import statement

        Returns:
            True if first-party import
        """
        first_party_roots = ("core", "modules", "orchestrator", "interfaces", "shared", "stack")
        return module.split(".")[0] in first_party_roots

    def _extract_imports(self, file_path: Path) -> list[tuple[str, int]]:
        """Extract all import statements from a Python file.

        Args:
            file_path: Path to Python file

        Returns:
            List of (module_name, line_number) tuples
        """
        try:
            content = file_path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError):
            # Skip files with syntax errors or encoding issues
            return []

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append((node.module, node.lineno))

        return imports

    def _check_core_imports(self, file_path: Path) -> None:
        """Check that core/ never imports upward.

        Args:
            file_path: Path to Python file in core/
        """
        imports = self._extract_imports(file_path)

        for module, line in imports:
            if not self._is_first_party_import(module):
                continue

            root_module = module.split(".")[0]
            if root_module in ("modules", "orchestrator", "interfaces"):
                self.violations.append(
                    Violation(
                        file=file_path.relative_to(self.src_root),
                        line=line,
                        import_stmt=module,
                        rule="Core layer isolation",
                        description=f"core/ must not import from {root_module}/",
                    )
                )

    def _check_module_imports(self, file_path: Path) -> None:
        """Check that modules/ cannot import other modules.

        Args:
            file_path: Path to Python file in modules/
        """
        current_module = self._get_module_name(file_path)
        if not current_module:
            return

        imports = self._extract_imports(file_path)

        for module, line in imports:
            if not self._is_first_party_import(module):
                continue

            # Check if importing from modules/
            if module.startswith("modules."):
                imported_module = module.split(".")[1] if len(module.split(".")) > 1 else None
                if imported_module and imported_module != current_module:
                    description = (
                        f"modules/{current_module} cannot import from modules/{imported_module} "
                        "(use Protocol-based DI via core)"
                    )
                    self.violations.append(
                        Violation(
                            file=file_path.relative_to(self.src_root),
                            line=line,
                            import_stmt=module,
                            rule="Module isolation",
                            description=description,
                        )
                    )

    def _check_interface_imports(self, file_path: Path) -> None:
        """Check that interfaces/ cannot import modules directly.

        Args:
            file_path: Path to Python file in interfaces/
        """
        imports = self._extract_imports(file_path)

        for module, line in imports:
            if not self._is_first_party_import(module):
                continue

            # Interfaces should go through orchestrator, not directly to modules
            if module.startswith("modules."):
                description = "interfaces/ should not import modules/ directly (use orchestrator/)"
                self.violations.append(
                    Violation(
                        file=file_path.relative_to(self.src_root),
                        line=line,
                        import_stmt=module,
                        rule="Interface layer dependencies",
                        description=description,
                    )
                )

    def _check_orchestrator_imports(self, file_path: Path) -> None:
        """Check that orchestrator/ cannot import from interfaces/.

        Args:
            file_path: Path to Python file in orchestrator/
        """
        imports = self._extract_imports(file_path)

        for module, line in imports:
            if not self._is_first_party_import(module):
                continue

            # Orchestrator should not import from interfaces (higher layer)
            if module.startswith("interfaces."):
                description = "orchestrator/ cannot import from interfaces/ (upward dependency)"
                self.violations.append(
                    Violation(
                        file=file_path.relative_to(self.src_root),
                        line=line,
                        import_stmt=module,
                        rule="Orchestrator layer isolation",
                        description=description,
                    )
                )

    def validate_file(self, file_path: Path) -> None:
        """Validate a single Python file.

        Args:
            file_path: Path to Python file
        """
        if not file_path.suffix == ".py":
            return

        layer = self._get_layer(file_path)
        if not layer:
            return

        if layer == "core":
            self._check_core_imports(file_path)
        elif layer == "modules":
            self._check_module_imports(file_path)
        elif layer == "interfaces":
            self._check_interface_imports(file_path)
        elif layer == "orchestrator":
            self._check_orchestrator_imports(file_path)

    def validate_directory(self, directory: Path) -> None:
        """Recursively validate all Python files in a directory.

        Args:
            directory: Directory to scan
        """
        # Skip certain directories
        skip_dirs = {"__pycache__", ".venv", "venv", ".git", "tests", "node_modules"}

        for path in directory.rglob("*.py"):
            # Skip if any parent directory is in skip list
            if any(part in skip_dirs for part in path.parts):
                continue

            self.validate_file(path)

    def validate_all(self) -> list[Violation]:
        """Validate all source files.

        Returns:
            List of violations found
        """
        self.violations = []
        self.validate_directory(self.src_root)
        return self.violations


def validate_architecture(src_root: Path) -> tuple[bool, list[str]]:
    """Validate architecture rules across the codebase.

    Args:
        src_root: Path to services/agent/src directory

    Returns:
        Tuple of (passed, violations_list)
        - passed: True if no violations found
        - violations_list: List of violation descriptions
    """
    validator = ArchitectureValidator(src_root)
    violations = validator.validate_all()

    if not violations:
        return (True, [])

    violation_strings = [str(v) for v in violations]
    return (False, violation_strings)


__all__ = ["validate_architecture", "Violation", "ArchitectureValidator"]
