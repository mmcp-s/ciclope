#!/usr/bin/env bash
# Descarrega a voz portuguesa do Piper (pt_PT-tugão-medium) para a pasta vozes/.
#
# Porque não `python -m piper.download_voices`: o descarregador do Piper falha
# com o "ã" do nome desta voz (UnicodeEncodeError). Aqui o "ã" vai codificado
# no endereço como %C3%A3, que é como os URLs representam caracteres não-ASCII.
#
# Uso: scripts/descarregar-voz.sh [pasta-de-destino]
set -euo pipefail

DESTINO="${1:-$(dirname "$0")/../vozes}"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_PT/tug%C3%A3o/medium"
REMOTO="pt_PT-tug%C3%A3o-medium"
LOCAL="pt_PT-tugão-medium"

mkdir -p "$DESTINO"
for extensao in onnx.json onnx; do
  echo "A descarregar $LOCAL.$extensao ..."
  # Descarrega para .part e só renomeia no fim: se a ligação cair a meio, não
  # fica um .onnx cortado que o Piper depois não consegue ler.
  curl -fL --progress-bar -o "$DESTINO/$LOCAL.$extensao.part" "$BASE/$REMOTO.$extensao"
  mv "$DESTINO/$LOCAL.$extensao.part" "$DESTINO/$LOCAL.$extensao"
done
echo "Pronto: $(cd "$DESTINO" && pwd)/$LOCAL.onnx"
