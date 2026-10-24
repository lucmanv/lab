import pytest

from code_doc_agent.tools import ToolInputError, Workspace, validate_input


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("import os\n\nPORT = os.getenv('PORT')\n")
    (root / "README.md").write_text("# Proj\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("ignored")
    (root / "img.bin").write_bytes(b"\x00\x01\x02")
    (tmp_path / "secret.txt").write_text("fora")
    return root


def ws_for(path, tmp_path):
    return Workspace(path, tmp_path / "out")


def test_list_ignores_noise(project, tmp_path):
    out = ws_for(project, tmp_path).list_directory(".", recursive=True)
    assert "src/" in out and "app.py  (3 linhas)" in out
    assert "node_modules" not in out
    assert "binário" in out


def test_read_file_with_ranges(project, tmp_path):
    ws = ws_for(project, tmp_path)
    out = ws.read_file("src/app.py", start_line=3, end_line=3)
    assert "linhas 3-3 de 3" in out and "PORT" in out


def test_cannot_escape_root(project, tmp_path):
    ws = ws_for(project, tmp_path)
    with pytest.raises(ToolInputError):
        ws.read_file("../secret.txt")


def test_single_file_mode_only_reads_that_file(project, tmp_path):
    ws = ws_for(project / "src" / "app.py", tmp_path)
    assert "app.py" in ws.read_file("app.py")
    assert "app.py" in ws.list_directory(".")
    assert ws.search_code("PORT").startswith("app.py:3:")


def test_single_file_mode_blocks_siblings(project, tmp_path):
    (project / "src" / "other.py").write_text("x = 1\n")
    ws = ws_for(project / "src" / "app.py", tmp_path)
    with pytest.raises(ToolInputError):
        ws.read_file("other.py")


def test_search_code(project, tmp_path):
    out = ws_for(project, tmp_path).search_code(r"getenv", glob="*.py")
    assert out == "src/app.py:3: PORT = os.getenv('PORT')"


def test_write_and_append(project, tmp_path):
    ws = ws_for(project, tmp_path)
    with pytest.raises(ToolInputError):
        ws.write_document("pdd", "append", "x")
    ws.write_document("pdd", "write", "# PDD")
    ws.write_document("pdd", "append", "## Mais")
    assert (tmp_path / "out" / "PDD.md").read_text() == "# PDD\n## Mais"


def test_output_dir_inside_project_is_hidden(project):
    ws = Workspace(project, project / "docs")
    ws.write_document("manual", "write", "# Manual")
    assert "docs" not in ws.list_directory(".", recursive=True)
    assert ws.search_code("Manual") == "Sem resultados."


@pytest.mark.parametrize("name,data", [
    ("read_file", {}),                                  # falta path
    ("read_file", {"path": 1}),                         # tipo errado
    ("read_file", {"path": "a", "start_line": True}),   # bool não é inteiro
    ("read_file", {"path": "a", "start_line": 0}),      # abaixo do mínimo
    ("write_document", {"kind": "x", "mode": "write", "content": ""}),
    ("list_directory", {"path": ".", "extra": 1}),
    ("nao_existe", {}),
])
def test_validate_input_rejects(name, data):
    with pytest.raises(ToolInputError):
        validate_input(name, data)
