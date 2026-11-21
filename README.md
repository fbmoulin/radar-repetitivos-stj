# Radar de repetitivos do STJ (direito privado)

Página: **https://fbmoulin.github.io/radar-repetitivos-stj/**

Lista os precedentes qualificados **pendentes** da 2ª Seção e da Corte Especial do STJ e diz, para cada um, se a ordem de suspensão alcança processos no **1º grau** ou só recursos especiais no 2º grau e no STJ. Mostra também o que mudou desde a última atualização.

> Triagem para conferência, não é decisão. Antes de sobrestar um processo ou aplicar uma tese, confira a página do tema no STJ (cada precedente tem o link).

## Como receber as atualizações

- **Feed RSS:** `https://fbmoulin.github.io/radar-repetitivos-stj/feed.xml` (Feedly, Inoreader, Outlook, etc.).
- **Histórico no GitHub (Atom):** `https://github.com/fbmoulin/radar-repetitivos-stj/commits/main.atom`, que lista cada atualização dos dados.
- **Link direto para um tema:** na página, abra o precedente e use *Copiar link*.

## De onde vêm os dados

Portal de Dados Abertos do STJ, conjunto [Precedentes qualificados](https://dadosabertos.web.stj.jus.br/dataset/precedentes-qualificados) (arquivos Temas e Processos; licença CC BY). O robô consulta o portal todo dia, compara com a versão anterior e publica:

| Arquivo | Conteúdo |
|---|---|
| `data/precedentes.json` | precedentes pendentes com alcance da suspensão, providência e link do STJ |
| `data/mudancas.json` | histórico de mudanças (novo, saiu, situação, suspensão, alcance) |
| `data/meta.json` | datas da publicação, da base do STJ e da última verificação |
| `data/classificacao.json` | **classificação manual** do alcance de cada ordem de suspensão |
| `feed.xml` | RSS das mudanças |

## Como o alcance é classificado

A ordem de suspensão vem em texto livre no STJ. Cada uma foi lida no texto integral (filtro por palavras erra). Quando aparece um precedente novo, ou quando o texto de uma ordem muda, ele aparece como **"A classificar"** até nova conferência humana, que entra em `data/classificacao.json`.

## Atualização

- `.github/workflows/publicar.yml`: roda todo dia às 06h17 (Brasília), executa `scripts/atualizar.py --baixar` e publica no GitHub Pages.
- O portal do STJ pode recusar conexões de fora do Brasil, como a dos servidores do GitHub. Nesse caso a tentativa fica registrada como "bloqueado" e a atualização é feita por uma máquina no Brasil com o mesmo script.
- Proteções: o script não publica se a base vier pequena demais ou se o número de pendentes cair mais de 40% de uma vez.

Rodar localmente:

```bash
python3 scripts/atualizar.py --baixar          # baixa do portal do STJ
python3 scripts/atualizar.py --temas temas.csv --processos processos.csv --base-stj 2026-11-13
python3 -m pytest tests -q
python3 -m http.server 8000                    # abre em http://localhost:8000
```

## Créditos e licença

Dados: Superior Tribunal de Justiça, Portal de Dados Abertos (CC BY 4.0). Código: MIT. Classificação do alcance das suspensões feita com leitura humana e apoio de IA, sujeita a revisão (Res. CNJ 615/2025).
