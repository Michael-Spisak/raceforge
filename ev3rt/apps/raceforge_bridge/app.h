/* RaceForge EV3RT bridge app (spec 0011). */
#include "ev3api.h"
#include "target_test.h"

#include "rf_config.h"

#define PRIORITY_CTRL_TASK TMIN_APP_TPRI
#define PRIORITY_RX_TASK (TMIN_APP_TPRI + 1)
#define PRIORITY_MAIN_TASK (TMIN_APP_TPRI + 2)

#ifndef STACK_SIZE
#define STACK_SIZE 4096
#endif

#ifndef TOPPERS_MACRO_ONLY
extern void main_task(intptr_t exinf);
extern void control_task(intptr_t exinf);
extern void rx_task(intptr_t exinf);
extern void task_activator(intptr_t tskid);
#endif
