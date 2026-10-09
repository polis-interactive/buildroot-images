#!/bin/sh
# Wrap rcS so the marker follows all init scripts, before starting any getty.
/etc/init.d/rcS || exit $?
# Normal hardware boots never open or write to the application UART.
case " $(cat /proc/cmdline) " in
    *" polis.smoke=1 "*) ;;
    *) exit 0 ;;
esac
read -r uptime idle </proc/uptime
printf 'POLIS_SMOKE_READY init uptime=%s\n' "$uptime" >/dev/ttyAMA0
