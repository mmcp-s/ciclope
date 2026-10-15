"""Botão e LEDs do reSpeaker 2-Mics Pi HAT V2.0 (só no Raspberry Pi).

- Botão: GPIO17, ativo em baixo (ligar o pino à massa quando carregas).
  No Pi 5 só o gpiozero com o backend lgpio funciona; o RPi.GPIO dos exemplos
  antigos da Seeed não suporta o chip do Pi 5.
- LEDs: três APA102 no barramento SPI 0, chip select 1 (/dev/spidev0.1).
  O SPI tem de estar ligado: sudo raspi-config nonint do_spi 0

Tudo aqui é opcional: se as bibliotecas ou o hardware faltarem, avisa e
continua sem botão ou sem LEDs.
"""

from __future__ import annotations

import os
from typing import Callable

# Cor (R, G, B) de cada estado do olho.
CORES = {
    "inativo": (0, 0, 0),
    "a_ouvir": (0, 60, 255),
    "a_pensar": (255, 110, 0),
    "a_falar": (255, 40, 0),
    "erro": (255, 0, 0),
}


class APA102:
    """Controlador mínimo para LEDs APA102.

    O protocolo é simples: 4 bytes a zero (início), depois 4 bytes por LED
    (0xE0 + brilho de 5 bits, azul, verde, vermelho), e bytes a 0xFF no fim
    para empurrar os dados até ao último LED da cadeia.
    """

    def __init__(self, n: int = 3, bus: int = 0, dispositivo: int = 1, velocidade_hz: int = 8_000_000):
        import spidev

        self.n = n
        self._spi = spidev.SpiDev()
        self._spi.open(bus, dispositivo)
        self._spi.max_speed_hz = velocidade_hz

    def mostrar(self, cor: tuple[int, int, int], brilho: int) -> None:
        r, g, b = cor
        dados = [0x00] * 4
        for _ in range(self.n):
            dados += [0xE0 | (brilho & 0x1F), b & 0xFF, g & 0xFF, r & 0xFF]
        dados += [0xFF] * 4
        self._spi.xfer2(dados)

    def fechar(self) -> None:
        self.mostrar((0, 0, 0), 0)
        self._spi.close()


class ReSpeaker:
    def __init__(self, botao_gpio: int = 17, leds: bool = True, brilho: int = 6):
        self.brilho = max(0, min(31, brilho))
        self._botao = None
        self._leds = None

        # No Pi 5 o gpiozero tem de usar o lgpio. Definir aqui evita que ele
        # tente outros backends primeiro e encha o terminal de avisos.
        os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")
        try:
            from gpiozero import Button

            # pull_up=True: o pino fica em 1 por omissão e vai a 0 quando
            # carregas; o gpiozero trata isso como "carregado".
            self._botao = Button(botao_gpio, pull_up=True, bounce_time=0.05)
        except Exception as e:
            print(f"  [hardware] botão indisponível: {e}")

        if leds:
            try:
                self._leds = APA102()
                self._leds.mostrar((0, 0, 0), 0)
            except Exception as e:
                print(f"  [hardware] LEDs indisponíveis (o SPI está ligado?): {e}")

    @property
    def tem_botao(self) -> bool:
        return self._botao is not None

    def ao_carregar(self, funcao: Callable[[], None]) -> None:
        """Chama `funcao` quando o botão é carregado (num fio do gpiozero)."""
        if self._botao is not None:
            self._botao.when_pressed = funcao

    def estado(self, estado: str) -> None:
        if self._leds is not None:
            try:
                self._leds.mostrar(CORES.get(estado, (0, 0, 0)), self.brilho)
            except OSError:
                pass

    def fechar(self) -> None:
        if self._leds is not None:
            self._leds.fechar()
        if self._botao is not None:
            self._botao.close()
