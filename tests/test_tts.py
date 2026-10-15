import shutil

import numpy as np
import pytest

from ciclope.audio import reamostrar
from ciclope.tts import Fala, aplicar_efeitos


def silaba(taxa: int = 22050, segundos: float = 1.0) -> np.ndarray:
    """Um tom de 180 Hz que sobe e desce de volume, como uma sílaba longa."""
    t = np.arange(int(taxa * segundos)) / taxa
    return (8000 * np.sin(2 * np.pi * 180 * t) * np.sin(np.pi * t / segundos)).astype(np.int16)


def test_envelope_tem_uma_amostra_por_janela_e_fica_entre_0_e_255():
    fala = Fala("teste", silaba(), 22050)
    env = fala.envelope(30)
    assert len(env) == int(np.ceil(22050 / (22050 * 0.03)))  # 34 janelas num segundo
    assert all(0 <= v <= 255 for v in env)
    assert max(env) == 255
    # Sobe até ao meio e desce depois: o meio é mais alto do que as pontas.
    assert env[len(env) // 2] > env[0] and env[len(env) // 2] > env[-1]


def test_envelope_de_silencio_e_zeros():
    assert Fala("", np.zeros(2205, dtype=np.int16), 22050).envelope(30) == [0] * 4


def test_envelope_vazio():
    assert Fala("", np.zeros(0, dtype=np.int16), 22050).envelope(30) == []


def test_duracao():
    assert Fala("", np.zeros(44100, dtype=np.int16), 22050).duracao == pytest.approx(2.0)


def test_reamostrar_muda_o_comprimento_na_proporcao():
    x = silaba(22050, 0.5)
    y = reamostrar(x, 22050, 48000)
    assert len(y) == round(len(x) * 48000 / 22050)
    assert y.dtype == np.int16
    assert reamostrar(x, 22050, 22050) is x


def test_sem_efeitos_devolve_a_mesma_fala():
    fala = Fala("x", silaba(), 22050)
    assert aplicar_efeitos(fala, []) is fala


@pytest.mark.skipif(shutil.which("sox") is None, reason="SoX não instalado")
def test_speed_estica_a_duracao_e_mantem_a_taxa():
    fala = Fala("x", silaba(), 22050)
    lenta = aplicar_efeitos(fala, ["speed", "0.8"])
    assert lenta.taxa == 22050
    assert lenta.duracao == pytest.approx(fala.duracao / 0.8, rel=0.02)
