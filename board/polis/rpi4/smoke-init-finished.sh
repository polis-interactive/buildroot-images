#!/bin/sh
# Set diagnostic baud before rcS, including its optional S99 smoke output.
# Without a serial console/getty, Linux initializes this UART at 9600 baud.
# Polis images include polis.smoke=1 by default; stock uses the runner's opt-in.
smoke=0
case " $(cat /proc/cmdline) " in
    *" polis.smoke=1 "*) smoke=1 ;;
esac
if [ "$smoke" = 1 ]; then
    stty -F /dev/ttyAMA0 115200 raw -echo -crtscts clocal || exit $?
fi
# The readiness marker follows all init scripts, before starting any getty.
/etc/init.d/rcS || exit $?
[ "$smoke" = 1 ] || exit 0
read -r uptime idle </proc/uptime
printf 'POLIS_SMOKE_READY init uptime=%s\n' "$uptime" >/dev/ttyAMA0
