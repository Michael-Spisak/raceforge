/*
 * RaceForge EV3 link frames (rf-proto v1, spec 0005) and serial framing (COBS, spec 0011).
 * Bit-identical to car_runtime/rf-proto/src/ev3.rs and ev3_side/raceforge_ev3/protocol.py.
 * Plain C99, no EV3RT dependency: unit-tested on the host (ev3rt/tests).
 */
#ifndef RF_PROTO_H
#define RF_PROTO_H

#include <stddef.h>
#include <stdint.h>

#define RF_MAGIC 0x5246u
#define RF_VERSION 1u
#define RF_KIND_COMMAND 1u
#define RF_KIND_SENSOR 2u
#define RF_COMMAND_LEN 22u
#define RF_SENSOR_LEN 62u
#define RF_NO_ECHO 0xFFFFu

/* Command flags (board -> EV3) */
#define RF_CMD_STOP (1u << 0)
#define RF_CMD_ESTOP (1u << 1)
#define RF_CMD_RACE (1u << 2)
/* Status flags (EV3 -> board) */
#define RF_ST_LINK_OK (1u << 0)
#define RF_ST_ESTOP_PRESSED (1u << 1)
#define RF_ST_FAILSAFE (1u << 2)
/* LCD codes in CommandFrame.lcd */
#define RF_LCD_RUN 0u
#define RF_LCD_STOPPED 1u
#define RF_LCD_FAULT 2u
/* Button bits in SensorFrame.buttons */
#define RF_BTN_UP 0u
#define RF_BTN_DOWN 1u
#define RF_BTN_LEFT 2u
#define RF_BTN_RIGHT 3u
#define RF_BTN_ENTER 4u
#define RF_BTN_BACK 5u

typedef struct {
    uint32_t seq;
    uint32_t t_ms;
    int16_t steer_target_cdeg; /* steering motor target, centi-degrees of motor rotation */
    int16_t drive_speed_cps;   /* drive motor speed target, tacho counts (degrees) per second */
    uint8_t flags;
    uint8_t led;
    uint8_t lcd;
} rf_command_t;

typedef struct {
    int32_t tacho;
    int16_t speed_cps;
} rf_motor_t;

typedef struct {
    uint32_t seq;
    uint32_t t_ms;
    uint32_t ack_seq;
    rf_motor_t motors[4];        /* ports A..D */
    uint16_t ultrasonic_mm[4];   /* ports 1..4, RF_NO_ECHO when no reading */
    int16_t gyro_rate_dps;
    int32_t gyro_angle_deg;
    uint8_t touch;               /* bit i = port i+1 */
    uint8_t buttons;             /* RF_BTN_* bits */
    uint16_t battery_mv;
    uint8_t flags;
} rf_sensor_t;

uint16_t rf_crc16_ccitt(const uint8_t *data, size_t len);

/* Encode into out (RF_COMMAND_LEN / RF_SENSOR_LEN bytes). */
void rf_command_encode(const rf_command_t *c, uint8_t out[RF_COMMAND_LEN]);
void rf_sensor_encode(const rf_sensor_t *s, uint8_t out[RF_SENSOR_LEN]);

/* 0 on success, negative on error (short, magic, version, kind, crc). */
enum { RF_OK = 0, RF_E_SHORT = -1, RF_E_MAGIC = -2, RF_E_VERSION = -3, RF_E_KIND = -4, RF_E_CRC = -5 };
int rf_command_decode(const uint8_t *data, size_t len, rf_command_t *out);
int rf_sensor_decode(const uint8_t *data, size_t len, rf_sensor_t *out);

/*
 * COBS (Consistent Overhead Byte Stuffing): encoded data contains no 0x00, so 0x00 delimits frames.
 * rf_cobs_encode writes at most len + len/254 + 1 bytes (no delimiter); returns the encoded length.
 * rf_cobs_decode returns the decoded length, or -1 for an invalid encoding.
 */
size_t rf_cobs_encode(const uint8_t *in, size_t len, uint8_t *out);
int rf_cobs_decode(const uint8_t *in, size_t len, uint8_t *out, size_t out_cap);

/* Encodes a frame for the serial line: COBS(frame) followed by 0x00. Returns the total length. */
size_t rf_serial_frame(const uint8_t *frame, size_t len, uint8_t *out);

/* Byte-stream decoder: feed bytes; when a delimiter completes a valid COBS packet, the decoded
 * packet is available in `packet`/`packet_len` and rf_stream_push returns 1. Oversized or invalid
 * packets are dropped (counted in `dropped`). */
#define RF_STREAM_MAX 80u
typedef struct {
    uint8_t buf[RF_STREAM_MAX];
    size_t n;
    int overflow;
    uint8_t packet[RF_STREAM_MAX];
    size_t packet_len;
    uint32_t dropped;
} rf_stream_t;

void rf_stream_init(rf_stream_t *s);
int rf_stream_push(rf_stream_t *s, uint8_t byte);

#endif
