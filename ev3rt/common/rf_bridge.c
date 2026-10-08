#include "rf_bridge.h"

#include <string.h>

#define US_MAX_MM 2550u /* EV3 ultrasonic reports 255 cm when nothing is in range */

void rf_bridge_default_config(rf_bridge_config_t *c) {
    memset(c, 0, sizeof(*c));
    c->steer_port = 0; /* A */
    c->drive_port = 1; /* B */
    c->sensor[0] = RF_SENSOR_NONE; /* port 1 is the UART link by default */
    c->sensor[1] = RF_SENSOR_ULTRASONIC;
    c->sensor[2] = RF_SENSOR_ULTRASONIC;
    c->sensor[3] = RF_SENSOR_GYRO;
    c->estop_touch_port = -1;
    c->failsafe_ms = 150;
    c->drive_ff = 100.0f / 1050.0f;
    c->drive_kp = 0.05f;
    c->drive_ki = 0.2f;
    c->steer_kp = 1.5f;
    c->steer_min_power = 8;
    c->steer_max_power = 60;
    c->steer_deadband_deg = 1;
}

void rf_bridge_init(rf_bridge_t *b, const rf_bridge_config_t *cfg, const rf_hal_t *hal, uint32_t now_ms) {
    int i;
    memset(b, 0, sizeof(*b));
    b->cfg = *cfg;
    b->hal = hal;
    b->cmd.flags = RF_CMD_STOP;
    b->t0_ms = now_ms;
    b->prev_ms = now_ms;
    b->was_expired = 1;
    for (i = 0; i < 4; i++) b->prev_counts[i] = hal->motor_counts(i);
}

static int expired(const rf_bridge_t *b, uint32_t now_ms) {
    return !b->have_cmd || (uint32_t)(now_ms - b->last_rx_ms) > b->cfg.failsafe_ms;
}

int rf_bridge_on_packet(rf_bridge_t *b, const uint8_t *data, size_t len, uint32_t now_ms) {
    rf_command_t c;
    uint32_t behind;
    if (rf_command_decode(data, len, &c) != RF_OK) {
        b->stats.rx_bad++;
        return 0;
    }
    /* Ignore reordered (older) frames while the link is up; after a failsafe accept any valid frame
     * (the board may have restarted and its sequence starts over). */
    behind = b->cmd.seq - c.seq;
    if (b->have_cmd && behind > 0 && behind < 1000 && !expired(b, now_ms)) {
        b->stats.rx_stale++;
        return 0;
    }
    b->cmd = c;
    b->have_cmd = 1;
    b->last_rx_ms = now_ms;
    b->stats.rx++;
    return 1;
}

static int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
static float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

static void show(rf_bridge_t *b, const char *status) {
    if (b->status != status) {
        b->status = status;
        b->hal->show(status);
    }
}

static void steer(rf_bridge_t *b, int16_t target_cdeg) {
    const rf_bridge_config_t *c = &b->cfg;
    int32_t err = (int32_t)target_cdeg / 100 - b->hal->motor_counts(c->steer_port);
    int power;
    if (err <= c->steer_deadband_deg && err >= -c->steer_deadband_deg) {
        b->hal->motor_brake(c->steer_port); /* hold */
        return;
    }
    power = (int)(c->steer_kp * (float)err);
    if (power > 0 && power < c->steer_min_power) power = c->steer_min_power;
    if (power < 0 && power > -c->steer_min_power) power = -c->steer_min_power;
    b->hal->motor_power(c->steer_port, clampi(power, -c->steer_max_power, c->steer_max_power));
}

static void drive(rf_bridge_t *b, int16_t target_cps, float dt_s) {
    const rf_bridge_config_t *c = &b->cfg;
    float target = (float)target_cps;
    float err = target - b->speed_cps[c->drive_port];
    float limit = c->drive_ki > 0 ? 100.0f / c->drive_ki : 0.0f;
    float power;
    if (target_cps == 0) {
        b->drive_integral = 0;
        b->hal->motor_brake(c->drive_port);
        return;
    }
    b->drive_integral = clampf(b->drive_integral + err * dt_s, -limit, limit);
    power = c->drive_ff * target + c->drive_kp * err + c->drive_ki * b->drive_integral;
    b->hal->motor_power(c->drive_port, clampi((int)power, -100, 100));
}

static void brake_drive(rf_bridge_t *b) {
    b->drive_integral = 0;
    b->hal->motor_brake(b->cfg.drive_port);
}

void rf_bridge_step(rf_bridge_t *b, uint32_t now_ms, rf_sensor_t *out) {
    const rf_hal_t *hal = b->hal;
    const rf_bridge_config_t *c = &b->cfg;
    uint32_t dt_ms = now_ms - b->prev_ms;
    float dt_s = dt_ms > 0 ? (float)dt_ms / 1000.0f : 0.01f;
    int is_expired = expired(b, now_ms);
    int estop = 0;
    uint8_t touch = 0;
    int i;

    memset(out, 0, sizeof(*out));
    /* motors: counts and speed (light low-pass) */
    for (i = 0; i < 4; i++) {
        int32_t counts = hal->motor_counts(i);
        if (dt_ms > 0) {
            float inst = (float)(counts - b->prev_counts[i]) * 1000.0f / (float)dt_ms;
            b->speed_cps[i] = 0.5f * b->speed_cps[i] + 0.5f * inst;
        }
        b->prev_counts[i] = counts;
        out->motors[i].tacho = counts;
        out->motors[i].speed_cps = (int16_t)clampf(b->speed_cps[i], -32768.0f, 32767.0f);
    }
    b->prev_ms = now_ms;
    /* sensors */
    for (i = 0; i < 4; i++) {
        out->ultrasonic_mm[i] = RF_NO_ECHO;
        if (c->sensor[i] == RF_SENSOR_ULTRASONIC) {
            int16_t cm = hal->ultrasonic_cm(i);
            if (cm >= 0) {
                uint32_t mm = (uint32_t)cm * 10u;
                out->ultrasonic_mm[i] = (uint16_t)(mm > US_MAX_MM ? US_MAX_MM : mm);
            }
        } else if (c->sensor[i] == RF_SENSOR_GYRO) {
            out->gyro_rate_dps = hal->gyro_rate_dps(i);
            out->gyro_angle_deg = hal->gyro_angle_deg(i);
        } else if (c->sensor[i] == RF_SENSOR_TOUCH) {
            if (hal->touch_pressed(i)) touch |= (uint8_t)(1u << i);
        }
    }
    estop = c->estop_touch_port >= 0 && (touch & (1u << c->estop_touch_port));

    if (is_expired && !b->was_expired) b->stats.failsafe_trips++;
    b->was_expired = is_expired;

    if (is_expired) {
        brake_drive(b);
        hal->motor_brake(c->steer_port);
        show(b, "LINK LOST");
    } else if (estop || (b->cmd.flags & (RF_CMD_STOP | RF_CMD_ESTOP))) {
        brake_drive(b);
        steer(b, b->cmd.steer_target_cdeg);
        show(b, estop ? "E-STOP" : (b->cmd.lcd == RF_LCD_FAULT ? "FAULT" : "STOPPED"));
    } else {
        steer(b, b->cmd.steer_target_cdeg);
        drive(b, b->cmd.drive_speed_cps, dt_s);
        show(b, "RUN");
    }

    b->seq++;
    out->seq = b->seq;
    out->t_ms = now_ms - b->t0_ms;
    out->ack_seq = b->cmd.seq;
    out->touch = touch;
    out->buttons = hal->buttons();
    out->battery_mv = hal->battery_mv();
    out->flags = (uint8_t)((is_expired ? RF_ST_FAILSAFE : RF_ST_LINK_OK) | (estop ? RF_ST_ESTOP_PRESSED : 0));
    b->stats.tx++;
}

void rf_bridge_shutdown(rf_bridge_t *b) {
    brake_drive(b);
    b->hal->motor_brake(b->cfg.steer_port);
    show(b, "STOPPED");
}
