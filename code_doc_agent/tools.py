"""Ferramentas que o agente pode usar.

Todas as leituras ficam presas ao caminho dado pelo utilizador: o agente
nunca lê nada fora dele. A única escrita permitida é a dos dois documentos
finais, na pasta de saída.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

# Pastas que quase nunca têm código do projeto e só gastam contexto.
IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode",
    "dist", "build", "target", "bin", "obj", ".next", ".nuxt", "coverage",
    ".gradle", ".terraform", "vendor",
}
IGNORED_FILE_PATTERNS = ["*.pyc", "*.pyo", "*.class", "*.o", "*.so", "*.dll",
                         "*.exe", "*.min.js", "*.map", "*.lock", "*.log"]

MAX_LINES_PER_READ = 800
MAX_CHARS_PER_READ = 60_000
MAX_LIST_ENTRIES = 500
MAX_SEARCH_MATCHES = 200

DOC_KINDS = {"pdd": "PDD.md", "manual": "MANUAL_UTILIZADOR.md"}


class ToolInputError(ValueError):
    """Input de ferramenta inválido (ex.: JSON cortado pelo streaming)."""


TOOL_DEFINITIONS = [
    {
        "name": "list_directory",
        "description": (
            "Lista ficheiros e pastas dentro do código a documentar. "
            "Use '.' para a raiz. Com recursive=true mostra a árvore completa "
            "(ignora pastas como .git, node_modules, venv). Cada ficheiro vem "
            "com o tamanho em linhas."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Caminho relativo à raiz."},
                "recursive": {"type": "boolean"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": (
            f"Lê um ficheiro de texto, com números de linha. Devolve no máximo "
            f"{MAX_LINES_PER_READ} linhas por chamada; use start_line/end_line "
            "para ler ficheiros grandes por partes."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Caminho relativo à raiz."},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": (
            "Pesquisa uma expressão regular (sintaxe Python) em todos os ficheiros "
            "de texto. Útil para encontrar pontos de entrada, rotas, comandos CLI, "
            "variáveis de ambiente, configurações. 'glob' filtra ficheiros (ex.: '*.py')."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string"},
                "ignore_case": {"type": "boolean"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "write_document",
        "description": (
            "Escreve um dos documentos finais em Markdown. kind='pdd' para o PDD, "
            "kind='manual' para o Manual de Utilizador. mode='write' cria/substitui "
            "o documento; mode='append' acrescenta ao fim. Para documentos longos, "
            "escreva por secções: a primeira com 'write' e as seguintes com 'append'."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(DOC_KINDS)},
                "mode": {"type": "string", "enum": ["write", "append"]},
                "content": {"type": "string", "description": "Texto Markdown."},
            },
            "required": ["kind", "mode", "content"],
        },
    },
]

_SCHEMAS = {t["name"]: t["input_schema"] for t in TOOL_DEFINITIONS}
_PY_TYPES = {"string": str, "integer": int, "boolean": bool}


def validate_input(name: str, data: object) -> dict:
    """Valida o input contra o schema da ferramenta.

    Com eager_input_streaming a API não valida o input, por isso fazemos aqui.
    """
    schema = _SCHEMAS.get(name)
    if schema is None:
        raise ToolInputError(f"Ferramenta desconhecida: {name}")
    if not isinstance(data, dict):
        raise ToolInputError("O input tem de ser um objeto JSON.")
    for key in schema["required"]:
        if key not in data:
            raise ToolInputError(f"Falta o campo obrigatório '{key}'.")
    for key, value in data.items():
        prop = schema["properties"].get(key)
        if prop is None:
            raise ToolInputError(f"Campo desconhecido '{key}'.")
        expected = _PY_TYPES[prop["type"]]
        # bool é subclasse de int em Python; não aceitar True como inteiro.
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ToolInputError(f"O campo '{key}' tem de ser {prop['type']}.")
        if "enum" in prop and value not in prop["enum"]:
            raise ToolInputError(f"O campo '{key}' tem de ser um de {prop['enum']}.")
        if "minimum" in prop and value < prop["minimum"]:
            raise ToolInputError(f"O campo '{key}' tem de ser >= {prop['minimum']}.")
    return data


@dataclass
class Workspace:
    """O código a documentar (pasta ou ficheiro único) e a pasta de saída."""

    target: Path
    output_dir: Path
    written: dict[str, Path] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.target = self.target.expanduser().resolve()
        if not self.target.exists():
            raise FileNotFoundError(f"Caminho não existe: {self.target}")
        self.single_file = self.target.is_file()
        self.root = self.target.parent if self.single_file else self.target
        self.output_dir = self.output_dir.expanduser().resolve()

    # ---------- utilitários ----------

    def _resolve(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if path != self.root and self.root not in path.parents:
            raise ToolInputError(f"'{rel}' está fora do código a documentar.")
        if self.single_file and path not in (self.target, self.root):
            raise ToolInputError(
                f"Só é possível ler o ficheiro '{self.target.name}'."
            )
        if self._is_output(path):
            raise ToolInputError("Essa pasta é a pasta de saída dos documentos.")
        return path

    def _is_output(self, path: Path) -> bool:
        return path == self.output_dir or self.output_dir in path.parents

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() or "."

    @staticmethod
    def _ignored(path: Path) -> bool:
        if path.is_dir():
            return path.name in IGNORED_DIRS
        return any(fnmatch.fnmatch(path.name, p) for p in IGNORED_FILE_PATTERNS)

    @staticmethod
    def _read_text(path: Path) -> str | None:
        """Devolve o texto do ficheiro, ou None se parecer binário."""
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        if b"\x00" in raw[:8192]:
            return None
        return raw.decode("utf-8", errors="replace")

    def iter_files(self):
        if self.single_file:
            yield self.target
            return
        stack = [self.root]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_symlink() or self._ignored(entry) or self._is_output(entry):
                    continue
                if entry.is_dir():
                    stack.append(entry)
                elif entry.is_file():
                    yield entry

    # ---------- ferramentas ----------

    def list_directory(self, path: str, recursive: bool = False) -> str:
        base = self._resolve(path)
        if not base.is_dir():
            raise ToolInputError(f"'{path}' não é uma pasta.")
        if self.single_file:
            return self._describe_file(self.target)

        lines: list[str] = []

        def walk(directory: Path, depth: int) -> None:
            try:
                entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except OSError as exc:
                lines.append(f"{'  ' * depth}[erro: {exc}]")
                return
            for entry in entries:
                if len(lines) >= MAX_LIST_ENTRIES:
                    return
                if entry.is_symlink() or self._ignored(entry) or self._is_output(entry):
                    continue
                indent = "  " * depth
                if entry.is_dir():
                    lines.append(f"{indent}{entry.name}/")
                    if recursive:
                        walk(entry, depth + 1)
                else:
                    lines.append(indent + self._describe_file(entry))

        walk(base, 0)
        if len(lines) >= MAX_LIST_ENTRIES:
            lines.append(
                f"... (lista cortada em {MAX_LIST_ENTRIES} entradas; liste subpastas "
                "individualmente para ver o resto)"
            )
        return "\n".join(lines) or "(pasta vazia)"

    def _describe_file(self, path: Path) -> str:
        text = self._read_text(path)
        if text is None:
            return f"{path.name}  (binário, {path.stat().st_size} bytes)"
        return f"{path.name}  ({len(text.splitlines())} linhas)"

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        file_path = self._resolve(path)
        if not file_path.is_file():
            raise ToolInputError(f"'{path}' não é um ficheiro.")
        text = self._read_text(file_path)
        if text is None:
            return f"'{path}' é um ficheiro binário; não é possível lê-lo como texto."

        all_lines = text.splitlines()
        total = len(all_lines)
        if start_line > max(total, 1):
            raise ToolInputError(f"start_line {start_line} depois do fim ({total} linhas).")
        last = min(end_line or total, total, start_line + MAX_LINES_PER_READ - 1)

        out, size = [], 0
        for number in range(start_line, last + 1):
            line = f"{number:>5}  {all_lines[number - 1]}"
            if size + len(line) > MAX_CHARS_PER_READ:
                last = number - 1
                break
            out.append(line)
            size += len(line) + 1

        header = f"{self._rel(file_path)} — linhas {start_line}-{last} de {total}"
        footer = ""
        if last < total and (end_line is None or last < end_line):
            footer = f"\n[continua: chame read_file com start_line={last + 1}]"
        return header + "\n" + "\n".join(out) + footer

    def search_code(self, pattern: str, glob: str | None = None, ignore_case: bool = False) -> str:
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ToolInputError(f"Expressão regular inválida: {exc}") from exc

        matches: list[str] = []
        for file_path in self.iter_files():
            rel = self._rel(file_path)
            if glob and not (fnmatch.fnmatch(file_path.name, glob) or fnmatch.fnmatch(rel, glob)):
                continue
            text = self._read_text(file_path)
            if text is None:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{rel}:{number}: {line.strip()[:300]}")
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        matches.append(
                            f"... (mais resultados omitidos; limite de {MAX_SEARCH_MATCHES}, "
                            "refine o padrão ou use glob)"
                        )
                        return "\n".join(matches)
        return "\n".join(matches) or "Sem resultados."

    def write_document(self, kind: str, mode: str, content: str) -> str:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / DOC_KINDS[kind]
        if mode == "append":
            if kind not in self.written:
                raise ToolInputError(
                    f"O documento '{kind}' ainda não foi criado; use mode='write' primeiro."
                )
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\n" + content)
        else:
            path.write_text(content, encoding="utf-8")
        self.written[kind] = path
        size = path.stat().st_size
        return f"{path.name} gravado ({size} bytes no total)."

    def run(self, name: str, data: object) -> str:
        args = validate_input(name, data)
        return getattr(self, name)(**args)
