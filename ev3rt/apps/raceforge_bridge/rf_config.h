/* Default configuration; `raceforge car ev3rt build` replaces this file with one generated from the
 * car bundle (ports, sensors, gains, link). Spec 0011. */
#ifndef RF_CONFIG_H
#define RF_CONFIG_H

#define RF_CONTROL_PERIOD_MS 10
/* Link: EV3_SERIAL_UART (sensor port 1), EV3_SERIAL_USB_CDC or EV3_SERIAL_BT (testing only) */
#define RF_LINK_PORT EV3_SERIAL_UART

#define RF_STEER_PORT 0 /* A */
#define RF_DRIVE_PORT 1 /* B */
#define RF_STEER_MOTOR MEDIUM_MOTOR
#define RF_DRIVE_MOTOR LARGE_MOTOR
/* RF_SENSOR_NONE / ULTRASONIC / GYRO / TOUCH for ports 1..4 (port 1 is the UART link by default) */
#define RF_SENSOR_1 RF_SENSOR_NONE
#define RF_SENSOR_2 RF_SENSOR_ULTRASONIC
#define RF_SENSOR_3 RF_SENSOR_ULTRASONIC
#define RF_SENSOR_4 RF_SENSOR_GYRO
#define RF_ESTOP_TOUCH_PORT (-1)
#define RF_FAILSAFE_MS 150

#define RF_DRIVE_FF (100.0f / 1050.0f)
#define RF_DRIVE_KP 0.05f
#define RF_DRIVE_KI 0.2f
#define RF_STEER_KP 1.5f
#define RF_STEER_MIN_POWER 8
#define RF_STEER_MAX_POWER 60
#define RF_STEER_DEADBAND_DEG 1

#endif
