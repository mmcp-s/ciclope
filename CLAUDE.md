# Notas para o Claude Code

- Tudo em **português europeu**: código (nomes), comentários, mensagens, documentação.
- A dona do projeto está a aprender: os comentários explicam os conceitos
  (geradores, buffers, RMS...) quando aparecem pela primeira vez. Mantém esse
  tom ao acrescentar código; não tires explicações existentes.
- Mapa: `ciclope/__init__.py` descreve cada módulo; `docs/arquitetura.md`
  explica as decisões e o protocolo do olho.
- O olho existe em dois sítios com a **mesma lógica**: `simulador/index.html`
  (JavaScript) e `firmware/olho/src/olho.cpp` (C++). Uma mudança num tem de ir
  para o outro.
- Testes: `pytest` (não precisam de rede, som nem voz). Corre-os antes de
  qualquer commit.
- Segredos nunca entram no git: `.env`, `ciclope.toml` e
  `firmware/olho/include/config.h` estão no `.gitignore`.
- Imports pesados (piper, faster_whisper, openwakeword, sounddevice, gpiozero,
  spidev) ficam dentro das funções/construtores, para o modo texto e os testes
  funcionarem sem eles.
