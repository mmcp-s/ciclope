// O olho: estado da animação e desenho.
//
// É a mesma lógica do simulador (simulador/index.html), com os mesmos
// estados, proporções e tempos. Afina a expressão no browser, que é muito
// mais rápido, e depois copia os números para aqui.
#pragma once
#include <Arduino_GFX_Library.h>
#include <vector>

enum class Estado : uint8_t { Inativo, AOuvir, APensar, AFalar, Erro };

Estado estadoDeTexto(const char *texto);

// Segundos desde o arranque. Vem do esp_timer, que conta microssegundos em
// 64 bits: não dá a volta aos 49 dias como o millis(), e um double mantém a
// precisão ao microssegundo durante anos. Um float de segundos, pelo
// contrário, já só distinguiria passos de 8 ms ao fim de um dia ligado.
double segundos();

class Olho {
 public:
  void definirEstado(Estado e);
  void iniciarFala(std::vector<uint8_t> &&envelope, uint16_t janelaMs, double agora);
  void definirLigado(bool ligado) { semLigacao = !ligado; }

  // agora em segundos (de segundos()), dt em segundos.
  void atualizar(double agora, float dt);
  void desenhar(Arduino_GFX *tela);

 private:
  float volumeAtual(double agora);
  void escolherOlhar(float &x, float &y, float &espera);

  Estado estado = Estado::Inativo;
  bool semLigacao = true;

  // valores atuais (suavizados) e alvos
  float olharX = 0, olharY = 0, alvoX = 0, alvoY = 0;
  float pupila = 0.42f, brilho = 0.25f, cima = 0.10f, baixo = 0.04f, cor = 0;
  float piscar = 0;
  int pensarLado = 1;

  // instantes, em segundos
  double proximoOlhar = 0;
  double piscarInicio = -1, proximoPiscar = 1.5;

  // envelope da fala
  std::vector<uint8_t> env;
  uint16_t envMs = 30;
  double envInicio = 0;
};
