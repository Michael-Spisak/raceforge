"""RaceForge EV3-side program (spec 0005): bridges the EV3's motors and sensors to the board.

Runs on ev3dev (Python 3.5): keep the code to the 3.5 subset (no f-strings, no variable
annotations, no dataclasses).
"""

# UDP port the EV3 program listens on (the board's rf-ev3 crate uses the same default).
DEFAULT_PORT = 47100
