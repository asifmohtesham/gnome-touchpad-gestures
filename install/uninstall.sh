#!/usr/bin/env bash
# Removes gnome-touchpad-gestures and revokes the device access it was given.
set -euo pipefail

rules_dir="/etc/udev/rules.d"
rule="71-gnome-touchpad-gestures.rules"
# The names this project has been installed under before.
formers=("finger-drag" "gnome-x11-touchpad-gestures")
former_extension="gnome-x11-touchpad-gestures@asifmohtesham.github.io"
unit="gnome-touchpad-gestures.service"
unit_file="$HOME/.config/systemd/user/$unit"
program_dir="$HOME/.local/share/gnome-touchpad-gestures"
extension="gnome-touchpad-gestures@asifmohtesham.github.io"
extension_dir="$HOME/.local/share/gnome-shell/extensions/$extension"

touchpad_nodes() {
    local path
    udevadm trigger --dry-run --verbose --subsystem-match=input \
        --sysname-match='event*' --property-match=ID_INPUT_TOUCHPAD=1 |
    while read -r path; do
        echo "/dev/$(udevadm info --query=name --path="$path")"
    done
}

# Prints a list of extensions with ours taken out, or nothing if it is not
# in it or the list cannot be understood. A list that cannot be read is
# never written over.
without_extension() {
    python3 - "$extension" "$1" <<'PYTHON'
import ast
import sys

uuid, listed = sys.argv[1], sys.argv[2].strip()
if listed.startswith("@as "):
    listed = listed[4:]
try:
    names = ast.literal_eval(listed)
except (SyntaxError, ValueError):
    sys.exit(0)
if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
    sys.exit(0)
if uuid in names:
    print([name for name in names if name != uuid])
PYTHON
}

# Takes the extension out of one of the shell's two lists.
unlist_extension() {
    local key="$1" edited
    edited="$(without_extension "$(gsettings get org.gnome.shell "$key")")"
    if [ -n "$edited" ]; then
        gsettings set org.gnome.shell "$key" "$edited"
    fi
}

# The extension as it was called before the project was renamed.
remove_former_extension() {
    # The functions above work on $extension, which for their duration is
    # the former one.
    local extension="$former_extension"
    gnome-extensions disable "$extension" 2>/dev/null || true
    unlist_extension enabled-extensions
    unlist_extension disabled-extensions
    rm -rf "$(dirname "$extension_dir")/$extension"
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
    echo "Stopping and removing the user service and the program..."
    systemctl --user disable --now "$unit" 2>/dev/null || true
    rm -f "$unit_file"
    rm -rf "$program_dir"
    local former
    for former in "${formers[@]}"; do
        systemctl --user disable --now "$former.service" 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/$former.service"
        rm -rf "$HOME/.local/share/$former"
    done
    systemctl --user daemon-reload

    echo "Switching off and removing the shell extension..."
    # The shell switches it off at once, if it has it loaded, and writes it
    # down among those switched off. A shell that has not loaded it cannot
    # be asked, and leaves it among those switched on. Either way the
    # settings are cleared of it by hand, so that nothing is left behind.
    gnome-extensions disable "$extension" 2>/dev/null || true
    unlist_extension enabled-extensions
    unlist_extension disabled-extensions
    rm -rf "$extension_dir"
    remove_former_extension

    echo "Removing udev rule and device access (needs sudo)..."
    sudo rm -f "$rules_dir/$rule"
    for former in "${formers[@]}"; do
        sudo rm -f "$rules_dir/71-$former.rules"
    done
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

    echo "gnome-touchpad-gestures removed. This repository was left in place."
}

main() {
    main_as "$(id -u)"
}

# Sourcing this file defines the functions without running anything.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
