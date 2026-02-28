# Procfile — multi-process deployment (Honcho, Foreman, or OpenClaw)
#
# Each agent runs in its own process with its own IBKR client connection.
# Set PROFILE environment variable to switch strategy configuration.
#
# Usage (Honcho / Foreman):
#   honcho start
#   foreman start
#
# Usage (individual processes):
#   oct-listener    --profile discord_equities
#   oct-interpreter --profile discord_equities
#   oct-risk        --profile discord_equities
#   oct-executor    --profile discord_equities

listener:    oct-listener    --profile discord_equities
interpreter: oct-interpreter --profile discord_equities
risk:        oct-risk        --profile discord_equities
executor:    oct-executor    --profile discord_equities
