#!/bin/sh
# Record how suss started this command, then do what the test wrote to run.then.
here=$(dirname "$0")
printf '%s\n' "$@" > "$here/run.args"
pwd > "$here/run.cwd"
env > "$here/run.env"
if [ -f "$here/run.then" ]; then
    . "$here/run.then"
fi
