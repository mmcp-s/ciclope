#include "ligacao.h"

#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <WebSocketsClient.h>
#include <WiFi.h>

#include <atomic>

#if __has_include("config.h")
#include "config.h"
#else
#error "Falta include/config.h — copia include/config.example.h para include/config.h e preenche o WiFi e o IP do Pi."
#endif

static WebSocketsClient ws;
static QueueHandle_t fila = nullptr;

// std::atomic: lida pelo núcleo do desenho, escrita pelo da rede. Sem ela, um
// núcleo podia não ver a mudança feita pelo outro.
static std::atomic<bool> wsLigado{false};

static bool wsIniciado = false;
static uint32_t ultimaTentativa = 0;
static uint32_t desligadoDesde = 0;

static void enviar(Mensagem *m) {
  // Fila cheia (o desenho parou?): deita fora em vez de bloquear a rede.
  if (xQueueSend(fila, &m, 0) != pdTRUE) delete m;
}

static void enviarEstado(Estado e) {
  Mensagem *m = new Mensagem{Mensagem::DeEstado};
  m->estado = e;
  m->quando = segundos();
  enviar(m);
}

// Trata uma mensagem do Ciclope. O protocolo está em ciclope/cara.py:
//   {"t":"ola","estado":"..."} · {"t":"estado","v":"..."} · {"t":"fala","ms":30,"env":[...]}
static void tratarMensagem(const uint8_t *dados, size_t n) {
  JsonDocument doc;
  if (deserializeJson(doc, dados, n)) {
    Serial.println("[olho] mensagem JSON inválida");
    return;
  }
  const char *tipo = doc["t"] | "";

  if (!strcmp(tipo, "estado")) {
    enviarEstado(estadoDeTexto(doc["v"] | "inativo"));
  } else if (!strcmp(tipo, "ola")) {
    enviarEstado(estadoDeTexto(doc["estado"] | "inativo"));
  } else if (!strcmp(tipo, "fala")) {
    Mensagem *m = new Mensagem{Mensagem::DeFala};
    JsonArrayConst valores = doc["env"];
    m->env.reserve(valores.size());
    for (JsonVariantConst v : valores) m->env.push_back((uint8_t)v.as<int>());
    m->ms = doc["ms"] | 30;
    m->quando = segundos();
    enviar(m);
  }
}

static void aoEvento(WStype_t tipo, uint8_t *dados, size_t n) {
  switch (tipo) {
    case WStype_CONNECTED:
      wsLigado = true;
      Serial.println("[olho] ligado ao Ciclope");
      break;
    case WStype_DISCONNECTED:
      if (wsLigado) {
        Serial.println("[olho] ligação perdida, a tentar de novo...");
        enviarEstado(Estado::Inativo);  // não ficar preso em "a pensar" ou "a falar"
        desligadoDesde = millis();
      }
      wsLigado = false;
      break;
    case WStype_TEXT:
      tratarMensagem(dados, n);
      break;
    default:
      break;
  }
}

static bool hostELocal() { return String(CICLOPE_HOST).endsWith(".local"); }

// "ciclope.local" é um nome mDNS: não há servidor de DNS que o conheça, é o
// próprio Pi que responde a perguntas em multicast na rede local.
static String resolverHost() {
  String host = CICLOPE_HOST;
  if (!hostELocal()) return host;
  String nome = host.substring(0, host.length() - 6);
  IPAddress ip = MDNS.queryHost(nome.c_str(), 2000);  // bloqueia até 2 s — aqui não faz mal
  if (ip == IPAddress(0, 0, 0, 0)) {
    Serial.printf("[olho] não encontrei %s na rede\n", host.c_str());
    return "";
  }
  Serial.printf("[olho] %s = %s\n", host.c_str(), ip.toString().c_str());
  return ip.toString();
}

static void tarefaRede(void *) {
  WiFi.mode(WIFI_STA);
  // Sem poupança de energia no WiFi: com ela, as mensagens chegam com atrasos
  // de centenas de milissegundos e o olho dessincroniza da voz.
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("[olho] a ligar ao WiFi \"%s\"...\n", WIFI_SSID);

  bool wifiAnunciado = false, mdnsIniciado = false;

  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      if (wsLigado) enviarEstado(Estado::Inativo);
      wsLigado = false;
      wifiAnunciado = false;
      vTaskDelay(pdMS_TO_TICKS(100));  // o próprio ESP32 volta a ligar ao WiFi sozinho
      continue;
    }
    if (!wifiAnunciado) {
      Serial.printf("[olho] WiFi ligado, IP %s\n", WiFi.localIP().toString().c_str());
      wifiAnunciado = true;
    }
    if (!mdnsIniciado) {
      MDNS.begin("ciclope-olho");
      mdnsIniciado = true;
    }

    // Abre o WebSocket (tenta a cada 5 s até conseguir resolver o endereço).
    if (!wsIniciado && millis() - ultimaTentativa > 5000) {
      ultimaTentativa = millis();
      String host = resolverHost();
      if (host.length()) {
        ws.begin(host, CICLOPE_PORTA, "/");
        ws.onEvent(aoEvento);
        ws.setReconnectInterval(3000);
        // Pings periódicos: deteta uma ligação morta (Pi desligado à bruta)
        // em ~20 s em vez de ficar à espera para sempre.
        ws.enableHeartbeat(15000, 3000, 2);
        wsIniciado = true;
        desligadoDesde = millis();
      }
    }

    // Com um nome .local, o Pi pode ter mudado de IP. Se estivermos sem
    // ligação há 30 s, voltamos a perguntar o endereço.
    if (wsIniciado && hostELocal() && !wsLigado && millis() - desligadoDesde > 30000) {
      ws.disconnect();
      wsIniciado = false;
    }

    if (wsIniciado) ws.loop();
    vTaskDelay(1);  // cede o núcleo às outras tarefas (WiFi incluído)
  }
}

void ligacaoIniciar() {
  fila = xQueueCreate(16, sizeof(Mensagem *));
  // Núcleo 0: o mesmo da pilha WiFi. O loop() do Arduino corre no núcleo 1.
  xTaskCreatePinnedToCore(tarefaRede, "rede", 12288, nullptr, 1, nullptr, 0);
}

bool ligacaoAtiva() { return wsLigado; }

Mensagem *ligacaoProxima() {
  Mensagem *m = nullptr;
  if (fila && xQueueReceive(fila, &m, 0) == pdTRUE) return m;
  return nullptr;
}
