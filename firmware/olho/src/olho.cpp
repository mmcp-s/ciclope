#include "olho.h"

#include <esp_random.h>
#include <esp_timer.h>
#include <math.h>
#include <string.h>

// ---------- parâmetros de cada estado (iguais aos do simulador) ----------
struct Parametros {
  float pupila;         // raio da pupila em fração do raio da íris
  float brilho;         // 0 a 1
  float palpebraCima;   // fecho em repouso, 0 a 1
  float palpebraBaixo;
};

static const Parametros PARAMETROS[] = {
    /* Inativo */ {0.42f, 0.25f, 0.10f, 0.04f},
    /* AOuvir  */ {0.56f, 0.60f, 0.00f, 0.00f},
    /* APensar */ {0.36f, 0.45f, 0.20f, 0.06f},
    /* AFalar  */ {0.42f, 0.45f, 0.06f, 0.03f},
    /* Erro    */ {0.30f, 0.20f, 0.45f, 0.20f},
};

struct Cor {
  float r, g, b;
};

static const Cor ESCLERA = {239, 230, 216};
static const Cor ESCLERA_BORDA = {185, 169, 145};
static const Cor IRIS_FORA = {90, 30, 5};
static const Cor IRIS_DENTRO = {245, 160, 40};
static const Cor IRIS_ERRO_FORA = {80, 10, 5};
static const Cor IRIS_ERRO_DENTRO = {230, 60, 40};
static const Cor PUPILA = {20, 12, 6};
static const Cor REFLEXO = {255, 248, 236};
static const Cor PELE = {92, 70, 58};
static const Cor PELE_BORDA = {60, 42, 34};

// ---------- utilitários ----------
static float aleatorio(float a, float b) { return a + (b - a) * (esp_random() / 4294967295.0f); }
static float limitar(float x, float a, float b) { return x < a ? a : (x > b ? b : x); }

// Aproximação exponencial: cada fotograma anda uma fração do caminho até ao
// alvo. tau é o tempo (s) para percorrer ~63% da distância.
static float aproximar(float atual, float alvo, float dt, float tau) {
  return atual + (alvo - atual) * (1.0f - expf(-dt / tau));
}

static Cor misturar(Cor a, Cor b, float t) {
  return {a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t};
}
static Cor escalar(Cor c, float k) { return {c.r * k, c.g * k, c.b * k}; }
static Cor dessaturar(Cor c, float t) {
  float cinza = 0.3f * c.r + 0.59f * c.g + 0.11f * c.b;
  return misturar(c, {cinza, cinza, cinza}, t);
}

// O ecrã usa 16 bits por píxel: 5 para o vermelho, 6 para o verde, 5 para o azul.
static uint16_t rgb565(Cor c) {
  uint8_t r = (uint8_t)limitar(c.r, 0, 255), g = (uint8_t)limitar(c.g, 0, 255), b = (uint8_t)limitar(c.b, 0, 255);
  return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

double segundos() { return esp_timer_get_time() / 1e6; }

Estado estadoDeTexto(const char *t) {
  if (!t) return Estado::Inativo;
  if (!strcmp(t, "a_ouvir")) return Estado::AOuvir;
  if (!strcmp(t, "a_pensar")) return Estado::APensar;
  if (!strcmp(t, "a_falar")) return Estado::AFalar;
  if (!strcmp(t, "erro")) return Estado::Erro;
  return Estado::Inativo;
}

// ---------- lógica ----------
void Olho::definirEstado(Estado e) {
  estado = e;
  proximoOlhar = 0;  // escolhe já um olhar adequado ao novo estado
}

void Olho::iniciarFala(std::vector<uint8_t> &&envelope, uint16_t janelaMs, double agora) {
  env = std::move(envelope);
  envMs = janelaMs ? janelaMs : 30;
  envInicio = agora;
}

float Olho::volumeAtual(double agora) {
  if (env.size() < 2) return 0;
  double pos = (agora - envInicio) * 1000.0 / envMs;
  if (pos < 0) return 0;  // a mensagem chegou "no futuro": ainda não começou
  if (pos >= env.size() - 1) {
    env.clear();
    return 0;
  }
  size_t i = (size_t)pos;
  float f = (float)(pos - i);
  return ((1 - f) * env[i] + f * env[i + 1]) / 255.0f;
}

void Olho::escolherOlhar(float &x, float &y, float &espera) {
  switch (estado) {
    case Estado::AOuvir:  // atento: olha em frente, quase parado
      x = aleatorio(-0.08f, 0.08f); y = aleatorio(-0.08f, 0.06f); espera = aleatorio(0.6f, 1.5f);
      break;
    case Estado::APensar:  // alterna entre cima-esquerda e cima-direita
      pensarLado = -pensarLado;
      x = 0.45f * pensarLado; y = aleatorio(-0.55f, -0.4f); espera = aleatorio(0.9f, 1.6f);
      break;
    case Estado::AFalar:
      x = aleatorio(-0.2f, 0.2f); y = aleatorio(-0.15f, 0.15f); espera = aleatorio(1.0f, 2.5f);
      break;
    case Estado::Erro:
      x = aleatorio(-0.1f, 0.1f); y = 0.3f; espera = aleatorio(2.0f, 4.0f);
      break;
    default:  // inativo: deriva pelo espaço
      x = aleatorio(-0.55f, 0.55f); y = aleatorio(-0.45f, 0.45f); espera = aleatorio(1.5f, 4.0f);
  }
}

void Olho::atualizar(double agora, float dt) {
  const Parametros &p = PARAMETROS[(int)estado];

  // Olhar: movimentos rápidos (sacadas) para pontos escolhidos a intervalos irregulares.
  if (agora >= proximoOlhar) {
    float espera;
    escolherOlhar(alvoX, alvoY, espera);
    proximoOlhar = agora + espera;
  }
  olharX = aproximar(olharX, alvoX, dt, 0.06f);
  olharY = aproximar(olharY, alvoY, dt, 0.06f);

  // Fala: a pupila contrai e a íris acende com o volume.
  float v = volumeAtual(agora);
  float alvoBrilho = estado == Estado::AFalar ? p.brilho + 0.55f * v : p.brilho;
  pupila = aproximar(pupila, p.pupila - 0.12f * v, dt, 0.25f);
  brilho = aproximar(brilho, alvoBrilho, dt, 0.08f);
  cor = aproximar(cor, estado == Estado::Erro ? 1.0f : 0.0f, dt, 0.3f);

  // Pálpebras em repouso; sem ligação, o olho fica sonolento.
  float sono = semLigacao ? 0.3f : 0.0f;
  cima = aproximar(cima, fmaxf(p.palpebraCima, sono), dt, 0.15f);
  baixo = aproximar(baixo, p.palpebraBaixo, dt, 0.15f);

  // Piscar: fecha em 40% do tempo, abre nos restantes 60%. Às vezes duas vezes.
  if (piscarInicio < 0 && agora >= proximoPiscar) piscarInicio = agora;
  piscar = 0;
  if (piscarInicio >= 0) {
    float t = (float)((agora - piscarInicio) / 0.16);
    if (t >= 1) {
      piscarInicio = -1;
      proximoPiscar = agora + (aleatorio(0, 1) < 0.2f ? 0.12f : aleatorio(2.0f, 6.0f));
    } else {
      piscar = t < 0.4f ? t / 0.4f : 1 - (t - 0.4f) / 0.6f;
    }
  }
}

// ---------- desenho ----------
void Olho::desenhar(Arduino_GFX *tela) {
  const int W = tela->width();
  const float R = W / 2.0f, cx = R, cy = R;
  const float IR = 0.48f * R;            // raio da íris
  const float D = R - IR - 0.06f * R;    // quanto a íris se pode afastar do centro
  const int ix = (int)(cx + olharX * D), iy = (int)(cy + olharY * D);
  const float sat = semLigacao ? 0.75f : 0.0f;

  // Esclera, com a borda mais escura a dar volume.
  // Não limpamos o ecrã: a esclera cobre o círculo visível, e os cantos do
  // quadrado ficam fora do vidro redondo. Poupa uma escrita de 460 KB por fotograma.
  tela->fillCircle(cx, cy, R, rgb565(ESCLERA_BORDA));
  tela->fillCircle(cx, cy, R * 0.97f, rgb565(misturar(ESCLERA_BORDA, ESCLERA, 0.5f)));
  tela->fillCircle(cx, cy, R * 0.93f, rgb565(ESCLERA));

  // Íris: dez anéis do escuro (fora) para o claro (dentro); o primeiro é a orla.
  const float g = 0.55f + 0.45f * brilho;
  const Cor fora = misturar(IRIS_FORA, IRIS_ERRO_FORA, cor);
  const Cor dentro = misturar(IRIS_DENTRO, IRIS_ERRO_DENTRO, cor);
  const int ANEIS = 10;
  for (int k = 0; k < ANEIS; k++) {
    float t = powf((float)k / (ANEIS - 1), 0.6f);
    float orla = k == 0 ? 0.55f : 1.0f;
    Cor c = escalar(misturar(fora, dentro, t), g * orla);
    tela->fillCircle(ix, iy, IR * (1 - 0.055f * k), rgb565(dessaturar(c, sat)));
  }

  // Pupila e reflexos.
  tela->fillCircle(ix, iy, IR * pupila, rgb565(PUPILA));
  tela->fillCircle(ix - IR * 0.35f, iy - IR * 0.38f, IR * 0.14f, rgb565(REFLEXO));
  tela->fillCircle(ix + IR * 0.25f, iy + IR * 0.30f, IR * 0.06f, rgb565(REFLEXO));

  // Pálpebras: um retângulo com uma elipse na ponta, para a borda ser curva.
  // Desenha-se primeiro um pouco maior na cor da borda, depois na cor da pele.
  const float fc = fmaxf(cima, piscar), fb = fmaxf(baixo, piscar);
  const float yCima = cy - R + fc * 1.1f * R;
  const float yBaixo = cy + R - fb * 0.9f * R;
  const float cc = R * 0.35f * (1 - 0.5f * fc), cb = R * 0.25f;
  const float bordaPx = fmaxf(2.0f, W / 120.0f);
  for (int passo = 0; passo < 2; passo++) {
    const float d = passo == 0 ? bordaPx : 0;
    const uint16_t c = rgb565(passo == 0 ? PELE_BORDA : PELE);
    int alturaCima = (int)(yCima - cc + d);
    if (alturaCima > 0) tela->fillRect(0, 0, W, alturaCima, c);
    tela->fillEllipse(cx, yCima - cc, R * 1.15f, cc + d, c);
    int yRetBaixo = (int)(yBaixo + cb - d);
    if (yRetBaixo < W) tela->fillRect(0, yRetBaixo, W, W - yRetBaixo, c);
    tela->fillEllipse(cx, yBaixo + cb, R * 1.15f, cb + d, c);
  }
}
