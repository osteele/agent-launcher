# agent-launcher — install and test.

default:
    @just --list

# Link the agent launchers and write their shell environment for this machine.
install:
    ./launchers/setup

# Run the test suite.
test:
    python3 -m unittest
