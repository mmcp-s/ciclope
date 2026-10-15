from pathlib import Path

import pytest

from ciclope.config import RAIZ, ErroConfig, carregar, resolver_caminho


def test_exemplo_do_repositorio_e_valido():
    cfg = carregar(RAIZ / "ciclope.example.toml")
    assert cfg.modelo.fornecedor == "anthropic"
    assert cfg.voz.efeitos[:2] == ["speed", "0.92"]
    assert cfg.cara.porta_ws == 8765
    assert "português europeu" in cfg.personagem.sistema


def test_caminhos_relativos_sao_relativos_ao_toml(tmp_path: Path):
    toml = tmp_path / "x.toml"
    toml.write_text('[voz]\nmodelo = "vozes/v.onnx"\nvelocidade = 1\n', encoding="utf-8")
    cfg = carregar(toml)
    assert cfg.voz.velocidade == 1.0  # inteiro aceite onde se espera decimal
    assert resolver_caminho(cfg, cfg.voz.modelo) == tmp_path / "vozes" / "v.onnx"


def test_opcao_com_erro_de_escrita_e_recusada(tmp_path: Path):
    toml = tmp_path / "x.toml"
    toml.write_text("[voz]\nvelocidde = 0.9\n", encoding="utf-8")
    with pytest.raises(ErroConfig, match="velocidde"):
        carregar(toml)


def test_tipo_errado_e_recusado(tmp_path: Path):
    toml = tmp_path / "x.toml"
    toml.write_text('[cara]\nporta_ws = "8765"\n', encoding="utf-8")
    with pytest.raises(ErroConfig, match="porta_ws"):
        carregar(toml)


def test_efeitos_aceitam_numeros(tmp_path: Path):
    toml = tmp_path / "x.toml"
    toml.write_text('[voz]\nefeitos = ["speed", 0.92, "pitch", -250]\n', encoding="utf-8")
    assert carregar(toml).voz.efeitos == ["speed", "0.92", "pitch", "-250"]


def test_comando_pode_vir_depois_das_opcoes():
    from ciclope.main import _comando_primeiro

    assert _comando_primeiro(["-c", "x.toml", "voz"]) == ["voz", "-c", "x.toml"]
    assert _comando_primeiro(["--mudo", "dizer", "olá", "voz"]) == ["dizer", "--mudo", "olá", "voz"]
    assert _comando_primeiro(["--mudo"]) == ["texto", "--mudo"]
    assert _comando_primeiro([]) == ["texto"]


def test_ficheiro_inexistente(tmp_path: Path):
    with pytest.raises(ErroConfig):
        carregar(tmp_path / "nao-existe.toml")
