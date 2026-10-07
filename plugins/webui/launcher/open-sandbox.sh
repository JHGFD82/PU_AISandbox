#!/bin/sh
#
# Opens the Princeton University AI Sandbox. What the icon on a Mac or Linux
# desktop runs, with the Python the icon was made with as its one argument.
#
# The icon doesn't run that Python directly, because a Python can be removed,
# or upgraded to one at a different path, and an icon that runs a Python that
# isn't there any more does nothing at all — no window, no message. So this
# tries that one, then the usual places for one, and runs the sandbox's
# start.py with the first that works. start.py itself finds a newer Python if
# the one it is started with is too old, and says so if there isn't one.
#
# If there is no Python at all, it opens loading.html, beside this file,
# straight from disk. Not filled in by anything, that page explains that
# Python needs to be installed.
#
# Written for plain sh, which every Mac and Linux computer has, because it
# has to work when nothing else does.

here=$(cd "$(dirname "$0")" && pwd)
start="$here/../../../start.py"

for python in "$1" \
              /opt/homebrew/bin/python3 \
              /usr/local/bin/python3 \
              /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
              python3 \
              /usr/bin/python3; do
    [ -n "$python" ] || continue
    # Run, not just looked for: on a Mac without Apple's developer tools,
    # /usr/bin/python3 is there but only offers to install them.
    if "$python" -c "" >/dev/null 2>&1; then
        exec "$python" "$start" --launch
    fi
done

if command -v open >/dev/null 2>&1; then
    exec open "$here/loading.html"
fi
exec xdg-open "$here/loading.html"
