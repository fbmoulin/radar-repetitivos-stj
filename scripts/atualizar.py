#!/usr/bin/env python3
"""Atualiza o Radar de repetitivos do STJ a partir dos Dados Abertos do STJ.

Lê Temas.csv e Processos.csv (baixados do portal ou de arquivos locais), separa os
precedentes pendentes da 2ª Seção e da Corte Especial, junta a classificação manual
do alcance da suspensão (data/classificacao.json), compara com a versão anterior e
grava:

  data/precedentes.json  lista publicada na página
  data/mudancas.json     histórico de mudanças (mais recente primeiro)
  data/meta.json         datas, contagens e o resultado da última tentativa
  feed.xml               RSS das mudanças

Só biblioteca padrão. Uso:
  python scripts/atualizar.py --baixar
  python scripts/atualizar.py --temas temas.csv --processos processos.csv

Códigos de saída: 0 atualizado ou sem mudança; 3 portal inacessível (o meta.json
registra "bloqueado" e nada mais muda); 4 dados suspeitos (nada é publicado).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "data"
SITE = "https://fbmoulin.github.io/radar-repetitivos-stj/"
CKAN = "https://dadosabertos.web.stj.jus.br/api/3/action/package_show?id=precedentes-qualificados"
UA = "radar-repetitivos-stj/1.0 (+https://github.com/fbmoulin/radar-repetitivos-stj)"

ORGAOS = {"S2": "2ª Seção", "CE": "Corte Especial"}
PENDENTES = {
    "Afetado",
    "Em Julgamento",
    "Sobrestado",
    "Mérito Julgado",
    "Relevância reconhecida",
    "Afetado - Possível Revisão de Tese",
    "Pendente",
    "Em Afetação",
    "Admitido",
    "Suspensão deferida",
}
GRUPO = {
    "Suspende o 1º grau": "Suspende o 1º grau",
    "Texto ambíguo: conferir": "A conferir",
    "Já julgado: aplicar a tese": "Já julgado",
    "Só REsp/AREsp (2º grau e STJ)": "Só REsp/AREsp",
    "Sem suspensão": "Sem suspensão ou sem ordem",
    "Sem suspensão (controvérsia não afetada)": "Sem suspensão ou sem ordem",
    "Sem informação na base": "Sem suspensão ou sem ordem",
    "A classificar": "A classificar",
}
PROVIDENCIA = {
    "Suspende o 1º grau": "A ordem alcança processos em 1º grau: sobrestar os feitos com a mesma questão (art. 1.037, II, do CPC) e intimar as partes (art. 1.037, § 8º). Atos urgentes seguem (art. 314).",
    "Texto ambíguo: conferir": "O texto da base não deixa claro se a ordem alcança o 1º grau: conferir o acórdão de afetação antes de sobrestar.",
    "Já julgado: aplicar a tese": "Mérito julgado: aplicar a tese depois de conferir a publicação do acórdão.",
    "Só REsp/AREsp (2º grau e STJ)": "O 1º grau julga normalmente: a suspensão atinge só recursos especiais e agravos no 2º grau e no STJ.",
    "Sem suspensão": "Sem suspensão: seguir normalmente e acompanhar a tese.",
    "Sem suspensão (controvérsia não afetada)": "Controvérsia ainda não afetada: não suspende processos.",
    "Sem informação na base": "A base não traz ordem de suspensão: conferir na página do tema.",
    "A classificar": "Precedente novo ou com ordem alterada, ainda não classificado: conferir a ordem de suspensão na página do STJ.",
}
MIN_TEMAS = 1000
QUEDA_MAXIMA = 0.4
MAX_MUDANCAS = 500


def agora() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def norm(texto: str | None) -> str:
    return " ".join((texto or "").split())


def sha1(texto: str | None) -> str:
    return hashlib.sha1(norm(texto).encode("utf-8")).hexdigest()


def ler_csv(texto: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(texto.lstrip("﻿"))))


# ── download ─────────────────────────────────────────────────────────


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL fixa do STJ)
        return resp.read()


def _escolher(recursos: list[dict], nome: str) -> dict:
    candidatos = [r for r in recursos if (r.get("name") or "").strip().lower() == nome]
    if not candidatos:
        raise ValueError(f"recurso {nome} não encontrado no portal")
    chave = lambda r: r.get("last_modified") or r.get("metadata_modified") or r.get("created") or ""  # noqa: E731
    return max(candidatos, key=chave)


def baixar() -> tuple[str, str, str]:
    """Devolve (temas_csv, processos_csv, data_da_base) a partir da API CKAN do STJ."""
    pacote = json.loads(_get(CKAN))["result"]
    temas_r = _escolher(pacote["resources"], "temas.csv")
    proc_r = _escolher(pacote["resources"], "processos.csv")
    base = temas_r.get("last_modified") or pacote.get("metadata_modified") or ""
    temas = _get(temas_r["url"], 180).decode("utf-8")
    processos = _get(proc_r["url"], 180).decode("utf-8")
    return temas, processos, base


# ── montagem ─────────────────────────────────────────────────────────


def rotulo(row: dict) -> str:
    tipo = (row.get("tipoPrecedente") or "").strip()
    if not tipo:
        tipo = "ProRQF" if (row.get("anotacoesNUGEPNAC") or "").startswith("ProRQF") else "Precedente"
    return f"{tipo} {row['numeroPrecedente'].strip()}"


def link_stj(tipo: str, numero: str, registro: str | None) -> str:
    if tipo == "Tema":
        return (
            "https://processo.stj.jus.br/repetitivos/temas_repetitivos/pesquisa.jsp?"
            f"novaConsulta=true&tipo_pesquisa=T&cod_tema_inicial={numero}&cod_tema_final={numero}"
        )
    if registro:
        return f"https://processo.stj.jus.br/processo/pesquisa/?tipoPesquisa=tipoPesquisaNumeroRegistro&termo={registro}"
    return "https://processo.stj.jus.br/repetitivos/temas_repetitivos/"


def leading_cases(processos: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in processos:
        seq = (p.get("sequencialPrecedente") or "").strip()
        if not seq:
            continue
        atual = out.get(seq)
        if atual is None or (p.get("leadingCase") == "S" and atual.get("leadingCase") != "S"):
            out[seq] = p
    return out


def montar(temas: list[dict], processos: list[dict], classificacao: dict) -> tuple[list[dict], dict[str, dict]]:
    """Devolve (pendentes publicados, todos os precedentes por rótulo)."""
    lc = leading_cases(processos)
    vistos: set[str] = set()
    todos: dict[str, dict] = {}
    pendentes: list[dict] = []
    for row in temas:
        seq = (row.get("sequencialPrecedente") or "").strip()
        if not seq or seq in vistos:
            continue
        vistos.add(seq)
        rid = rotulo(row)
        todos[rid] = row
        if row.get("situacao") not in PENDENTES or row.get("orgaoJulgador") not in ORGAOS:
            continue
        tipo, numero = rid.split(" ", 1)
        ordem = norm(row.get("informacoesComplementares"))
        c = classificacao.get(rid)
        reclassificar = bool(c) and c.get("ordem_sha1") != sha1(ordem)
        alcance = c["alcance"] if c and not reclassificar else "A classificar"
        processo = lc.get(seq, {})
        pendentes.append(
            {
                "id": rid,
                "tipo": tipo,
                "numero": int(numero) if numero.isdigit() else numero,
                "orgao": ORGAOS[row["orgaoJulgador"]],
                "area": c["area"] if c else "A classificar",
                "materia": c["materia"] if c else norm(row.get("questaoSubmetidaAJulgamento"))[:120],
                "alcance": alcance,
                "grupo": GRUPO[alcance],
                "providencia": PROVIDENCIA[alcance],
                "observacoes": (c or {}).get("observacoes", ""),
                "reclassificar": reclassificar,
                "conferido_em": (c or {}).get("conferido_em", ""),
                "situacao": row.get("situacao", ""),
                "primeira_afetacao": row.get("dataPrimeiraAfetacao") or "",
                "questao": norm(row.get("questaoSubmetidaAJulgamento")),
                "ordem_suspensao": ordem,
                "tese": norm(row.get("teseFirmada")),
                "processo_paradigma": norm(processo.get("Processo")),
                "link_stj": link_stj(tipo, numero, (processo.get("numeroRegistro") or "").strip()),
            }
        )
    pendentes.sort(key=lambda p: (p["tipo"], p["numero"] if isinstance(p["numero"], int) else 0))
    return pendentes, todos


def diferencas(anteriores: list[dict], atuais: list[dict], todos: dict[str, dict], data: str) -> list[dict]:
    antes = {p["id"]: p for p in anteriores}
    depois = {p["id"]: p for p in atuais}
    mud: list[dict] = []
    for rid, p in depois.items():
        a = antes.get(rid)
        if a is None:
            mud.append({"data": data, "id": rid, "tipo": "novo", "texto": f"Entrou na lista ({p['situacao']}).", "materia": p["materia"]})
            continue
        if a["situacao"] != p["situacao"]:
            mud.append({"data": data, "id": rid, "tipo": "situacao", "texto": f"Situação: {a['situacao']} → {p['situacao']}.", "materia": p["materia"]})
        if sha1(a.get("ordem_suspensao")) != sha1(p.get("ordem_suspensao")):
            mud.append({"data": data, "id": rid, "tipo": "suspensao", "texto": "A ordem de suspensão mudou: conferir e reclassificar.", "materia": p["materia"]})
        if a.get("alcance") != p["alcance"] and p["alcance"] != "A classificar":
            mud.append({"data": data, "id": rid, "tipo": "alcance", "texto": f"Alcance da suspensão: {a.get('alcance')} → {p['alcance']}.", "materia": p["materia"]})
    for rid, a in antes.items():
        if rid in depois:
            continue
        row = todos.get(rid, {})
        situacao = row.get("situacao") or "não consta mais da base"
        tese = norm(row.get("teseFirmada"))
        texto = f"Saiu da lista de pendentes: {situacao}."
        if tese and not tese.startswith("*"):
            texto += f" Tese: {tese[:280]}"
        mud.append({"data": data, "id": rid, "tipo": "saiu", "texto": texto, "materia": a["materia"]})
    return mud


def contagens(pendentes: list[dict]) -> dict:
    por: dict[str, int] = {}
    for p in pendentes:
        por[p["grupo"]] = por.get(p["grupo"], 0) + 1
    return {"total": len(pendentes), "por_grupo": por}


def feed(mudancas: list[dict], atualizado: str) -> str:
    itens = []
    for m in mudancas[:50]:
        titulo = f"{m['id']}: {m['texto'].split('.')[0]}"
        data = datetime.fromisoformat(m["data"].replace("Z", "+00:00"))
        guid = sha1(f"{m['data']}|{m['id']}|{m['tipo']}|{m['texto']}")
        itens.append(
            "<item>"
            f"<title>{escape(titulo)}</title>"
            f"<link>{escape(SITE + '#sel=' + m['id'].replace(' ', '%20'))}</link>"
            f"<description>{escape(m['materia'] + ' · ' + m['texto'])}</description>"
            f"<pubDate>{format_datetime(data)}</pubDate>"
            f'<guid isPermaLink="false">{guid}</guid>'
            "</item>"
        )
    data = datetime.fromisoformat(atualizado.replace("Z", "+00:00"))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        "<title>Radar de repetitivos do STJ (direito privado)</title>"
        f"<link>{SITE}</link>"
        "<description>Mudanças nos precedentes pendentes da 2ª Seção e da Corte Especial do STJ.</description>"
        "<language>pt-BR</language>"
        f"<lastBuildDate>{format_datetime(data)}</lastBuildDate>" + "".join(itens) + "</channel></rss>\n"
    )


def _ler_json(caminho: Path, padrao):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def _gravar_json(caminho: Path, dados) -> None:
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def registrar_tentativa(status: str, detalhe: str, dados: Path) -> None:
    meta = _ler_json(dados / "meta.json", {})
    meta["ultima_tentativa"] = {"em": agora(), "status": status, "detalhe": detalhe[:200]}
    _gravar_json(dados / "meta.json", meta)


def atualizar(temas_csv: str, processos_csv: str, base_stj: str, dados: Path = DADOS, raiz: Path = RAIZ, origem: str = "") -> int:
    temas = ler_csv(temas_csv)
    processos = ler_csv(processos_csv)
    if len(temas) < MIN_TEMAS:
        registrar_tentativa("erro", f"Temas.csv com {len(temas)} linhas (mínimo {MIN_TEMAS}); nada publicado.", dados)
        return 4
    classificacao = _ler_json(dados / "classificacao.json", {})
    pendentes, todos = montar(temas, processos, classificacao)
    anteriores = _ler_json(dados / "precedentes.json", [])
    if anteriores and len(pendentes) < (1 - QUEDA_MAXIMA) * len(anteriores):
        registrar_tentativa("erro", f"Queda suspeita: {len(anteriores)} → {len(pendentes)} pendentes; nada publicado.", dados)
        return 4
    data = agora()
    novas = diferencas(anteriores, pendentes, todos, data) if anteriores else []
    historico = _ler_json(dados / "mudancas.json", [])
    if not anteriores and not historico:
        novas = [{"data": data, "id": "Base", "tipo": "base", "texto": "Primeira publicação do radar.", "materia": f"{len(pendentes)} precedentes pendentes"}]
    mudou = bool(novas) or pendentes != anteriores
    meta = _ler_json(dados / "meta.json", {})
    if mudou:
        historico = (novas + historico)[:MAX_MUDANCAS]
        _gravar_json(dados / "precedentes.json", pendentes)
        _gravar_json(dados / "mudancas.json", historico)
        meta["atualizado_em"] = data
        (raiz / "feed.xml").write_text(feed(historico, data), encoding="utf-8")
    meta.update(
        {
            "base_stj": base_stj or meta.get("base_stj", ""),
            "contagens": contagens(pendentes),
            "classificacao_conferida_em": max((c.get("conferido_em", "") for c in classificacao.values()), default=""),
            "ultima_tentativa": {"em": data, "status": "ok", "detalhe": f"{len(novas)} mudança(s){' · ' + origem if origem else ''}"},
        }
    )
    meta.setdefault("atualizado_em", data)
    _gravar_json(dados / "meta.json", meta)
    print(f"pendentes={len(pendentes)} mudancas={len(novas)} a_classificar={sum(p['alcance'] == 'A classificar' for p in pendentes)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baixar", action="store_true", help="baixa do portal de dados abertos do STJ")
    ap.add_argument("--temas", type=Path)
    ap.add_argument("--processos", type=Path)
    ap.add_argument("--base-stj", default="", help="data da base, quando os arquivos são locais")
    ap.add_argument("--origem", default="", help="texto livre gravado em meta.json (ex.: Mac)")
    args = ap.parse_args(argv)
    if args.baixar:
        try:
            temas, processos, base = baixar()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            detalhe = f"{type(exc).__name__}: {getattr(exc, 'code', '') or getattr(exc, 'reason', '') or ''}"
            registrar_tentativa("bloqueado", detalhe, DADOS)
            print(f"::warning::portal do STJ inacessível ({detalhe}); nada mudou")
            return 3
    elif args.temas and args.processos:
        temas = args.temas.read_text(encoding="utf-8")
        processos = args.processos.read_text(encoding="utf-8")
        base = args.base_stj
    else:
        ap.error("use --baixar ou --temas e --processos")
    return atualizar(temas, processos, base, origem=args.origem)


if __name__ == "__main__":
    sys.exit(main())
