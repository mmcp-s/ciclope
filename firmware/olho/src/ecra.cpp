// Configuração de cada placa. Pinos e parâmetros confirmados no wiki e no
// esquema elétrico da Waveshare, no código de exemplo dela e nos ficheiros de
// placa do CircuitPython (ver docs/arquitetura.md para as fontes).
#include "ecra.h"

#if defined(BOARD_WS_S3_TOUCH_LCD_21)
// ---------------------------------------------------------------------------
// Waveshare ESP32-S3-Touch-LCD-2.1 — 480×480, controlador ST7701S, interface RGB
//
// Este ecrã recebe os píxeis por 16 fios em paralelo (interface RGB), mas é
// configurado no arranque por um SPI lento de 3 fios. O reset e o chip select
// desse SPI não estão ligados ao ESP32: estão num expansor de I/O (TCA9554)
// no barramento I2C. Por isso temos de falar com o expansor antes do ecrã.
// ---------------------------------------------------------------------------
#include <Wire.h>
#include "ws21_init.h"

#define PINO_LUZ 6
#define LADO 480
#define TCA9554_ENDERECO 0x20
#define TCA9554_SAIDA 0x01   // registo dos valores das saídas
#define TCA9554_CONFIG 0x03  // registo de direção (1 = entrada, 0 = saída)

static void tcaEscrever(uint8_t registo, uint8_t valor) {
  Wire.beginTransmission(TCA9554_ENDERECO);
  Wire.write(registo);
  Wire.write(valor);
  Wire.endTransmission();
}

// DC indefinido = SPI de 9 bits; o CS está no expansor e é tratado à mão.
static Arduino_DataBus *barramento =
    new Arduino_SWSPI(GFX_NOT_DEFINED /* DC */, GFX_NOT_DEFINED /* CS */, 2 /* SCK */, 1 /* MOSI */,
                      GFX_NOT_DEFINED /* MISO */);

static Arduino_ESP32RGBPanel *painelRgb = new Arduino_ESP32RGBPanel(
    40 /* DE */, 39 /* VSYNC */, 38 /* HSYNC */, 41 /* PCLK */,
    46 /* R0 */, 3 /* R1 */, 8 /* R2 */, 18 /* R3 */, 17 /* R4 */,
    14 /* G0 */, 13 /* G1 */, 12 /* G2 */, 11 /* G3 */, 10 /* G4 */, 9 /* G5 */,
    5 /* B0 */, 45 /* B1 */, 48 /* B2 */, 47 /* B3 */, 21 /* B4 */,
    1 /* hsync_polarity */, 50 /* hsync_front_porch */, 8 /* hsync_pulse_width */, 10 /* hsync_back_porch */,
    1 /* vsync_polarity */, 8 /* vsync_front_porch */, 3 /* vsync_pulse_width */, 8 /* vsync_back_porch */,
    0 /* pclk_active_neg */, 12000000 /* prefer_speed */, false /* useBigEndian */,
    0 /* de_idle_high */, 0 /* pclk_idle_high */,
    0 /* bounce_buffer_size_px — ver "imagem a deslizar" no README */);

static Arduino_GFX *painel = new Arduino_RGB_Display(
    LADO, LADO, painelRgb, 0 /* rotação */, true /* auto_flush */,
    barramento, GFX_NOT_DEFINED /* RST: está no expansor */,
    ws21_st7701_init_operations, sizeof(ws21_st7701_init_operations));

static void prepararPlaca() {
  Wire.begin(15 /* SDA */, 7 /* SCL */);
  // Primeiro os valores, depois a direção: o TCA9554 arranca com todas as
  // saídas a 1, e o P7 liga o besouro — se configurássemos primeiro a
  // direção, apitava.
  // Bits: P0 LCD_RST, P1 TP_RST, P2 LCD_CS, P3 SD_CS, P7 besouro.
  tcaEscrever(TCA9554_SAIDA, 0x0C);   // resets em baixo, CS do ecrã e do SD em alto
  tcaEscrever(TCA9554_CONFIG, 0x70);  // P4–P6 entradas (interrupções), o resto saídas
  delay(10);
  tcaEscrever(TCA9554_SAIDA, 0x0F);   // solta os resets
  delay(120);
  tcaEscrever(TCA9554_SAIDA, 0x0B);   // CS do ecrã em baixo durante toda a configuração
}

static void depoisDoBegin() {
  tcaEscrever(TCA9554_SAIDA, 0x0F);   // CS do ecrã em alto
}

#elif defined(BOARD_WS_S3_LCD_128)
// ---------------------------------------------------------------------------
// Waveshare ESP32-S3-LCD-1.28 — 240×240, controlador GC9A01, SPI
// (a versão SEM toque; a versão "Touch" tem outros pinos)
// ---------------------------------------------------------------------------
#define PINO_LUZ 40
#define LADO 240

static Arduino_DataBus *barramento =
    new Arduino_ESP32SPI(8 /* DC */, 9 /* CS */, 10 /* SCK */, 11 /* MOSI */, GFX_NOT_DEFINED /* MISO */);
static Arduino_GFX *painel = new Arduino_GC9A01(barramento, 12 /* RST */, 0 /* rotação */, true /* IPS */);

static void prepararPlaca() {}
static void depoisDoBegin() {}

#else
#error "Escolhe a placa: compila com -DBOARD_WS_S3_TOUCH_LCD_21 ou -DBOARD_WS_S3_LCD_128 (ver platformio.ini)"
#endif

// A tela tem o tamanho do ecrã e vive na PSRAM: 115 KB no 1,28", 460 KB no 2,1".
static Arduino_Canvas *tela = new Arduino_Canvas(LADO, LADO, painel, 0, 0, 0);

bool ecraIniciar() {
  prepararPlaca();
  bool ok = tela->begin();
  depoisDoBegin();

  // Retroiluminação por PWM: 20 kHz (inaudível), 10 bits (0–1023).
  ledcAttach(PINO_LUZ, 20000, 10);
  ledcWrite(PINO_LUZ, 0);

  tela->fillScreen(RGB565_BLACK);
  tela->flush();
  return ok;
}

Arduino_GFX *ecraTela() { return tela; }
void ecraMostrar() { tela->flush(); }
int ecraLado() { return LADO; }
void ecraBrilho(uint16_t nivel) { ledcWrite(PINO_LUZ, nivel > 1023 ? 1023 : nivel); }
