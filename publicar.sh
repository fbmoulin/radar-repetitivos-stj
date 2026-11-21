#!/bin/bash
# Publica o Radar de repetitivos do STJ no GitHub (rodar uma vez, no Mac, nesta pasta).
# Requer o gh autenticado (gh auth status). Cria o repositório público,
# envia o código e liga o GitHub Pages pelo Actions.
set -euo pipefail
REPO="${REPO:-fbmoulin/radar-repetitivos-stj}"

gh auth status >/dev/null
if gh repo view "$REPO" >/dev/null 2>&1; then
  echo "O repositório $REPO já existe; nada foi criado."
else
  gh repo create "$REPO" --public \
    --description "Repetitivos pendentes do STJ (direito privado): o que suspende o 1º grau e o que mudou" \
    --homepage "https://${REPO%%/*}.github.io/${REPO##*/}/"
fi

[ -d .git ] || git init -q -b main
git add -A
git diff --cached --quiet || git commit -q -m "Radar de repetitivos do STJ: primeira publicação"
git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$REPO.git"
git push -u origin main

# GitHub Pages com origem "GitHub Actions" (ignora erro se já estiver ligado).
gh api -X POST "repos/$REPO/pages" -f build_type=workflow >/dev/null 2>&1 \
  || gh api -X PUT "repos/$REPO/pages" -f build_type=workflow >/dev/null 2>&1 || true

gh workflow run publicar.yml --repo "$REPO" || true
echo "Pronto. Em 1 a 2 minutos: https://${REPO%%/*}.github.io/${REPO##*/}/"
