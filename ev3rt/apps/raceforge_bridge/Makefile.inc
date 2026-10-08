# RaceForge EV3RT bridge (spec 0011). `raceforge car ev3rt build` copies ev3rt/common/*.c and the generated
# rf_config.h next to these files inside the EV3RT SDK workspace, then runs `make app=raceforge_bridge`.
APPL_COBJS += rf_proto.o rf_bridge.o

SRCLANG := c
