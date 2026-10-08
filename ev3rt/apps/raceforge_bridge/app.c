/*
 * RaceForge EV3RT bridge (spec 0011): the EV3 under EV3RT 1.1 does what ev3_side does under ev3dev —
 * drive/steer motors and read sensors for the car runtime on the onboard board, over a serial link
 * (UART on sensor port 1 by default; COBS-framed rf-proto v1 frames).
 *
 * Tasks: RX_TASK reads bytes and hands complete commands to the bridge; CTRL_TASK runs every 10 ms
 * (failsafe, e-stop, motor loops, sensor frame back); MAIN_TASK sets up the hardware, shows link
 * statistics on the LCD and ends the app when BACK is held for 1 s.
 */
#include <stdio.h>
#include <string.h>

#include "app.h"
#include "rf_bridge.h"
#include "rf_proto.h"

static rf_bridge_t bridge;
static FILE *link_file;
static volatile const char *status_text = "WAIT LINK";

static uint32_t now_ms(void) {
    SYSTIM t;
    get_tim(&t); /* EV3RT: milliseconds */
    return (uint32_t)t;
}

/* ---- hardware abstraction for the bridge library ---- */
static int32_t hal_counts(int port) { return ev3_motor_get_counts((motor_port_t)port); }
static void hal_power(int port, int power) { ev3_motor_set_power((motor_port_t)port, power); }
static void hal_brake(int port) { ev3_motor_stop((motor_port_t)port, true); }
static int16_t hal_ultrasonic_cm(int port) { return ev3_ultrasonic_sensor_get_distance((sensor_port_t)port); }
static int16_t hal_gyro_rate(int port) { return ev3_gyro_sensor_get_rate((sensor_port_t)port); }
static int16_t hal_gyro_angle(int port) { return ev3_gyro_sensor_get_angle((sensor_port_t)port); }
static int hal_touch(int port) { return ev3_touch_sensor_is_pressed((sensor_port_t)port) ? 1 : 0; }

static uint8_t hal_buttons(void) {
    uint8_t bits = 0;
    if (ev3_button_is_pressed(UP_BUTTON)) bits |= 1u << RF_BTN_UP;
    if (ev3_button_is_pressed(DOWN_BUTTON)) bits |= 1u << RF_BTN_DOWN;
    if (ev3_button_is_pressed(LEFT_BUTTON)) bits |= 1u << RF_BTN_LEFT;
    if (ev3_button_is_pressed(RIGHT_BUTTON)) bits |= 1u << RF_BTN_RIGHT;
    if (ev3_button_is_pressed(ENTER_BUTTON)) bits |= 1u << RF_BTN_ENTER;
    if (ev3_button_is_pressed(BACK_BUTTON)) bits |= 1u << RF_BTN_BACK;
    return bits;
}

static uint16_t hal_battery_mv(void) { return (uint16_t)ev3_battery_voltage_mV(); }

/* Called from the control task: only remember the text and set the LED; MAIN_TASK draws the LCD. */
static void hal_show(const char *status) {
    status_text = status;
    if (strcmp(status, "RUN") == 0) {
        ev3_led_set_color(LED_GREEN);
    } else if (strcmp(status, "STOPPED") == 0) {
        ev3_led_set_color(LED_ORANGE);
    } else {
        ev3_led_set_color(LED_RED); /* LINK LOST, E-STOP, FAULT */
    }
}

static const rf_hal_t HAL = {
    hal_counts, hal_power, hal_brake, hal_ultrasonic_cm, hal_gyro_rate, hal_gyro_angle,
    hal_touch, hal_buttons, hal_battery_mv, hal_show,
};

static sensor_type_t sensor_type(int kind) {
    switch (kind) {
    case RF_SENSOR_ULTRASONIC: return ULTRASONIC_SENSOR;
    case RF_SENSOR_GYRO: return GYRO_SENSOR;
    case RF_SENSOR_TOUCH: return TOUCH_SENSOR;
    default: return NONE_SENSOR;
    }
}

void task_activator(intptr_t tskid) { act_tsk((ID)tskid); }

void rx_task(intptr_t unused) {
    static rf_stream_t stream;
    (void)unused;
    rf_stream_init(&stream);
    for (;;) {
        int ch = fgetc(link_file);
        if (ch == EOF) {
            tslp_tsk(1);
            continue;
        }
        if (rf_stream_push(&stream, (uint8_t)ch)) {
            loc_mtx(MTX_BRIDGE);
            rf_bridge_on_packet(&bridge, stream.packet, stream.packet_len, now_ms());
            unl_mtx(MTX_BRIDGE);
        }
    }
}

void control_task(intptr_t unused) {
    rf_sensor_t s;
    uint8_t frame[RF_SENSOR_LEN];
    uint8_t line[RF_SENSOR_LEN + RF_SENSOR_LEN / 254 + 2];
    size_t n;
    (void)unused;
    loc_mtx(MTX_BRIDGE);
    rf_bridge_step(&bridge, now_ms(), &s);
    unl_mtx(MTX_BRIDGE);
    rf_sensor_encode(&s, frame);
    n = rf_serial_frame(frame, sizeof(frame), line);
    fwrite(line, 1, n, link_file);
    fflush(link_file);
}

static void draw(int row, const char *text) {
    int32_t w, h;
    ev3_font_get_size(EV3_FONT_MEDIUM, &w, &h);
    ev3_lcd_fill_rect(0, row * h, EV3_LCD_WIDTH, h, EV3_LCD_WHITE);
    ev3_lcd_draw_string(text, 0, row * h);
}

void main_task(intptr_t unused) {
    static const int sensors[4] = {RF_SENSOR_1, RF_SENSOR_2, RF_SENSOR_3, RF_SENSOR_4};
    rf_bridge_config_t cfg;
    char buf[40];
    int i, back_held = 0;
    (void)unused;

    ev3_lcd_set_font(EV3_FONT_MEDIUM);
    draw(0, "RaceForge EV3RT");
    ev3_motor_config(EV3_PORT_A + RF_STEER_PORT, RF_STEER_MOTOR);
    ev3_motor_config(EV3_PORT_A + RF_DRIVE_PORT, RF_DRIVE_MOTOR);
    ev3_motor_reset_counts(EV3_PORT_A + RF_STEER_PORT); /* steering straight at start = 0 */
    ev3_motor_reset_counts(EV3_PORT_A + RF_DRIVE_PORT);
    for (i = 0; i < 4; i++) {
        if (sensors[i] != RF_SENSOR_NONE) ev3_sensor_config((sensor_port_t)(EV3_PORT_1 + i), sensor_type(sensors[i]));
        if (sensors[i] == RF_SENSOR_GYRO) ev3_gyro_sensor_reset((sensor_port_t)(EV3_PORT_1 + i));
    }

    link_file = ev3_serial_open_file(RF_LINK_PORT);
    if (link_file == NULL) {
        draw(1, "NO SERIAL PORT");
        draw(2, "see rc.conf.ini");
        ext_tsk();
        return;
    }

    rf_bridge_default_config(&cfg);
    cfg.steer_port = RF_STEER_PORT;
    cfg.drive_port = RF_DRIVE_PORT;
    for (i = 0; i < 4; i++) cfg.sensor[i] = sensors[i];
    cfg.estop_touch_port = RF_ESTOP_TOUCH_PORT;
    cfg.failsafe_ms = RF_FAILSAFE_MS;
    cfg.drive_ff = RF_DRIVE_FF;
    cfg.drive_kp = RF_DRIVE_KP;
    cfg.drive_ki = RF_DRIVE_KI;
    cfg.steer_kp = RF_STEER_KP;
    cfg.steer_min_power = RF_STEER_MIN_POWER;
    cfg.steer_max_power = RF_STEER_MAX_POWER;
    cfg.steer_deadband_deg = RF_STEER_DEADBAND_DEG;
    rf_bridge_init(&bridge, &cfg, &HAL, now_ms());

    act_tsk(RX_TASK);
    ev3_sta_cyc(CYC_CTRL);

    for (;;) {
        rf_bridge_stats_t st;
        loc_mtx(MTX_BRIDGE);
        st = bridge.stats;
        unl_mtx(MTX_BRIDGE);
        draw(1, (const char *)status_text);
        snprintf(buf, sizeof(buf), "rx %lu bad %lu", (unsigned long)st.rx, (unsigned long)st.rx_bad);
        draw(2, buf);
        snprintf(buf, sizeof(buf), "lost %lu  %u mV", (unsigned long)st.failsafe_trips,
                 (unsigned)ev3_battery_voltage_mV());
        draw(3, buf);
        draw(4, "hold BACK 1s: exit");
        back_held = ev3_button_is_pressed(BACK_BUTTON) ? back_held + 1 : 0;
        if (back_held >= 5) break;
        tslp_tsk(200);
    }
    ev3_stp_cyc(CYC_CTRL);
    loc_mtx(MTX_BRIDGE);
    rf_bridge_shutdown(&bridge);
    unl_mtx(MTX_BRIDGE);
    ext_tsk();
}
