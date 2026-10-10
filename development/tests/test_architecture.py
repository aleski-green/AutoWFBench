"""Keep architectural responsibilities separate without counting collection leaves."""
import ast
import importlib.util
import unittest

from autowfbench.core.common import ROOT


SPLITS = {
    "autowfbench": {"core", "runtime", "interfaces"},
    "autowfbench.core": {"common", "contracts", "scoring"},
    "autowfbench.runtime": {"engine", "environment", "judge"},
    "autowfbench.runtime.environment": {"apps", "state", "server"},
    "autowfbench.interfaces": {"cli", "composer", "solution"},
    "autowfbench.interfaces.composer": {"graph", "n8n", "search"},
}
BOUNDARIES = {
    "autowfbench.core": ("autowfbench.core",),
    "autowfbench.runtime": ("autowfbench.core", "autowfbench.runtime"),
    "autowfbench.runtime.environment.apps": ("autowfbench.core",),
    "autowfbench.runtime.environment.state": ("autowfbench.core", "autowfbench.runtime.environment.apps"),
    "autowfbench.runtime.environment.server": ("autowfbench.core", "autowfbench.runtime.environment.apps", "autowfbench.runtime.environment.state"),
    "autowfbench.interfaces.composer.graph": ("autowfbench.core", "autowfbench.interfaces.composer.n8n"),
    "autowfbench.interfaces.composer.n8n": ("autowfbench.core",),
}


def belongs(module, prefix):
    return module == prefix or module.startswith(prefix + ".")


def import_graph():
    modules = {}
    for path in (ROOT / "autowfbench").rglob("*.py"):
        parts = path.relative_to(ROOT).with_suffix("").parts
        modules[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = path
    graph = {module: set() for module in modules}
    for module, path in modules.items():
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                targets = [name.name for name in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                targets = [base, *(base + "." + name.name for name in node.names if base + "." + name.name in modules)]
            else:
                continue
            for target in targets:
                if belongs(target, "autowfbench"):
                    if target not in modules:
                        raise AssertionError(f"{module} imports missing module {target}")
                    graph[module].add(target)
    return graph


class ArchitectureTests(unittest.TestCase):
    def test_explicit_responsibility_splits(self):
        for module, expected in SPLITS.items():
            directory = ROOT.joinpath(*module.split("."))
            actual = {p.stem for p in directory.iterdir()
                      if not p.name.startswith("_") and ((p / "__init__.py").is_file() or p.suffix == ".py")}
            with self.subTest(module=module):
                self.assertEqual(actual, expected)

    def test_dependency_direction(self):
        for source, targets in import_graph().items():
            for boundary, allowed in BOUNDARIES.items():
                if belongs(source, boundary):
                    for target in targets:
                        self.assertTrue(any(belongs(target, prefix) for prefix in allowed),
                                        f"{source} must not import {target}")

    def test_no_import_cycles(self):
        graph, visited, active = import_graph(), set(), []

        def visit(module):
            self.assertNotIn(module, active, "Import cycle: " + " -> ".join([*active, module]))
            if module in visited:
                return
            active.append(module)
            for target in sorted(graph[module]):
                visit(target)
            active.pop()
            visited.add(module)

        for module in graph:
            visit(module)
