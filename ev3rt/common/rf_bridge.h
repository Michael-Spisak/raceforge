/*
 * EV3 side of the board <-> EV3 link under EV3RT (spec 0011): same behaviour as the ev3dev bridge
 * (ev3_side/raceforge_ev3/bridge.py) — failsafe, e-stop, steering position, drive speed, sensor frames.
 * Hardware access goes through rf_hal_t so the logic runs in host unit tests (ev3rt/tests).
 */
#ifndef RF_BRIDGE_H
#define RF_BRIDGE_H

#include <stdint.h>

#include "rf_proto.h"

enum { RF_SENSOR_NONE = 0, RF_SENSOR_ULTRASONIC = 1, RF_SENSOR_GYRO = 2, RF_SENSOR_TOUCH = 3 };

typedef struct {
    int steer_port;               /* motor port 0..3 (A..D) */
    int drive_port;
    int sensor[4];                /* RF_SENSOR_* for ports 1..4 */
    int estop_touch_port;         /* 0..3, or -1 */
    uint32_t failsafe_ms;         /* spec 0005: 150 */
    /* drive speed loop: power = ff * target + kp * err + ki * integral  (power in percent) */
    float drive_ff;               /* percent per (deg/s); EV3 large motor ≈ 100 / 1050 */
    float drive_kp;
    float drive_ki;
    /* steering position loop: power = steer_kp * error_deg, at least steer_min_power, at most steer_max_power */
    float steer_kp;
    int steer_min_power;
    int steer_max_power;
    int steer_deadband_deg;
} rf_bridge_config_t;

typedef struct {
    int32_t (*motor_counts)(int port);            /* degrees */
    void (*motor_power)(int port, int power);     /* -100..100 */
    void (*motor_brake)(int port);
    int16_t (*ultrasonic_cm)(int port);           /* < 0 when no reading */
    int16_t (*gyro_rate_dps)(int port);
    int16_t (*gyro_angle_deg)(int port);
    int (*touch_pressed)(int port);
    uint8_t (*buttons)(void);                     /* RF_BTN_* bits */
    uint16_t (*battery_mv)(void);
    void (*show)(const char *status);             /* LCD status line */
} rf_hal_t;

typedef struct {
    uint32_t rx, rx_bad, rx_stale, tx, failsafe_trips;
} rf_bridge_stats_t;

typedef struct {
    rf_bridge_config_t cfg;
    const rf_hal_t *hal;
    rf_command_t cmd;
    int have_cmd;
    uint32_t last_rx_ms;
    uint32_t t0_ms;
    uint32_t seq;
    int was_expired;
    int32_t prev_counts[4];
    uint32_t prev_ms;
    float speed_cps[4];
    float drive_integral;
    const char *status;
    rf_bridge_stats_t stats;
} rf_bridge_t;

void rf_bridge_default_config(rf_bridge_config_t *cfg);
void rf_bridge_init(rf_bridge_t *b, const rf_bridge_config_t *cfg, const rf_hal_t *hal, uint32_t now_ms);

/* A decoded packet from the serial line. Returns 1 if it was accepted as the current command. */
int rf_bridge_on_packet(rf_bridge_t *b, const uint8_t *data, size_t len, uint32_t now_ms);

/* One 10 ms cycle: failsafe/e-stop, motors, sensors. Fills `out` with the frame to send. */
void rf_bridge_step(rf_bridge_t *b, uint32_t now_ms, rf_sensor_t *out);

/* Stop everything (app exit). */
void rf_bridge_shutdown(rf_bridge_t *b);

#endif
