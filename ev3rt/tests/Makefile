# Host build of the EV3RT bridge library tests (spec 0011 AC1). Run: make -C ev3rt/tests
CC ?= cc
CFLAGS ?= -std=c99 -O1 -g -Wall -Wextra -Werror -pedantic -Wshadow -Wconversion -Wno-sign-conversion -fsanitize=address,undefined
OUT ?= build

all: test check-apps

$(OUT)/test_bridge: test_bridge.c ../common/rf_proto.c ../common/rf_bridge.c ../common/rf_proto.h ../common/rf_bridge.h
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -I../common -o $@ test_bridge.c ../common/rf_proto.c ../common/rf_bridge.c -lm

test: $(OUT)/test_bridge
	./$(OUT)/test_bridge

clean:
	rm -rf $(OUT)

.PHONY: all test clean

# Syntax/type check of the EV3RT apps against the stub ev3api.h (the real build needs the EV3RT SDK).
check-apps:
	$(CC) -std=c99 -Wall -Wextra -Werror -fsyntax-only -Istub -I../common -I../apps/raceforge_bridge ../apps/raceforge_bridge/app.c

.PHONY: check-apps
