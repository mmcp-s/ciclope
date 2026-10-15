"""Modelos de linguagem: quem escreve as respostas do ciclope.

Todos os fornecedores cumprem o mesmo contrato, a classe abstrata `Modelo`:
um método `stream(mensagens, sistema)` que devolve pedaços de texto à medida
que são gerados. O resto do programa nunca sabe com quem está a falar — trocar
de fornecedor é mudar uma linha no ciclope.toml.

Os três falam por HTTP com streaming em SSE (Server-Sent Events): o servidor
manda linhas de texto `data: {...json...}` à medida que gera, separadas por
linhas vazias. Cada fornecedor arruma o JSON à sua maneira, e é só isso que
muda entre as três implementações.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Iterator

import requests

from ciclope.config import ConfigModelo

# Ligação: 10 s para estabelecer; leitura: até 120 s sem receber nada.
# Um modelo local lento pode demorar a mandar o primeiro pedaço.
_TIMEOUT = (10, 120)


class ErroModelo(Exception):
    pass


class Modelo(ABC):
    """Contrato comum. Qualquer fornecedor novo só tem de implementar stream()."""

    fornecedor: str = "?"

    def __init__(self, nome: str, max_tokens: int):
        self.nome = nome
        self.max_tokens = max_tokens
        # Uma Session reaproveita a ligação TLS entre perguntas (keep-alive).
        # Sem ela, cada pergunta repetia o aperto de mão TLS — umas centenas
        # de milissegundos a mais no tempo até à primeira palavra.
        self._sessao = requests.Session()

    @abstractmethod
    def stream(self, mensagens: list[dict], sistema: str = "") -> Iterator[str]:
        """Recebe [{'role': 'user'|'assistant', 'content': str}, ...].

        É um gerador: devolve pedaços de texto à medida que chegam.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.nome!r})"


def _verificar(resposta: requests.Response) -> None:
    """Como raise_for_status(), mas inclui o corpo da resposta na mensagem.

    É no corpo que o fornecedor explica o problema ("chave inválida",
    "modelo não existe", "sem créditos"). O raise_for_status() deita-o fora.
    """
    if resposta.status_code >= 400:
        try:
            corpo = resposta.text[:600]
        except Exception:
            corpo = "(sem corpo)"
        raise ErroModelo(f"HTTP {resposta.status_code} de {resposta.url}: {corpo}")


def _eventos_sse(resposta: requests.Response) -> Iterator[str]:
    """Extrai o conteúdo das linhas `data:` de uma resposta SSE."""
    # Detalhe que estraga os acentos sem dar erro: para conteúdo text/*, se o
    # servidor não disser o charset, o requests assume ISO-8859-1 e o "ã"
    # chega como "Ã£". Forçamos UTF-8, que é o que estes servidores mandam.
    resposta.encoding = "utf-8"
    for linha in resposta.iter_lines(decode_unicode=True):
        if linha and linha.startswith("data:"):
            yield linha[5:].lstrip()


class ModeloAnthropic(Modelo):
    fornecedor = "anthropic"
    PREDEFINIDO = "claude-haiku-5-5"

    def __init__(self, nome: str = "", max_tokens: int = 300, api_key: str | None = None):
        super().__init__(nome or self.PREDEFINIDO, max_tokens)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ErroModelo("Falta ANTHROPIC_API_KEY no .env")

    def stream(self, mensagens: list[dict], sistema: str = "") -> Iterator[str]:
        corpo = {
            "model": self.nome,
            "max_tokens": self.max_tokens,
            "messages": mensagens,
            "stream": True,  # instrução PARA O SERVIDOR: manda aos pedaços
        }
        if sistema:
            corpo["system"] = sistema

        # stream=True no requests é outra coisa: instrução PARA A BIBLIOTECA,
        # para não esperar pelo corpo todo. Precisamos dos dois.
        # O `with` fecha a ligação se o gerador for abandonado a meio
        # (por exemplo, se carregares no botão para o calar).
        with self._sessao.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=corpo,
            stream=True,
            timeout=_TIMEOUT,
        ) as resposta:
            _verificar(resposta)
            for dados in _eventos_sse(resposta):
                try:
                    evento = json.loads(dados)
                except json.JSONDecodeError:
                    continue
                tipo = evento.get("type")
                if tipo == "content_block_delta":
                    delta = evento.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]
                elif tipo == "error":
                    raise ErroModelo(f"Erro da API a meio da resposta: {evento.get('error')}")


class ModeloGemini(Modelo):
    fornecedor = "gemini"
    PREDEFINIDO = "gemini-3.5-flash-lite"

    def __init__(
        self,
        nome: str = "",
        max_tokens: int = 300,
        api_key: str | None = None,
        nivel_raciocinio: str = "low",
    ):
        super().__init__(nome or self.PREDEFINIDO, max_tokens)
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ErroModelo("Falta GOOGLE_API_KEY no .env")
        self.nivel_raciocinio = nivel_raciocinio

    def stream(self, mensagens: list[dict], sistema: str = "") -> Iterator[str]:
        # A Google chama "model" ao papel que a Anthropic chama "assistant".
        # A tradução fica fechada aqui dentro: o resto do programa não sabe.
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in mensagens
        ]
        config_geracao: dict = {"maxOutputTokens": self.max_tokens}
        if self.nivel_raciocinio:
            # Nos Flash 3.x o "pensamento" conta para o limite de tokens e
            # atrasa a primeira palavra. Pedimos o mínimo razoável.
            config_geracao["thinkingConfig"] = {"thinkingLevel": self.nivel_raciocinio}

        corpo: dict = {"contents": contents, "generationConfig": config_geracao}
        if sistema:
            corpo["systemInstruction"] = {"parts": [{"text": sistema}]}

        # ?alt=sse é o equivalente ao "stream": true da Anthropic, mas no URL.
        # Sem ele, a Google devolve um array JSON gigante no fim.
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.nome}:streamGenerateContent?alt=sse"
        )
        with self._sessao.post(
            url,
            headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
            json=corpo,
            stream=True,
            timeout=_TIMEOUT,
        ) as resposta:
            _verificar(resposta)
            for dados in _eventos_sse(resposta):
                try:
                    evento = json.loads(dados)
                except json.JSONDecodeError:
                    continue
                for candidato in evento.get("candidates", []):
                    for parte in candidato.get("content", {}).get("parts", []):
                        # Partes marcadas como "thought" são raciocínio interno, não resposta.
                        if parte.get("thought"):
                            continue
                        if parte.get("text"):
                            yield parte["text"]


class ModeloOpenAI(Modelo):
    """Qualquer servidor compatível com o formato da OpenAI.

    Serve para o llama-server (llama.cpp) ou o Ollama a correr no próprio Pi,
    e para serviços remotos como OpenRouter, Groq ou Mistral. Muda só o URL.
    """

    fornecedor = "openai"

    def __init__(
        self,
        nome: str = "",
        max_tokens: int = 300,
        url_base: str = "http://127.0.0.1:9931",
        api_key: str | None = None,
    ):
        super().__init__(nome or "local", max_tokens)
        self.url_base = url_base.rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")

    def stream(self, mensagens: list[dict], sistema: str = "") -> Iterator[str]:
        msgs = ([{"role": "system", "content": sistema}] if sistema else []) + mensagens
        cabecalhos = {"content-type": "application/json"}
        if self.api_key:
            cabecalhos["authorization"] = f"Bearer {self.api_key}"

        with self._sessao.post(
            f"{self.url_base}/v1/chat/completions",
            headers=cabecalhos,
            json={
                "model": self.nome,
                "messages": msgs,
                "max_tokens": self.max_tokens,
                "stream": True,
            },
            stream=True,
            timeout=_TIMEOUT,
        ) as resposta:
            _verificar(resposta)
            for dados in _eventos_sse(resposta):
                if dados == "[DONE]":
                    break
                try:
                    evento = json.loads(dados)
                except json.JSONDecodeError:
                    continue
                for escolha in evento.get("choices") or []:
                    # Ignoramos reasoning_content / reasoning (raciocínio dos
                    # modelos que pensam) e ficamos só com o texto da resposta.
                    texto = (escolha.get("delta") or {}).get("content")
                    if texto:
                        yield texto


def criar_modelo(cfg: ConfigModelo) -> Modelo:
    """Fábrica: a única função que sabe que fornecedores existem."""
    f = cfg.fornecedor.lower()
    if f == "anthropic":
        return ModeloAnthropic(cfg.nome, cfg.max_tokens)
    if f == "gemini":
        return ModeloGemini(cfg.nome, cfg.max_tokens, nivel_raciocinio=cfg.nivel_raciocinio)
    if f == "openai":
        return ModeloOpenAI(cfg.nome, cfg.max_tokens, url_base=cfg.url_base)
    raise ErroModelo(f"Fornecedor desconhecido: {cfg.fornecedor!r} (usa anthropic, gemini ou openai)")
