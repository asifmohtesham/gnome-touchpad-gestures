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
# The part that runs inside GNOME Shell. Without it the gestures still work,
# but workspaces snap across and glides are held back only in the overview.
extension="gnome-x11-touchpad-gestures@asifmohtesham.github.io"
extensions_dir="$HOME/.local/share/gnome-shell/extensions"
# What this project installed when it was called finger-drag. Left in place,
# the old service would run beside the new one and every gesture would
# happen twice, and the old rule would go on granting access after an
# uninstall. An upgrade removes all three.
former="finger-drag"
former_unit="$unit_dir/$former.service"
former_program="$HOME/.local/share/$former"
former_rule="71-$former.rules"
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
    [ ! -e "$rules_dir/$former_rule" ] || return 0
    local status=0
    check_access >/dev/null 2>&1 || status=$?
    [ "$status" -eq "$no_access" ]
}

# Copies the files given into a directory, replacing what was there.
#
# The first argument is where they go, the second where the work is done:
# the new copy is built as <second>.new and swapped in only once it is
# complete, so a copy that fails leaves what worked where it was. It is
# replaced whole, so a file removed from the repository does not linger.
put_in_place() {
    local target="$1" beside="$2"
    shift 2
    local fresh="$beside.new" old="$beside.old"
    rm -rf "$fresh" "$old"
    mkdir -p "$fresh" "$(dirname "$target")"
    cp "$@" "$fresh/"
    if [ -e "$target" ] || [ -L "$target" ]; then
        mv "$target" "$old"
    fi
    if ! mv "$fresh" "$target"; then
        if [ -e "$old" ] || [ -L "$old" ]; then
            mv "$old" "$target"
        fi
        rm -rf "$fresh"
        return 1
    fi
    rm -rf "$old"
}

install_program() {
    local package="gnome_x11_touchpad_gestures"
    put_in_place "$program_dir/$package" "$program_dir/$package" \
        "$repo/$package"/*.py
}

# Prints a list of extensions with ours put in ("with") or taken out
# ("without"), or nothing if that changes nothing or the list cannot be
# understood. A list that cannot be read is never written over.
edited_list() {
    python3 - "$extension" "$1" "$2" <<'PYTHON'
import ast
import sys

uuid, change, listed = sys.argv[1], sys.argv[2], sys.argv[3].strip()
if listed.startswith("@as "):
    listed = listed[4:]
try:
    names = ast.literal_eval(listed)
except (SyntaxError, ValueError):
    sys.exit(0)
if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
    sys.exit(0)
wanted = [name for name in names if name != uuid]
if change == "with":
    wanted = names if uuid in names else names + [uuid]
if wanted != names:
    print(wanted)
PYTHON
}

# Puts the extension in, or takes it out of, one of the shell's two lists.
list_extension() {
    local key="$1" change="$2" edited
    edited="$(edited_list "$change" "$(gsettings get org.gnome.shell "$key")")"
    if [ -n "$edited" ]; then
        gsettings set org.gnome.shell "$key" "$edited"
    fi
}

install_extension() {
    # Named one by one: whatever else lies in that directory, an editor's
    # backup or a note, is no part of the extension. The copy is made ready
    # beside the program, not among the extensions, where the shell would
    # take what a failed copy left behind for an extension.
    local from="$repo/extension/$extension"
    put_in_place "$extensions_dir/$extension" "$program_dir/extension" \
        "$from/extension.js" "$from/gestures.js" "$from/metadata.json"
    # A shell that has not seen the extension yet cannot be asked to switch
    # it on. It is then switched on in the settings, which the shell reads
    # when it next starts. Switching it off had the shell write it down
    # among those switched off, and that list is the one that wins.
    if ! gnome-extensions enable "$extension" 2>/dev/null; then
        list_extension enabled-extensions with
        list_extension disabled-extensions without
    fi
}

remove_former_service() {
    if [ -e "$former_unit" ] || [ -e "$former_program" ]; then
        echo "Removing the version installed as $former..."
        systemctl --user disable --now "$former.service" 2>/dev/null || true
        rm -f "$former_unit"
        rm -rf "$former_program"
    fi
}

install_service() {
    remove_former_service
    install_program
    install_extension
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
        if [ -e "$rules_dir/$former_rule" ]; then
            sudo rm -f "$rules_dir/$former_rule"
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
        echo "The udev rule is already in place: no password needed."
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
    echo
    echo "The shell extension is loaded when GNOME Shell starts. If it is new or"
    echo "has changed, restart the shell: press Alt+F2, type r, press Enter."
    echo "Your windows stay open. Until then workspaces snap across as before."
}

main() {
    main_as "$(id -u)"
}

# Sourcing this file defines the functions without running anything.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
