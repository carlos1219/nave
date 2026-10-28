"""Ayudante `nave_oversized.py` del envoltorio de Semgrep (F7-02, ADR 0024)."""

import importlib.util
import json
import os

import pytest

LIMIT = 100


@pytest.fixture
def helper(root):
    path = root / "deploy/scanner-sandbox/semgrep/nave_oversized.py"
    spec = importlib.util.spec_from_file_location("nave_oversized", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sarif(results=()):
    return {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "Semgrep OSS"}}, "results": list(results)}]}


def write(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_solo_se_listan_los_ficheros_mayores_que_el_limite(helper, tmp_path):
    write(tmp_path / "ok.py", LIMIT)
    write(tmp_path / "sub/big.py", LIMIT + 1)
    write(tmp_path / "a/z.bin", LIMIT * 5)
    assert helper.oversized_files(tmp_path, LIMIT) == [("a/z.bin", LIMIT * 5), ("sub/big.py", LIMIT + 1)]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="sin enlaces simbólicos")
def test_no_sigue_enlaces_simbolicos(helper, tmp_path):
    outside = tmp_path / "outside"
    write(outside / "huge.bin", LIMIT * 10)
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        os.symlink(outside, repo / "link_dir")
        os.symlink(outside / "huge.bin", repo / "link_file")
    except OSError:
        pytest.skip("no se pueden crear enlaces simbólicos")
    assert helper.oversized_files(repo, LIMIT) == []


def test_cada_fichero_omitido_es_un_resultado_con_ruta_del_repositorio(helper):
    results = helper.synthetic_results([("sub dir/ñ big.py", 500)], LIMIT)
    assert len(results) == 1
    result = results[0]
    assert result["ruleId"] == helper.RULE_FILE and result["level"] == "warning"
    uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "repo/sub%20dir/%C3%B1%20big.py"
    assert str(LIMIT) in result["message"]["text"]


def test_el_exceso_se_resume_en_un_unico_resultado_sin_ubicacion(helper):
    files = [(f"f{i:03}.py", LIMIT + 1) for i in range(helper.MAX_LISTED + 7)]
    results = helper.synthetic_results(files, LIMIT)
    assert [r["ruleId"] for r in results].count(helper.RULE_FILE) == helper.MAX_LISTED
    assert results[-1]["ruleId"] == helper.RULE_MORE and "locations" not in results[-1]


def test_sin_ficheros_grandes_el_sarif_no_cambia(helper, tmp_path, capsys):
    write(tmp_path / "repo/ok.py", 10)
    original = sarif([{"ruleId": "r", "message": {"text": "m"}}])
    (tmp_path / "in.json").write_text(json.dumps(original), encoding="utf-8")
    assert helper.main([str(tmp_path / "in.json"), str(tmp_path / "repo"), str(LIMIT)]) == 0
    assert json.loads(capsys.readouterr().out) == original


def test_se_conservan_los_resultados_de_semgrep_y_se_anaden_los_omitidos(helper, tmp_path, capsys):
    write(tmp_path / "repo/big.py", LIMIT + 1)
    original = sarif([{"ruleId": "r", "message": {"text": "m"}}])
    (tmp_path / "in.json").write_text(json.dumps(original), encoding="utf-8")
    assert helper.main([str(tmp_path / "in.json"), str(tmp_path / "repo"), str(LIMIT)]) == 0
    merged = json.loads(capsys.readouterr().out)
    assert [r["ruleId"] for r in merged["runs"][0]["results"]] == ["r", helper.RULE_FILE]


@pytest.mark.parametrize("content", ["no es json", "[]", '{"runs": []}', '{"runs": [1]}', '{"runs": [{"results": 3}]}'])
def test_un_sarif_inesperado_falla_en_vez_de_darse_por_bueno(helper, tmp_path, capsys, content):
    (tmp_path / "in.json").write_text(content, encoding="utf-8")
    (tmp_path / "repo").mkdir()
    assert helper.main([str(tmp_path / "in.json"), str(tmp_path / "repo"), str(LIMIT)]) == helper.EXIT_FAILURE
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("args", [[], ["a", "b"], ["a", "b", "-1"], ["a", "b", "1e3"], ["a", "b", "١٢"], ["a", "b", "1", "2"]])
def test_argumentos_invalidos_fallan(helper, args):
    assert helper.main(args) == helper.EXIT_FAILURE


def test_el_envoltorio_y_el_ayudante_comparten_el_limite(root):
    wrapper = (root / "deploy/scanner-sandbox/semgrep/nave-scan").read_text(encoding="utf-8")
    assert 'MAX_TARGET_BYTES=2000000' in wrapper
    assert '--max-target-bytes "${MAX_TARGET_BYTES}"' in wrapper
    assert 'nave_oversized.py "${SARIF}" /workspace/repo "${MAX_TARGET_BYTES}"' in wrapper
    dockerfile = (root / "deploy/scanner-sandbox/semgrep/Dockerfile").read_text(encoding="utf-8")
    assert "COPY nave_oversized.py /usr/local/lib/nave/nave_oversized.py" in dockerfile
