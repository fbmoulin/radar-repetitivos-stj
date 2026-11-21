"""Testes do atualizador (dados sintéticos; nenhum dado real de processo)."""

from __future__ import annotations

import csv
import io
import json
import sys
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import atualizar as at  # noqa: E402

CAMPOS_T = [
    "sequencialPrecedente", "tipoPrecedente", "numeroPrecedente", "dataPrimeiraAfetacao", "situacao",
    "informacoesComplementares", "questaoSubmetidaAJulgamento", "teseFirmada", "anotacoesNUGEPNAC", "orgaoJulgador",
]
CAMPOS_P = ["sequencialPrecedente", "Processo", "numeroRegistro", "leadingCase"]


def tema(seq, numero, situacao="Afetado", orgao="S2", ordem="Suspensão dos recursos especiais.", tipo="Tema", tese="", anot=""):
    return {
        "sequencialPrecedente": str(seq), "tipoPrecedente": tipo, "numeroPrecedente": str(numero),
        "dataPrimeiraAfetacao": "2026-01-10", "situacao": situacao, "informacoesComplementares": ordem,
        "questaoSubmetidaAJulgamento": f"Questão {numero}", "teseFirmada": tese, "anotacoesNUGEPNAC": anot,
        "orgaoJulgador": orgao,
    }


def para_csv(linhas, campos):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=campos)
    w.writeheader()
    w.writerows(linhas)
    return buf.getvalue()


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    dados = tmp_path / "data"
    dados.mkdir()
    monkeypatch.setattr(at, "MIN_TEMAS", 1)
    return tmp_path, dados


def classif(rid, alcance="Suspende o 1º grau", ordem="Suspensão dos recursos especiais."):
    return {rid: {"materia": f"Matéria {rid}", "area": "Bancário e consumo", "alcance": alcance,
                  "observacoes": "obs", "conferido_em": "2026-11-06", "ordem_sha1": at.sha1(ordem)}}


def rodar(pasta_, temas, processos=()):
    raiz, dados = pasta_
    return at.atualizar(para_csv(temas, CAMPOS_T), para_csv(list(processos), CAMPOS_P), "2026-11-13", dados, raiz)


def ler(dados, nome):
    return json.loads((dados / nome).read_text(encoding="utf-8"))


def test_filtra_pendentes_da_2a_secao_e_corte_especial(pasta):
    raiz, dados = pasta
    temas = [
        tema(1, 10), tema(2, 11, orgao="CE"), tema(3, 12, orgao="S1"),
        tema(4, 13, situacao="Trânsito em Julgado"), tema(1, 10),
    ]
    assert rodar(pasta, temas) == 0
    ids = [p["id"] for p in ler(dados, "precedentes.json")]
    assert ids == ["Tema 10", "Tema 11"]


def test_sem_tipo_vira_prorqf_pela_anotacao(pasta):
    raiz, dados = pasta
    rodar(pasta, [tema(1, 1478, tipo="", situacao="Relevância reconhecida", anot="ProRQF 4/STJ.")])
    assert ler(dados, "precedentes.json")[0]["id"] == "ProRQF 1478"


def test_classificacao_e_link_do_stj(pasta):
    raiz, dados = pasta
    (dados / "classificacao.json").write_text(json.dumps(classif("Tema 10")), encoding="utf-8")
    proc = [{"sequencialPrecedente": "1", "Processo": "REsp 1", "numeroRegistro": "202600000001", "leadingCase": "S"}]
    rodar(pasta, [tema(1, 10)], proc)
    p = ler(dados, "precedentes.json")[0]
    assert p["grupo"] == "Suspende o 1º grau"
    assert "art. 1.037" in p["providencia"]
    assert "cod_tema_inicial=10&cod_tema_final=10" in p["link_stj"]
    assert p["processo_paradigma"] == "REsp 1"


def test_sem_classificacao_fica_a_classificar(pasta):
    raiz, dados = pasta
    rodar(pasta, [tema(1, 10)])
    p = ler(dados, "precedentes.json")[0]
    assert p["alcance"] == "A classificar" and p["area"] == "A classificar"


def test_ordem_alterada_pede_reclassificacao(pasta):
    raiz, dados = pasta
    (dados / "classificacao.json").write_text(json.dumps(classif("Tema 10")), encoding="utf-8")
    rodar(pasta, [tema(1, 10, ordem="Suspensão de todos os processos pendentes.")])
    p = ler(dados, "precedentes.json")[0]
    assert p["reclassificar"] is True and p["alcance"] == "A classificar"


def test_primeira_execucao_registra_base_e_feed_valido(pasta):
    raiz, dados = pasta
    rodar(pasta, [tema(1, 10)])
    assert [m["tipo"] for m in ler(dados, "mudancas.json")] == ["base"]
    ET.fromstring((raiz / "feed.xml").read_text(encoding="utf-8"))


def test_mudancas_entre_execucoes(pasta):
    raiz, dados = pasta
    rodar(pasta, [tema(1, 10), tema(2, 11), tema(3, 12)])
    rodar(pasta, [
        tema(1, 10, situacao="Em Julgamento"),
        tema(2, 11, ordem="Suspensão de todos os processos pendentes."),
        tema(3, 12, situacao="Acórdão Publicado", tese="Tese de teste."),
        tema(4, 13),
    ])
    mud = ler(dados, "mudancas.json")
    tipos = {(m["id"], m["tipo"]) for m in mud}
    assert ("Tema 10", "situacao") in tipos
    assert ("Tema 11", "suspensao") in tipos
    assert ("Tema 12", "saiu") in tipos
    assert ("Tema 13", "novo") in tipos
    saiu = next(m for m in mud if m["tipo"] == "saiu")
    assert "Acórdão Publicado" in saiu["texto"] and "Tese de teste." in saiu["texto"]
    assert mud[-1]["tipo"] == "base"


def test_sem_mudanca_nao_reescreve_dados(pasta):
    raiz, dados = pasta
    rodar(pasta, [tema(1, 10)])
    antes = (dados / "precedentes.json").read_text(encoding="utf-8")
    atualizado = ler(dados, "meta.json")["atualizado_em"]
    rodar(pasta, [tema(1, 10)])
    assert (dados / "precedentes.json").read_text(encoding="utf-8") == antes
    assert ler(dados, "meta.json")["atualizado_em"] == atualizado
    assert len(ler(dados, "mudancas.json")) == 1


def test_queda_suspeita_nao_publica(pasta):
    raiz, dados = pasta
    rodar(pasta, [tema(i, 100 + i) for i in range(1, 11)])
    assert rodar(pasta, [tema(1, 101)]) == 4
    assert len(ler(dados, "precedentes.json")) == 10
    assert ler(dados, "meta.json")["ultima_tentativa"]["status"] == "erro"


def test_base_pequena_demais_nao_publica(pasta, monkeypatch):
    raiz, dados = pasta
    monkeypatch.setattr(at, "MIN_TEMAS", 5)
    assert rodar(pasta, [tema(1, 10)]) == 4
    assert not (dados / "precedentes.json").exists()


def test_portal_bloqueado_registra_e_sai_com_3(tmp_path, monkeypatch):
    dados = tmp_path / "data"
    dados.mkdir()
    monkeypatch.setattr(at, "DADOS", dados)

    def bloqueado():
        raise urllib.error.HTTPError(at.CKAN, 403, "Forbidden", {}, None)

    monkeypatch.setattr(at, "baixar", bloqueado)
    assert at.main(["--baixar"]) == 3
    meta = ler(dados, "meta.json")
    assert meta["ultima_tentativa"]["status"] == "bloqueado"
    assert "403" in meta["ultima_tentativa"]["detalhe"]


def test_escolhe_o_recurso_mais_recente():
    recursos = [
        {"name": "Temas.csv", "url": "a", "last_modified": "2026-10-01T00:00:00"},
        {"name": "Temas.csv", "url": "b", "last_modified": "2026-11-13T21:45:00"},
        {"name": "Processos.csv", "url": "c"},
    ]
    assert at._escolher(recursos, "temas.csv")["url"] == "b"
    with pytest.raises(ValueError):
        at._escolher(recursos, "outro.csv")
