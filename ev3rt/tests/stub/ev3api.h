/* Host stand-in for EV3RT's ev3api.h: just enough declarations (copied names/signatures from the
 * EV3RT 1.1 SDK headers) to syntax-check the EV3RT apps on a development machine (spec 0011).
 * The real build uses the EV3RT SDK (CI job "ev3rt"). */
#ifndef RF_STUB_EV3API_H
#define RF_STUB_EV3API_H
#include <stdint.h>
#include <stdio.h>
typedef int ER;
typedef int ID;
typedef int bool_t;
typedef uint32_t SYSTIM;
#define true 1
#define false 0
#define TMIN_APP_TPRI 5
typedef enum { EV3_PORT_A = 0, EV3_PORT_B, EV3_PORT_C, EV3_PORT_D } motor_port_t;
typedef enum { NONE_MOTOR = 0, MEDIUM_MOTOR, LARGE_MOTOR, UNREGULATED_MOTOR } motor_type_t;
typedef enum { EV3_PORT_1 = 0, EV3_PORT_2, EV3_PORT_3, EV3_PORT_4 } sensor_port_t;
typedef enum { NONE_SENSOR = 0, ULTRASONIC_SENSOR, GYRO_SENSOR, TOUCH_SENSOR } sensor_type_t;
typedef enum { LEFT_BUTTON = 0, RIGHT_BUTTON, UP_BUTTON, DOWN_BUTTON, ENTER_BUTTON, BACK_BUTTON } button_t;
typedef enum { LED_OFF = 0, LED_RED = 1, LED_GREEN = 2, LED_ORANGE = 3 } ledcolor_t;
typedef enum { EV3_FONT_SMALL, EV3_FONT_MEDIUM } lcdfont_t;
typedef enum { EV3_LCD_WHITE = 0, EV3_LCD_BLACK = 1 } lcdcolor_t;
typedef enum { EV3_SERIAL_DEFAULT = 0, EV3_SERIAL_UART = 1, EV3_SERIAL_BT = 2, EV3_SERIAL_USB_CDC = 3 } serial_port_t;
#define EV3_LCD_WIDTH (178)
ER ev3_motor_config(motor_port_t port, motor_type_t type);
int32_t ev3_motor_get_counts(motor_port_t port);
ER ev3_motor_reset_counts(motor_port_t port);
ER ev3_motor_set_power(motor_port_t port, int power);
ER ev3_motor_stop(motor_port_t port, bool_t brake);
ER ev3_sensor_config(sensor_port_t port, sensor_type_t type);
int16_t ev3_ultrasonic_sensor_get_distance(sensor_port_t port);
int16_t ev3_gyro_sensor_get_rate(sensor_port_t port);
int16_t ev3_gyro_sensor_get_angle(sensor_port_t port);
ER ev3_gyro_sensor_reset(sensor_port_t port);
bool_t ev3_touch_sensor_is_pressed(sensor_port_t port);
bool_t ev3_button_is_pressed(button_t button);
int ev3_battery_voltage_mV(void);
ER ev3_led_set_color(ledcolor_t color);
ER ev3_lcd_set_font(lcdfont_t font);
ER ev3_font_get_size(lcdfont_t font, int32_t *w, int32_t *h);
ER ev3_lcd_draw_string(const char *str, int32_t x, int32_t y);
ER ev3_lcd_fill_rect(int32_t x, int32_t y, int32_t w, int32_t h, lcdcolor_t color);
FILE *ev3_serial_open_file(serial_port_t port);
ER ev3_sta_cyc(ID id);
ER ev3_stp_cyc(ID id);
/* kernel */
ER act_tsk(ID id);
ER tslp_tsk(int32_t ms);
ER get_tim(SYSTIM *t);
ER loc_mtx(ID id);
ER unl_mtx(ID id);
void ext_tsk(void);
/* ids that the kernel configurator generates from app.cfg */
#define MAIN_TASK 1
#define CTRL_TASK 2
#define RX_TASK 3
#define CYC_CTRL 4
#define MTX_BRIDGE 5
#endif
