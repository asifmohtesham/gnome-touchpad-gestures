#!/usr/bin/env bash
# Installs gnome-x11-touchpad-gestures. Safe to run repeatedly.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
rule="71-gnome-x11-touchpad-gestures.rules"
unit="gnome-x11-touchpad-gestures.service"
unit_dir="$HOME/.config/systemd/user"
rules_dir="/etc/udev/rules.d"
# Where the program is installed. The unit names the same place as
# %h/.local/share/gnome-x11-touchpad-gestures, so the two must change together.
program_dir="$HOME/.local/share/gnome-x11-touchpad-gestures"
# Exit status of `gnome_x11_touchpad_gestures.daemon --check` when device access is missing.
no_access=3
# ... and when the touchpad cannot tell fingers apart.
unsupported=4

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
    if [ "$status" -eq "$unsupported" ]; then
        echo "This touchpad reports one position, not each finger separately," >&2
        echo "so it cannot be used. Logging out will not help." >&2
    elif [ "$status" -eq "$no_access" ] && [ "$touchpads" -eq 0 ]; then
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

check_access() {
    (cd "$repo" && python3 -m gnome_x11_touchpad_gestures.daemon --check)
}

# Whether the privileged step has anything left to do. A check that fails
# for any reason other than missing access is not something sudo can fix.
needs_sudo() {
    cmp -s "$here/$rule" "$rules_dir/$rule" || return 0
    local status=0
    check_access >/dev/null 2>&1 || status=$?
    [ "$status" -eq "$no_access" ]
}

install_program() {
    local package="gnome_x11_touchpad_gestures"
    local fresh="$program_dir/$package.new" old="$program_dir/$package.old"
    rm -rf "$fresh" "$old"
    mkdir -p "$fresh"
    cp "$repo/$package"/*.py "$fresh/"
    # Swapped in only once it is complete, so a copy that fails leaves the
    # working program where it was. Replaced whole, so a module removed from
    # the repository does not linger.
    if [ -d "$program_dir/$package" ]; then
        mv "$program_dir/$package" "$old"
    fi
    mv "$fresh" "$program_dir/$package"
    rm -rf "$old"
}

install_service() {
    install_program
    mkdir -p "$unit_dir"
    install -m 0644 "$here/$unit" "$unit_dir/$unit"
    systemctl --user daemon-reload
    systemctl --user enable "$unit"
    systemctl --user restart "$unit"
}

main_as() {
    refuse_root "$1"
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

    if needs_sudo; then
        echo "Installing udev rule (needs sudo)..."
        if ! cmp -s "$here/$rule" "$rules_dir/$rule"; then
            sudo install -m 0644 "$here/$rule" "$rules_dir/$rule"
        fi
        sudo udevadm control --reload
        sudo udevadm trigger --action=change --subsystem-match=misc --sysname-match=uinput
        # Only the touchpad: a change event makes X remove and re-add the
        # device, so triggering every input device would briefly drop the
        # keyboard too.
        sudo udevadm trigger --action=change --subsystem-match=input --sysname-match='event*' \
            --property-match=ID_INPUT_TOUCHPAD=1
        sudo udevadm settle
    else
        echo "The udev rule is in place and access is granted: no password needed."
    fi

    echo "Checking device access..."
    local status=0
    check_access || status=$?
    if [ "$status" -ne 0 ]; then
        explain_check_failure "$status" "$(touchpad_count)"
        exit 1
    fi

    echo "Installing the program and the user service..."
    install_service

    sleep 1
    # Exits non-zero for a service that is not running, which is not a failure
    # of the install.
    systemctl --user --no-pager --lines=5 status "$unit" || true
    echo
    if [ "${XDG_SESSION_TYPE:-}" != "x11" ]; then
        echo "This is not an X11 session, so the service has not started."
        echo "It starts by itself at your next X11 login."
    fi
    echo "Installed to $program_dir."
    echo "The service runs that copy, so this directory can be moved or removed."
    echo "After changing the code here, run this script again to install it."
}

main() {
    main_as "$(id -u)"
}

# Sourcing this file defines the functions without running anything.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
