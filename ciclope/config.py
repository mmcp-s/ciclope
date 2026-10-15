"""Configuração: lê o ciclope.toml e as chaves do .env.

Duas fontes, por uma razão: o ciclope.toml diz COMO o ciclope se comporta e
pode ser partilhado; o .env guarda SEGREDOS (chaves de API) e nunca sai da tua
máquina. Misturá-los levaria, mais cedo ou mais tarde, a uma chave no git.

Cada secção do TOML corresponde a uma dataclass abaixo. Uma dataclass é uma
classe que só guarda dados: o Python gera sozinho o construtor a partir dos
campos declarados, com os valores por omissão que lá estão.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import load_dotenv

# Pasta raiz do repositório (a que contém este pacote).
RAIZ = Path(__file__).resolve().parent.parent

SISTEMA_PREDEFINIDO = """És um ciclope antigo que vive numa caverna. As tuas respostas vão ser
lidas em voz alta, por isso:
- Responde sempre em português europeu de Portugal, tratando o interlocutor por tu. Nunca uses "você".
- Sê breve: duas ou três frases, salvo se te pedirem detalhe.
- Nada de listas, marcadores, tabelas, emojis ou markdown — só texto corrido.
- Escreve os números por extenso."""


@dataclass
class ConfigModelo:
    fornecedor: str = "anthropic"
    nome: str = ""
    max_tokens: int = 300
    historico: int = 12
    url_base: str = "http://127.0.0.1:9931"
    nivel_raciocinio: str = "low"


@dataclass
class ConfigPersonagem:
    sistema: str = SISTEMA_PREDEFINIDO


@dataclass
class ConfigVoz:
    modelo: str = "vozes/pt_PT-tugão-medium.onnx"
    velocidade: float = 1.0
    # field(default_factory=...) porque uma lista é mutável: se fosse um valor
    # por omissão normal, todas as instâncias partilhariam a MESMA lista.
    efeitos: list[str] = field(
        default_factory=lambda: ["speed", "0.92", "pitch", "-250", "reverb", "40", "50", "100"]
    )


@dataclass
class ConfigAudio:
    saida: str = ""
    entrada: str = ""


@dataclass
class ConfigOuvidos:
    ativacao: str = "enter"
    palavra: str = "hey_jarvis"
    limiar_palavra: float = 0.5
    whisper: str = "small"
    idioma: str = "pt"
    silencio_fim_ms: int = 800
    espera_max_s: float = 6.0
    fala_max_s: float = 15.0
    agressividade_vad: int = 2


@dataclass
class ConfigCara:
    ativa: bool = True
    porta_ws: int = 8765
    porta_http: int = 8080
    janela_ms: int = 30


@dataclass
class ConfigHardware:
    respeaker: bool = False
    botao_gpio: int = 17
    leds: bool = True
    brilho_leds: int = 6


@dataclass
class Config:
    modelo: ConfigModelo = field(default_factory=ConfigModelo)
    personagem: ConfigPersonagem = field(default_factory=ConfigPersonagem)
    voz: ConfigVoz = field(default_factory=ConfigVoz)
    audio: ConfigAudio = field(default_factory=ConfigAudio)
    ouvidos: ConfigOuvidos = field(default_factory=ConfigOuvidos)
    cara: ConfigCara = field(default_factory=ConfigCara)
    hardware: ConfigHardware = field(default_factory=ConfigHardware)
    # De onde veio a configuração (None = só valores por omissão).
    origem: Path | None = None


class ErroConfig(Exception):
    pass


def _preencher(destino: object, dados: dict, secao: str) -> None:
    """Copia os valores de uma secção do TOML para a dataclass correspondente.

    Recusa chaves desconhecidas: um erro de escrita como `velocidde = 0.9`
    seria, de outro modo, ignorado em silêncio — e passavas uma tarde a
    perguntar porque é que a velocidade não muda.
    """
    validos = {f.name: f for f in fields(destino)}
    for chave, valor in dados.items():
        if chave not in validos:
            opcoes = ", ".join(validos)
            raise ErroConfig(f"[{secao}] não tem a opção '{chave}'. Opções válidas: {opcoes}")
        atual = getattr(destino, chave)
        # O TOML distingue 1 de 1.0; aqui aceitamos um inteiro onde se espera decimal.
        if isinstance(atual, float) and isinstance(valor, int) and not isinstance(valor, bool):
            valor = float(valor)
        # Listas de texto (como os efeitos do SoX): aceita números e converte-os,
        # porque escrever ["speed", 0.92] é o mais natural.
        if isinstance(atual, list) and isinstance(valor, list):
            convertidos = []
            for item in valor:
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    item = str(item)
                if not isinstance(item, str):
                    raise ErroConfig(f"[{secao}] {chave} só aceita texto e números, mas tem {item!r}")
                convertidos.append(item)
            valor = convertidos
        if atual is not None and not isinstance(valor, type(atual)):
            raise ErroConfig(
                f"[{secao}] {chave} devia ser {type(atual).__name__}, "
                f"mas é {type(valor).__name__}: {valor!r}"
            )
        setattr(destino, chave, valor)


def carregar(caminho: str | Path | None = None) -> Config:
    """Lê a configuração e as variáveis do .env.

    Procura, por esta ordem: o caminho dado, ./ciclope.toml, <raiz>/ciclope.toml.
    Se não encontrar nenhum, usa os valores por omissão.
    """
    # O .env da pasta atual tem prioridade sobre o da raiz do repositório.
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(RAIZ / ".env")

    candidatos = [Path(caminho)] if caminho else [Path.cwd() / "ciclope.toml", RAIZ / "ciclope.toml"]
    config = Config()

    for candidato in candidatos:
        if candidato.is_file():
            with open(candidato, "rb") as f:  # tomllib exige o ficheiro em modo binário
                dados = tomllib.load(f)
            for secao, valores in dados.items():
                if not hasattr(config, secao) or secao == "origem":
                    raise ErroConfig(f"Secção desconhecida no {candidato.name}: [{secao}]")
                if not isinstance(valores, dict):
                    raise ErroConfig(f"'{secao}' devia ser uma secção [{secao}]")
                _preencher(getattr(config, secao), valores, secao)
            config.origem = candidato.resolve()
            break
    else:
        if caminho:
            raise ErroConfig(f"Não encontrei o ficheiro de configuração: {caminho}")

    return config


def resolver_caminho(config: Config, caminho: str) -> Path:
    """Caminhos relativos no TOML são relativos ao próprio ficheiro TOML."""
    p = Path(caminho).expanduser()
    if p.is_absolute():
        return p
    base = config.origem.parent if config.origem else RAIZ
    return (base / p).resolve()
