from ciclope.frases import dividir, limpar_para_voz


def em_pedacos(texto: str, tamanho: int = 7) -> list[str]:
    """Simula o modelo a mandar texto cortado em sítios arbitrários."""
    return [texto[i : i + tamanho] for i in range(0, len(texto), tamanho)]


def test_divide_em_frases_e_respeita_abreviaturas():
    texto = (
        "Olá Max! O Dr. Silva ligou hoje. Segundo ele, o resultado saiu bem... "
        "Queres que marque a consulta?"
    )
    assert list(dividir(em_pedacos(texto))) == [
        "Olá Max! O Dr. Silva ligou hoje.",
        "Segundo ele, o resultado saiu bem...",
        "Queres que marque a consulta?",
    ]


def test_nao_corta_numeros_decimais():
    texto = "O valor de pi é 3.14 mais ou menos, segundo dizem."
    # Todos os tamanhos de pedaço: o ponto pode chegar sozinho no fim de um.
    for tamanho in range(1, 12):
        assert list(dividir(em_pedacos(texto, tamanho))) == [texto], tamanho


def test_ponto_sozinho_num_pedaco_nao_fecha_a_frase():
    assert list(dividir(["O valor é ", "3", ".", "14", " mais ou menos."])) == ["O valor é 3.14 mais ou menos."]
    # As reticências chegam partidas mas ficam juntas, no fim da primeira frase.
    assert list(dividir(["Correu tudo bem", ".", ".. ou quase."])) == ["Correu tudo bem...", "ou quase."]
    assert list(dividir(["Ele disse «vou já", ".", "» E foi."])) == ["Ele disse «vou já.»", "E foi."]


def test_quebra_de_linha_fecha_frase():
    texto = "Primeiro ponto importante\nSegundo ponto importante\n"
    assert list(dividir(em_pedacos(texto, 5))) == ["Primeiro ponto importante", "Segundo ponto importante"]


def test_despeja_o_resto_sem_pontuacao():
    assert list(dividir(["Isto acaba sem ponto final"])) == ["Isto acaba sem ponto final"]


def test_junta_fragmentos_curtos():
    # "Sim." sozinho é curto demais e junta-se à frase seguinte.
    assert list(dividir(em_pedacos("Sim. Vou tratar disso amanhã de manhã."))) == [
        "Sim. Vou tratar disso amanhã de manhã."
    ]


def test_corta_frases_longas_numa_virgula():
    longa = "Temos maçãs, " * 20 + "e pronto."
    frases = list(dividir(em_pedacos(longa), max_chars=60))
    assert len(frases) > 1
    # Nem demasiado compridas, nem uma série de bocadinhos.
    assert all(40 <= len(f) <= 60 for f in frases[:-1])


def test_limpar_para_voz_tira_markdown():
    assert limpar_para_voz("**Atenção:** isto é `importante`") == "Atenção: isto é importante"
    assert limpar_para_voz("- primeiro item") == "primeiro item"
    assert limpar_para_voz("1. passo um") == "passo um"
    assert limpar_para_voz("Texto normal, sem nada.") == "Texto normal, sem nada."
