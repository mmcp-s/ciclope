"""Testa a leitura do streaming de cada fornecedor, sem rede.

Em vez de falar com a API a sério, substituímos a Session do requests por uma
falsa que devolve linhas SSE gravadas. Chama-se a isto um "duplo de teste".
"""

import json

import pytest

from ciclope.config import ConfigModelo
from ciclope.llm import ErroModelo, ModeloAnthropic, ModeloGemini, ModeloOpenAI, criar_modelo


class RespostaFalsa:
    def __init__(self, linhas: list[str], status: int = 200, texto: str = ""):
        self._linhas = linhas
        self.status_code = status
        self.text = texto
        self.url = "https://exemplo"
        self.encoding = None

    def iter_lines(self, decode_unicode=False):
        assert self.encoding == "utf-8", "o encoding tem de ser forçado antes de ler"
        yield from self._linhas

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SessaoFalsa:
    def __init__(self, resposta: RespostaFalsa):
        self.resposta = resposta
        self.pedido = None

    def post(self, url, **kwargs):
        self.pedido = {"url": url, **kwargs}
        return self.resposta


def sse(*eventos) -> list[str]:
    linhas = []
    for e in eventos:
        linhas += [f"data: {e if isinstance(e, str) else json.dumps(e)}", ""]
    return linhas


def test_anthropic_extrai_texto_e_ignora_outros_eventos():
    m = ModeloAnthropic(api_key="k")
    m._sessao = SessaoFalsa(RespostaFalsa(["event: message_start"] + sse(
        {"type": "message_start"},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Olá"}},
        {"type": "ping"},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ", Max"}},
        {"type": "message_stop"},
    )))
    assert "".join(m.stream([{"role": "user", "content": "oi"}], sistema="s")) == "Olá, Max"
    corpo = m._sessao.pedido["json"]
    assert corpo["stream"] is True and corpo["system"] == "s" and corpo["model"] == "claude-haiku-5-5"
    assert m._sessao.pedido["stream"] is True


def test_anthropic_erro_http_mostra_o_corpo():
    m = ModeloAnthropic(api_key="k")
    m._sessao = SessaoFalsa(RespostaFalsa([], status=401, texto='{"error":"invalid x-api-key"}'))
    with pytest.raises(ErroModelo, match="invalid x-api-key"):
        list(m.stream([{"role": "user", "content": "oi"}]))


def test_gemini_traduz_papeis_e_salta_pensamentos():
    m = ModeloGemini(api_key="k")
    m._sessao = SessaoFalsa(RespostaFalsa(sse(
        {"candidates": [{"content": {"parts": [{"text": "a pensar...", "thought": True}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "Sou o ciclope."}]}}]},
    )))
    msgs = [
        {"role": "user", "content": "quem és?"},
        {"role": "assistant", "content": "um ciclope"},
        {"role": "user", "content": "e então?"},
    ]
    assert "".join(m.stream(msgs, sistema="s")) == "Sou o ciclope."
    pedido = m._sessao.pedido
    assert pedido["url"].endswith(":streamGenerateContent?alt=sse")
    assert [c["role"] for c in pedido["json"]["contents"]] == ["user", "model", "user"]
    assert pedido["json"]["systemInstruction"]["parts"][0]["text"] == "s"
    assert pedido["json"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}


def test_openai_compativel_para_no_done_e_ignora_raciocinio():
    m = ModeloOpenAI(url_base="http://127.0.0.1:9931/", api_key="")
    m._sessao = SessaoFalsa(RespostaFalsa(sse(
        {"choices": [{"delta": {"reasoning_content": "hmm"}}]},
        {"choices": [{"delta": {"content": "Bom "}}]},
        {"choices": [{"delta": {"content": "dia."}}]},
        "[DONE]",
        {"choices": [{"delta": {"content": "isto já não conta"}}]},
    )))
    assert "".join(m.stream([{"role": "user", "content": "oi"}], sistema="s")) == "Bom dia."
    pedido = m._sessao.pedido
    assert pedido["url"] == "http://127.0.0.1:9931/v1/chat/completions"
    assert pedido["json"]["messages"][0] == {"role": "system", "content": "s"}
    assert "authorization" not in pedido["headers"]


def test_fabrica_escolhe_o_fornecedor(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    assert isinstance(criar_modelo(ConfigModelo(fornecedor="anthropic")), ModeloAnthropic)
    assert isinstance(criar_modelo(ConfigModelo(fornecedor="gemini")), ModeloGemini)
    assert isinstance(criar_modelo(ConfigModelo(fornecedor="openai")), ModeloOpenAI)
    with pytest.raises(ErroModelo):
        criar_modelo(ConfigModelo(fornecedor="outro"))


def test_sem_chave_da_erro_claro(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ErroModelo, match="ANTHROPIC_API_KEY"):
        ModeloAnthropic()
