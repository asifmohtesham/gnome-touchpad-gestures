#!/usr/bin/env bash
# Removes finger-drag and revokes the device access it was given.
set -euo pipefail

rule="/etc/udev/rules.d/71-finger-drag.rules"
unit="finger-drag.service"
unit_file="$HOME/.config/systemd/user/$unit"

touchpad_nodes() {
    local path
    udevadm trigger --dry-run --verbose --subsystem-match=input \
        --sysname-match='event*' --property-match=ID_INPUT_TOUCHPAD=1 |
    while read -r path; do
        echo "/dev/$(udevadm info --query=name --path="$path")"
    done
}

refuse_root() {
    local uid="$1"
    if [ "$uid" -eq 0 ]; then
        echo "Run this as your normal user, not as root and not with sudo." >&2
        echo "It installs a service for your own desktop session, and asks" >&2
        echo "for your password itself for the one step that needs it." >&2
        exit 1
    fi
}

main_as() {
    refuse_root "$1"
    echo "Stopping and removing the user service..."
    systemctl --user disable --now "$unit" 2>/dev/null || true
    rm -f "$unit_file"
    systemctl --user daemon-reload

    echo "Removing udev rule and device access (needs sudo)..."
    sudo rm -f "$rule"
    sudo udevadm control --reload
    # udev remembers that it tagged these devices, and the login manager would
    # grant access again from that memory. A fresh look at them clears it.
    sudo udevadm trigger --action=change --subsystem-match=misc --sysname-match=uinput
    sudo udevadm trigger --action=change --subsystem-match=input --sysname-match='event*' \
        --property-match=ID_INPUT_TOUCHPAD=1
    sudo udevadm settle
    # Removing the rule does not take back access that was already granted.
    local node
    for node in /dev/uinput $(touchpad_nodes); do
        sudo setfacl -x "u:$USER" "$node"
    done

    echo "finger-drag removed. The repository itself was left in place."
}

main() {
    main_as "$(id -u)"
}

# Sourcing this file defines the functions without running anything.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
