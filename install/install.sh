#!/usr/bin/env bash
# Installs finger-drag. Safe to run repeatedly.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
rule="71-finger-drag.rules"
unit="finger-drag.service"
unit_dir="$HOME/.config/systemd/user"
# Exit status of `finger_drag.daemon --check` when device access is missing.
no_access=3

explain_check_failure() {
    local status="$1"
    echo >&2
    if [ "$status" -eq "$no_access" ]; then
        echo "The rule is installed but access has not been granted to this" >&2
        echo "session yet. Log out, log back in, and run this script again." >&2
    else
        echo "The access check failed for another reason (exit status $status)." >&2
        echo "See the error above. Logging out will not help." >&2
    fi
}

main() {
    if [ "$repo" != "$HOME/finger-drag" ]; then
        echo "finger-drag must live at $HOME/finger-drag (found $repo)." >&2
        exit 1
    fi
    # Checked before anything is changed on the system.
    if ! python3 -c 'import evdev' 2>/dev/null; then
        echo "python3-evdev is missing. Install it with:" >&2
        echo "    sudo apt install python3-evdev" >&2
        exit 1
    fi

    echo "Installing udev rule (needs sudo)..."
    if ! cmp -s "$here/$rule" "/etc/udev/rules.d/$rule"; then
        sudo install -m 0644 "$here/$rule" "/etc/udev/rules.d/$rule"
    fi
    sudo udevadm control --reload
    sudo udevadm trigger --action=change --subsystem-match=misc --sysname-match=uinput
    # Only the touchpad: a change event makes X remove and re-add the device,
    # so triggering every input device would briefly drop the keyboard too.
    sudo udevadm trigger --action=change --subsystem-match=input --sysname-match='event*' \
        --property-match=ID_INPUT_TOUCHPAD=1
    sudo udevadm settle

    echo "Checking device access..."
    local status=0
    (cd "$repo" && python3 -m finger_drag.daemon --check) || status=$?
    if [ "$status" -ne 0 ]; then
        explain_check_failure "$status"
        exit 1
    fi

    echo "Installing user service..."
    mkdir -p "$unit_dir"
    install -m 0644 "$here/$unit" "$unit_dir/$unit"
    systemctl --user daemon-reload
    systemctl --user enable "$unit"
    systemctl --user restart "$unit"

    sleep 1
    systemctl --user --no-pager --lines=5 status "$unit"
}

# Sourcing this file defines the functions without running anything.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
