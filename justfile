# agent-command-guards — install and deploy.

default:
    @just --list

# Link the agent launchers and write their environment for this machine.
install:
    ./launchers/setup

# Install on Studio through agent-host-sync; account is agent or osteele.
deploy account="agent":
    agent-host-sync --account {{account}} apply --stage tools --tool agent-command-guards

# Show what deploying to Studio would change, without changing anything.
deploy-status account="agent":
    agent-host-sync --account {{account}} plan --stage tools --tool agent-command-guards
