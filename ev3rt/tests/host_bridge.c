/*
 * The EV3RT bridge logic on the host with simulated motors and sensors (spec 0011): stands in for the
 * brick in end-to-end tests of the laptop side (`raceforge car ev3rt check`, tests/car). Same loop as
 * ev3rt/apps/raceforge_bridge/app.c: bytes -> COBS packets -> rf_bridge_on_packet; every 10 ms
 * rf_bridge_step -> sensor frame back.
 *
 * Usage: host_bridge <serial device>   (e.g. the slave side of a pseudo terminal)
 * Simulated brick: battery 7.8 V, ultrasonic 0.50 m on port 2, gyro on port 4, ENTER pressed for
 * 300 ms every second, first-order motor model (power % -> deg/s, tau 50 ms).
 */
#define _DEFAULT_SOURCE
#define _DARWIN_C_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#include "rf_bridge.h"
#include "rf_proto.h"

static float speed[4];   /* deg/s */
static float counts[4];  /* deg */
static int power[4];
static int braked[4];
static uint32_t t_now;

static uint32_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u);
}

static int32_t hal_counts(int port) { return (int32_t)counts[port]; }
static void hal_power(int port, int p) { power[port] = p; braked[port] = 0; }
static void hal_brake(int port) { power[port] = 0; braked[port] = 1; }
static int16_t hal_ultrasonic_cm(int port) { return port == 1 ? 50 : -1; }
static int16_t hal_gyro_rate(int port) { (void)port; return 0; }
static int16_t hal_gyro_angle(int port) { (void)port; return 0; }
static int hal_touch(int port) { (void)port; return 0; }
static uint8_t hal_buttons(void) { return (t_now % 1000u) < 300u ? (uint8_t)(1u << RF_BTN_ENTER) : 0u; }
static uint16_t hal_battery_mv(void) { return 7800; }
static void hal_show(const char *status) { fprintf(stderr, "bridge: %s\n", status); }

static const rf_hal_t HAL = {
    hal_counts, hal_power, hal_brake, hal_ultrasonic_cm, hal_gyro_rate, hal_gyro_angle,
    hal_touch, hal_buttons, hal_battery_mv, hal_show,
};

static void motors(float dt_s) {
    int i;
    for (i = 0; i < 4; i++) {
        float target = braked[i] ? 0.0f : (float)power[i] * 10.5f;
        float k = dt_s / (braked[i] ? 0.02f : 0.05f);
        speed[i] += (target - speed[i]) * (k > 1.0f ? 1.0f : k);
        counts[i] += speed[i] * dt_s;
    }
}

int main(int argc, char **argv) {
    static rf_bridge_t bridge;
    static rf_stream_t stream;
    rf_bridge_config_t cfg;
    struct termios tio;
    uint32_t next, prev;
    int fd;
    if (argc != 2) {
        fprintf(stderr, "usage: host_bridge <serial device>\n");
        return 2;
    }
    fd = open(argv[1], O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
        perror(argv[1]);
        return 1;
    }
    if (tcgetattr(fd, &tio) == 0) {
        cfmakeraw(&tio);
        tcsetattr(fd, TCSANOW, &tio);
    }
    rf_bridge_default_config(&cfg);
    cfg.sensor[0] = RF_SENSOR_NONE;
    cfg.sensor[1] = RF_SENSOR_ULTRASONIC;
    cfg.sensor[2] = RF_SENSOR_NONE;
    cfg.sensor[3] = RF_SENSOR_GYRO;
    t_now = now_ms();
    rf_bridge_init(&bridge, &cfg, &HAL, t_now);
    rf_stream_init(&stream);
    prev = next = t_now;
    for (;;) {
        uint8_t buf[256];
        ssize_t n = read(fd, buf, sizeof(buf));
        ssize_t i;
        if (n == 0) return 0; /* peer closed */
        if (n < 0 && errno != EAGAIN && errno != EINTR) return errno == EIO ? 0 : 1;
        t_now = now_ms();
        for (i = 0; i < n; i++) {
            if (rf_stream_push(&stream, buf[i])) rf_bridge_on_packet(&bridge, stream.packet, stream.packet_len, t_now);
        }
        if ((int32_t)(t_now - next) >= 0) {
            rf_sensor_t s;
            uint8_t frame[RF_SENSOR_LEN];
            uint8_t line[RF_SENSOR_LEN + RF_SENSOR_LEN / 254 + 2];
            size_t len;
            motors((float)(t_now - prev) / 1000.0f);
            prev = t_now;
            rf_bridge_step(&bridge, t_now, &s);
            rf_sensor_encode(&s, frame);
            len = rf_serial_frame(frame, sizeof(frame), line);
            if (write(fd, line, len) < 0 && errno == EIO) return 0;
            next += 10;
        }
        usleep(1000);
    }
}
