#!/usr/bin/env bash
# Instala o reSpeaker 2-Mics Pi HAT V2.0 (codec TLV320AIC3104) num Raspberry Pi 5.
#
# Faz o mesmo que os passos do wiki da Seeed
# (https://wiki.seeedstudio.com/respeaker_2_mics_pi_hat_raspberry_v2/), com três
# cuidados a mais: pode correr-se duas vezes sem duplicar a linha no
# config.txt, garante que a linha fica numa secção [all], e liga o SPI para
# os LEDs.
#
# NÃO serve para a V1 (codec WM8960). Na V1 usava-se o seeed-voicecard, que a
# própria Seeed desaconselha no Raspberry Pi OS atual.
set -euo pipefail

CONFIG=/boot/firmware/config.txt
OVERLAY=respeaker-2mic-v2_0

echo "1/4 · A instalar as ferramentas para compilar o overlay..."
sudo apt-get update -q
sudo apt-get install -y -q git build-essential device-tree-compiler

echo "2/4 · A compilar o overlay a partir da versão atual da Seeed..."
# Usa sempre a versão atual: as anteriores a dezembro de 2024 davam ruído
# forte no Pi 5 (commit 11d38c9 do repositório da Seeed).
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 https://github.com/Seeed-Studio/seeed-linux-dtoverlays.git "$TMP/dto"
make -C "$TMP/dto" "overlays/rpi/$OVERLAY-overlay.dtbo"
sudo cp "$TMP/dto/overlays/rpi/$OVERLAY-overlay.dtbo" "/boot/firmware/overlays/$OVERLAY.dtbo"

echo "3/4 · A ativar o overlay em $CONFIG..."
# Só conta como ativo se a linha estiver antes de qualquer secção ou numa
# secção [all]. Debaixo de [pi4], por exemplo, não vale para o Pi 5.
if awk -v linha="dtoverlay=$OVERLAY" '
     /^\[/ { secao = $0 }
     $0 == linha && (secao == "" || secao == "[all]") { ativo = 1 }
     END { exit !ativo }' "$CONFIG"; then
  echo "    (já estava ativo)"
else
  sudo cp "$CONFIG" "$CONFIG.antes-do-respeaker"
  # [all] garante que a linha vale para qualquer modelo, mesmo que o ficheiro
  # acabe numa secção específica como [pi4] ou [cm5].
  printf '\n[all]\ndtoverlay=%s\n' "$OVERLAY" | sudo tee -a "$CONFIG" > /dev/null
  echo "    (cópia de segurança em $CONFIG.antes-do-respeaker)"
fi

echo "4/4 · A ligar o SPI (para os LEDs)..."
sudo raspi-config nonint do_spi 0

echo
echo "Feito. Reinicia com:  sudo reboot"
echo "Depois confirma com:  arecord -l   (deve aparecer seeed2micvoicec)"
