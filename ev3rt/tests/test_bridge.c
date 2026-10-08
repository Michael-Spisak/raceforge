/* Spec 0011 AC1: host tests for the EV3RT bridge library (frames, COBS, failsafe, loops). */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "rf_bridge.h"
#include "rf_proto.h"

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            failures++;                                                          \
        }                                                                        \
    } while (0)

static void hex(const char *s, uint8_t *out, size_t *len) {
    size_t i, n = strlen(s) / 2;
    for (i = 0; i < n; i++) {
        unsigned v;
        sscanf(s + 2 * i, "%2x", &v);
        out[i] = (uint8_t)v;
    }
    *len = n;
}

/* Golden frames made by ev3_side/raceforge_ev3/protocol.py (same as rf-proto in Rust). */
static const char *GOLDEN_CMD = "4652010107000000d204000024fa680101020100244f";
static const char *GOLDEN_SEN =
    "46520102090000008813000007000000e8030000ceff38ffffff6801000000000000050000000100fa00fffff609"
    "0000f4ff8efeffff0111dc1e01000c82";

static void test_golden_frames(void) {
    uint8_t want[128], got[128];
    size_t n;
    rf_command_t c = {7, 1234, -1500, 360, RF_CMD_STOP, 2, 1};
    rf_command_t back;
    rf_sensor_t s, sback;
    hex(GOLDEN_CMD, want, &n);
    CHECK(n == RF_COMMAND_LEN);
    rf_command_encode(&c, got);
    CHECK(memcmp(got, want, n) == 0);
    CHECK(rf_command_decode(want, n, &back) == RF_OK);
    CHECK(back.seq == 7 && back.steer_target_cdeg == -1500 && back.drive_speed_cps == 360 && back.lcd == 1);

    memset(&s, 0, sizeof(s));
    s.seq = 9;
    s.t_ms = 5000;
    s.ack_seq = 7;
    s.motors[0].tacho = 1000; s.motors[0].speed_cps = -50;
    s.motors[1].tacho = -200; s.motors[1].speed_cps = 360;
    s.motors[3].tacho = 5; s.motors[3].speed_cps = 1;
    s.ultrasonic_mm[0] = 250; s.ultrasonic_mm[1] = RF_NO_ECHO; s.ultrasonic_mm[2] = 2550; s.ultrasonic_mm[3] = 0;
    s.gyro_rate_dps = -12;
    s.gyro_angle_deg = -370;
    s.touch = 1;
    s.buttons = 0x11;
    s.battery_mv = 7900;
    s.flags = RF_ST_LINK_OK;
    hex(GOLDEN_SEN, want, &n);
    CHECK(n == RF_SENSOR_LEN);
    rf_sensor_encode(&s, got);
    CHECK(memcmp(got, want, n) == 0);
    CHECK(rf_sensor_decode(want, n, &sback) == RF_OK);
    {
        int k;
        for (k = 0; k < 4; k++) {
            CHECK(sback.motors[k].tacho == s.motors[k].tacho && sback.motors[k].speed_cps == s.motors[k].speed_cps);
            CHECK(sback.ultrasonic_mm[k] == s.ultrasonic_mm[k]);
        }
    }
    CHECK(sback.gyro_angle_deg == -370 && sback.buttons == 0x11 && sback.battery_mv == 7900);
    want[20] ^= 1;
    CHECK(rf_sensor_decode(want, n, &sback) == RF_E_CRC);
    CHECK(rf_command_decode(want, 5, &back) == RF_E_SHORT);
    CHECK(rf_crc16_ccitt((const uint8_t *)"123456789", 9) == 0x29B1);
}

static void test_cobs(void) {
    uint8_t in[600], enc[700], dec[700];
    size_t sizes[] = {1, 2, 22, 62, 253, 254, 255, 300, 600};
    size_t k, i;
    for (k = 0; k < sizeof(sizes) / sizeof(sizes[0]); k++) {
        size_t n = sizes[k], e;
        int d;
        for (i = 0; i < n; i++) in[i] = (uint8_t)((i * 37 + k) % 5 == 0 ? 0 : (i * 13 + 1));
        e = rf_cobs_encode(in, n, enc);
        for (i = 0; i < e; i++) CHECK(enc[i] != 0);
        CHECK(e <= n + n / 254 + 1);
        d = rf_cobs_decode(enc, e, dec, sizeof(dec));
        CHECK(d == (int)n && memcmp(dec, in, n) == 0);
    }
    CHECK(rf_cobs_decode((const uint8_t *)"\x05\x01", 2, dec, sizeof(dec)) == -1); /* truncated */
    { /* golden wire bytes, identical in car_runtime/rf-proto/src/cobs.rs */
        uint8_t frame[RF_COMMAND_LEN], line[64], want[64];
        size_t n, wn;
        rf_command_t c = {7, 1234, -1500, 360, RF_CMD_STOP, 2, 1};
        rf_command_encode(&c, frame);
        n = rf_serial_frame(frame, sizeof(frame), line);
        hex("064652010107010103d204010824fa680101020103244f00", want, &wn);
        CHECK(n == wn && memcmp(line, want, n) == 0);
    }
}

static void test_stream_resync(void) {
    rf_stream_t st;
    uint8_t frame[RF_COMMAND_LEN], line[64];
    rf_command_t c = {42, 0, 100, 200, 0, 0, 0}, back;
    size_t n, i;
    int got = 0;
    const uint8_t garbage[] = {0x13, 0x37, 0x00, 0xAA, 0xBB, 0xCC};
    rf_stream_init(&st);
    rf_command_encode(&c, frame);
    n = rf_serial_frame(frame, sizeof(frame), line);
    CHECK(line[n - 1] == 0);
    for (i = 0; i < sizeof(garbage); i++) got += rf_stream_push(&st, garbage[i]);
    for (i = 0; i < n; i++) got += rf_stream_push(&st, line[i]);
    /* ...and the following frame is clean */
    for (i = 0; i < n; i++) {
        if (rf_stream_push(&st, line[i])) {
            got++;
            CHECK(rf_command_decode(st.packet, st.packet_len, &back) == RF_OK && back.seq == 42);
        }
    }
    /* "13 37" is not valid COBS (dropped); "AA BB CC" + the first frame merge (dropped); then one clean frame */
    CHECK(got == 1 && st.dropped == 2);
    for (i = 0; i < 200; i++) rf_stream_push(&st, 0x55); /* oversized */
    CHECK(rf_stream_push(&st, 0) == 0);
    CHECK(st.dropped >= 1);
}

/* ---- mock hardware: first-order motor model (power % -> deg/s), sensors set by tests ---- */
static float m_counts[4], m_speed[4];
static int m_power[4], m_braked[4];
static int16_t us_cm[4] = {-1, 50, 300, -1};
static int touch[4];
static const char *shown = "";

static int32_t h_counts(int p) { return (int32_t)lroundf(m_counts[p]); }
static void h_power(int p, int pw) { m_power[p] = pw; m_braked[p] = 0; }
static void h_brake(int p) { m_power[p] = 0; m_braked[p] = 1; }
static int16_t h_us(int p) { return us_cm[p]; }
static int16_t h_rate(int p) { (void)p; return 3; }
static int16_t h_angle(int p) { (void)p; return 90; }
static int h_touch(int p) { return touch[p]; }
static uint8_t h_buttons(void) { return 1u << RF_BTN_ENTER; }
static uint16_t h_batt(void) { return 7800; }
static void h_show(const char *s) { shown = s; }
static const rf_hal_t HAL = {h_counts, h_power, h_brake, h_us, h_rate, h_angle, h_touch, h_buttons, h_batt, h_show};

static void physics(float dt) {
    int p;
    for (p = 0; p < 4; p++) {
        float target = m_braked[p] ? 0.0f : (float)m_power[p] * 9.5f; /* a bit weaker than the ff model */
        m_speed[p] += (target - m_speed[p]) * (dt / 0.08f > 1 ? 1 : dt / 0.08f);
        m_counts[p] += m_speed[p] * dt;
    }
}

static void reset_world(void) {
    memset(m_counts, 0, sizeof(m_counts));
    memset(m_speed, 0, sizeof(m_speed));
    memset(m_power, 0, sizeof(m_power));
    memset(m_braked, 0, sizeof(m_braked));
    memset(touch, 0, sizeof(touch));
}

static void send(rf_bridge_t *b, uint32_t seq, int16_t steer, int16_t speed, uint8_t flags, uint32_t now) {
    uint8_t f[RF_COMMAND_LEN];
    rf_command_t c = {seq, now, steer, speed, flags, 0, 0};
    rf_command_encode(&c, f);
    rf_bridge_on_packet(b, f, sizeof(f), now);
}

static void test_failsafe_and_stop(void) {
    rf_bridge_t b;
    rf_bridge_config_t cfg;
    rf_sensor_t out;
    uint32_t t = 1000;
    reset_world();
    rf_bridge_default_config(&cfg);
    rf_bridge_init(&b, &cfg, &HAL, t);
    rf_bridge_step(&b, t += 10, &out);
    CHECK(out.flags & RF_ST_FAILSAFE);
    CHECK(strcmp(shown, "LINK LOST") == 0 && m_braked[cfg.drive_port]);
    send(&b, 1, 0, 300, 0, t);
    rf_bridge_step(&b, t += 10, &out);
    CHECK((out.flags & RF_ST_LINK_OK) && strcmp(shown, "RUN") == 0 && m_power[cfg.drive_port] > 0);
    CHECK(out.ack_seq == 1 && out.buttons == (1u << RF_BTN_ENTER) && out.battery_mv == 7800);
    CHECK(out.ultrasonic_mm[0] == RF_NO_ECHO && out.ultrasonic_mm[1] == 500 && out.ultrasonic_mm[2] == 2550);
    CHECK(out.gyro_rate_dps == 3 && out.gyro_angle_deg == 90);
    /* link loss: 151 ms without a command -> brake */
    t += 141;
    rf_bridge_step(&b, t, &out);
    CHECK(out.flags & RF_ST_FAILSAFE);
    CHECK(m_braked[cfg.drive_port] && b.stats.failsafe_trips == 1);
    /* STOP flag brakes the drive but keeps steering active */
    send(&b, 2, 1000, 300, RF_CMD_STOP, t);
    rf_bridge_step(&b, t += 10, &out);
    CHECK(m_braked[cfg.drive_port] && strcmp(shown, "STOPPED") == 0);
    CHECK(m_power[cfg.steer_port] > 0);
    /* stale frame (older seq) while the link is up is ignored */
    send(&b, 3, 0, 100, 0, t);
    send(&b, 2, 0, 900, 0, t);
    CHECK(b.cmd.seq == 3 && b.stats.rx_stale == 1);
    /* bad frame is counted */
    CHECK(rf_bridge_on_packet(&b, (const uint8_t *)"xx", 2, t) == 0 && b.stats.rx_bad == 1);
}

static void test_estop_touch(void) {
    rf_bridge_t b;
    rf_bridge_config_t cfg;
    rf_sensor_t out;
    uint32_t t = 0;
    reset_world();
    rf_bridge_default_config(&cfg);
    cfg.sensor[0] = RF_SENSOR_TOUCH;
    cfg.estop_touch_port = 0;
    rf_bridge_init(&b, &cfg, &HAL, t);
    touch[0] = 1;
    send(&b, 1, 0, 300, 0, t);
    rf_bridge_step(&b, t += 10, &out);
    CHECK((out.flags & RF_ST_ESTOP_PRESSED) && out.touch == 1 && m_braked[cfg.drive_port]);
    CHECK(strcmp(shown, "E-STOP") == 0);
}

static void test_loops_converge(void) {
    rf_bridge_t b;
    rf_bridge_config_t cfg;
    rf_sensor_t out;
    uint32_t t = 0, seq = 0;
    int i;
    reset_world();
    rf_bridge_default_config(&cfg);
    rf_bridge_init(&b, &cfg, &HAL, t);
    for (i = 0; i < 300; i++) { /* 3 s at 100 Hz */
        send(&b, ++seq, 2500 /* 25 deg */, 360, 0, t);
        physics(0.01f);
        t += 10;
        rf_bridge_step(&b, t, &out);
    }
    CHECK(fabsf(m_speed[cfg.drive_port] - 360.0f) < 18.0f); /* within 5 % */
    CHECK(abs(h_counts(cfg.steer_port) - 25) <= cfg.steer_deadband_deg + 1);
    CHECK(abs(out.motors[cfg.drive_port].speed_cps - 360) < 25);
    /* stop: drive brakes and the integral resets */
    send(&b, ++seq, 2500, 0, 0, t);
    rf_bridge_step(&b, t += 10, &out);
    CHECK(m_braked[cfg.drive_port] && b.drive_integral == 0.0f);
}

int main(void) {
    test_golden_frames();
    test_cobs();
    test_stream_resync();
    test_failsafe_and_stop();
    test_estop_touch();
    test_loops_converge();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("ev3rt bridge tests: all passed\n");
    return 0;
}
