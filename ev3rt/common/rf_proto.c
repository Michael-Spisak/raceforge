#include "rf_proto.h"

#include <string.h>

uint16_t rf_crc16_ccitt(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFFu;
    size_t i;
    int b;
    for (i = 0; i < len; i++) {
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (b = 0; b < 8; b++) {
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

/* little-endian writers/readers */
static uint8_t *put_u8(uint8_t *p, uint8_t v) {
    *p = v;
    return p + 1;
}
static uint8_t *put_u16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)(v >> 8);
    return p + 2;
}
static uint8_t *put_u32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)(v >> 24);
    return p + 4;
}
static uint16_t get_u16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t get_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint8_t *header(uint8_t *p, uint8_t kind, uint32_t seq, uint32_t t_ms) {
    p = put_u16(p, RF_MAGIC);
    p = put_u8(p, RF_VERSION);
    p = put_u8(p, kind);
    p = put_u32(p, seq);
    return put_u32(p, t_ms);
}

static void finish(uint8_t *start, uint8_t *p) {
    uint16_t crc = rf_crc16_ccitt(start, (size_t)(p - start));
    put_u16(p, crc);
}

void rf_command_encode(const rf_command_t *c, uint8_t out[RF_COMMAND_LEN]) {
    uint8_t *p = header(out, RF_KIND_COMMAND, c->seq, c->t_ms);
    p = put_u16(p, (uint16_t)c->steer_target_cdeg);
    p = put_u16(p, (uint16_t)c->drive_speed_cps);
    p = put_u8(p, c->flags);
    p = put_u8(p, c->led);
    p = put_u8(p, c->lcd);
    p = put_u8(p, 0);
    finish(out, p);
}

void rf_sensor_encode(const rf_sensor_t *s, uint8_t out[RF_SENSOR_LEN]) {
    int i;
    uint8_t *p = header(out, RF_KIND_SENSOR, s->seq, s->t_ms);
    p = put_u32(p, s->ack_seq);
    for (i = 0; i < 4; i++) {
        p = put_u32(p, (uint32_t)s->motors[i].tacho);
        p = put_u16(p, (uint16_t)s->motors[i].speed_cps);
    }
    for (i = 0; i < 4; i++) p = put_u16(p, s->ultrasonic_mm[i]);
    p = put_u16(p, (uint16_t)s->gyro_rate_dps);
    p = put_u32(p, (uint32_t)s->gyro_angle_deg);
    p = put_u8(p, s->touch);
    p = put_u8(p, s->buttons);
    p = put_u16(p, s->battery_mv);
    p = put_u8(p, s->flags);
    p = put_u8(p, 0);
    finish(out, p);
}

static int check(const uint8_t *d, size_t len, size_t want, uint8_t kind) {
    if (len < want) return RF_E_SHORT;
    if (get_u16(d) != RF_MAGIC) return RF_E_MAGIC;
    if (d[2] != RF_VERSION) return RF_E_VERSION;
    if (d[3] != kind) return RF_E_KIND;
    if (rf_crc16_ccitt(d, want - 2) != get_u16(d + want - 2)) return RF_E_CRC;
    return RF_OK;
}

int rf_command_decode(const uint8_t *d, size_t len, rf_command_t *c) {
    int e = check(d, len, RF_COMMAND_LEN, RF_KIND_COMMAND);
    if (e != RF_OK) return e;
    c->seq = get_u32(d + 4);
    c->t_ms = get_u32(d + 8);
    c->steer_target_cdeg = (int16_t)get_u16(d + 12);
    c->drive_speed_cps = (int16_t)get_u16(d + 14);
    c->flags = d[16];
    c->led = d[17];
    c->lcd = d[18];
    return RF_OK;
}

int rf_sensor_decode(const uint8_t *d, size_t len, rf_sensor_t *s) {
    int i, e = check(d, len, RF_SENSOR_LEN, RF_KIND_SENSOR);
    const uint8_t *p;
    if (e != RF_OK) return e;
    s->seq = get_u32(d + 4);
    s->t_ms = get_u32(d + 8);
    s->ack_seq = get_u32(d + 12);
    p = d + 16;
    for (i = 0; i < 4; i++, p += 6) {
        s->motors[i].tacho = (int32_t)get_u32(p);
        s->motors[i].speed_cps = (int16_t)get_u16(p + 4);
    }
    for (i = 0; i < 4; i++, p += 2) s->ultrasonic_mm[i] = get_u16(p);
    s->gyro_rate_dps = (int16_t)get_u16(p);
    s->gyro_angle_deg = (int32_t)get_u32(p + 2);
    s->touch = p[6];
    s->buttons = p[7];
    s->battery_mv = get_u16(p + 8);
    s->flags = p[10];
    return RF_OK;
}

size_t rf_cobs_encode(const uint8_t *in, size_t len, uint8_t *out) {
    size_t read = 0, write = 1, code_pos = 0;
    uint8_t code = 1;
    while (read < len) {
        if (in[read] == 0) {
            out[code_pos] = code;
            code = 1;
            code_pos = write++;
            read++;
        } else {
            out[write++] = in[read++];
            code++;
            if (code == 0xFF) {
                out[code_pos] = code;
                code = 1;
                code_pos = write++;
            }
        }
    }
    out[code_pos] = code;
    return write;
}

int rf_cobs_decode(const uint8_t *in, size_t len, uint8_t *out, size_t out_cap) {
    size_t read = 0, write = 0;
    while (read < len) {
        uint8_t code = in[read++];
        size_t i;
        if (code == 0 || read + (size_t)(code - 1) > len) return -1;
        for (i = 1; i < code; i++) {
            if (in[read] == 0 || write >= out_cap) return -1;
            out[write++] = in[read++];
        }
        if (code != 0xFF && read < len) {
            if (write >= out_cap) return -1;
            out[write++] = 0;
        }
    }
    return (int)write;
}

size_t rf_serial_frame(const uint8_t *frame, size_t len, uint8_t *out) {
    size_t n = rf_cobs_encode(frame, len, out);
    out[n] = 0;
    return n + 1;
}

void rf_stream_init(rf_stream_t *s) { memset(s, 0, sizeof(*s)); }

int rf_stream_push(rf_stream_t *s, uint8_t byte) {
    int n;
    if (byte != 0) {
        if (s->n < RF_STREAM_MAX) {
            s->buf[s->n++] = byte;
        } else {
            s->overflow = 1;
        }
        return 0;
    }
    /* delimiter */
    if (s->n == 0) return 0; /* empty packet between delimiters */
    n = s->overflow ? -1 : rf_cobs_decode(s->buf, s->n, s->packet, sizeof(s->packet));
    s->n = 0;
    s->overflow = 0;
    if (n <= 0) {
        s->dropped++;
        return 0;
    }
    s->packet_len = (size_t)n;
    return 1;
}
