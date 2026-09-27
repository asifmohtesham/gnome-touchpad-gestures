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

touchpad_count() {
    udevadm trigger --dry-run --verbose --subsystem-match=input \
        --sysname-match='event*' --property-match=ID_INPUT_TOUCHPAD=1 | wc -l
}

# Prints the package for each Python module that cannot be imported.
missing_packages() {
    local module
    for module in "$@"; do
        python3 -c "import $module" 2>/dev/null || echo "python3-$module"
    done
}

explain_check_failure() {
    local status="$1" touchpads="$2"
    echo >&2
    if [ "$status" -eq "$no_access" ] && [ "$touchpads" -eq 0 ]; then
        echo "No touchpad was found on this machine, so there is nothing to" >&2
        echo "grant access to. Logging out will not help." >&2
    elif [ "$status" -eq "$no_access" ]; then
        echo "The rule is installed but access has not been granted to this" >&2
        echo "session yet. Log out, log back in, and run this script again." >&2
    else
        echo "The access check failed for another reason (exit status $status)." >&2
        echo "See the error above. Logging out will not help." >&2
    fi
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

install_service() {
    mkdir -p "$unit_dir"
    install -m 0644 "$here/$unit" "$unit_dir/$unit"
    systemctl --user daemon-reload
    systemctl --user enable "$unit"
    systemctl --user restart "$unit"
}

main_as() {
    refuse_root "$1"
    if [ "$repo" != "$HOME/finger-drag" ]; then
        echo "finger-drag must live at $HOME/finger-drag (found $repo)." >&2
        exit 1
    fi
    # Checked before anything is changed on the system.
    local missing
    missing="$(missing_packages evdev dbus | tr '\n' ' ')"
    if [ -n "$missing" ]; then
        echo "Missing: $missing" >&2
        echo "Install with:  sudo apt install $missing" >&2
        exit 1
    fi
    if [ "${XDG_SESSION_TYPE:-}" != "x11" ]; then
        echo "Warning: this session is '${XDG_SESSION_TYPE:-unknown}', not x11." >&2
        echo "The service only starts in an X11 session." >&2
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
        explain_check_failure "$status" "$(touchpad_count)"
        exit 1
    fi

    echo "Installing user service..."
    install_service

    sleep 1
    systemctl --user --no-pager --lines=5 status "$unit"
}

main() {
    main_as "$(id -u)"
}

# Sourcing this file defines the functions without running anything.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
