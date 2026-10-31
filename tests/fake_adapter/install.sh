#!/bin/sh
# Record how suss started this command, then do what the test wrote to install.then.
here=$(dirname "$0")
printf '%s\n' "$@" > "$here/install.args"
pwd > "$here/install.cwd"
env > "$here/install.env"
if [ -f "$here/install.then" ]; then
    . "$here/install.then"
fi
