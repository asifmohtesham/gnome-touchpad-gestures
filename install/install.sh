#!/usr/bin/env bash
# Installs gnome-touchpad-gestures. Safe to run repeatedly.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
rule="71-gnome-touchpad-gestures.rules"
unit="gnome-touchpad-gestures.service"
unit_dir="$HOME/.config/systemd/user"
rules_dir="/etc/udev/rules.d"
# Where the program is installed. The unit names the same place as
# %h/.local/share/gnome-touchpad-gestures, so the two must change together.
program_dir="$HOME/.local/share/gnome-touchpad-gestures"
# The part that runs inside GNOME Shell. On X11 the gestures work without
# it, but workspaces snap across and glides are held back only in the
# overview. On Wayland there is no drag without it.
extension="gnome-touchpad-gestures@asifmohtesham.github.io"
extensions_dir="$HOME/.local/share/gnome-shell/extensions"
# The names this project has been installed under before. Left in place,
# an old service would run beside the new one and every gesture would
# happen twice, an old rule would go on granting access after an uninstall,
# and an old extension would answer the daemon beside the new one. An
# upgrade removes them all.
formers=("finger-drag" "gnome-x11-touchpad-gestures")
former_extension="gnome-x11-touchpad-gestures@asifmohtesham.github.io"
# Exit status of `gnome_touchpad_gestures.daemon --check` when device access is missing.
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
    (cd "$repo" && python3 -m gnome_touchpad_gestures.daemon --check)
}

# Whether the privileged step has anything left to do. A check that fails
# for any reason other than missing access is not something sudo can fix.
needs_sudo() {
    cmp -s "$here/$rule" "$rules_dir/$rule" || return 0
    local former
    for former in "${formers[@]}"; do
        [ ! -e "$rules_dir/71-$former.rules" ] || return 0
    done
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
    local package="gnome_touchpad_gestures"
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

# Whether installing the extension changed what was installed: yes or no.
# Set by install_extension.
extension_changed="yes"

# Whether the extension installed is, file for file, the one given.
extension_is_installed_from() {
    local from="$1" file
    for file in extension.js gestures.js metadata.json; do
        cmp -s "$from/$file" "$extensions_dir/$extension/$file" || return 1
    done
}

# The version of the extension the shell has loaded, or nothing if it has
# loaded none or cannot be asked. Asked by the daemon's own client.
loaded_extension_version() {
    (cd "$repo" && python3 -c '
from gnome_touchpad_gestures.shell import Shell
print(Shell().extension_version() or "")') 2>/dev/null || true
}

this_version() {
    (cd "$repo" && python3 -c '
import gnome_touchpad_gestures
print(gnome_touchpad_gestures.__version__)')
}

# Says whether the shell has to be made to load the extension. It loads it
# when it starts, and goes on running what it loaded whatever is installed
# after. On X11 the shell can be restarted where it stands. On Wayland it
# cannot, and what is needed is to log out.
explain_extension() {
    local loaded="$1" version="$2" changed="$3" session="${4:-}"
    local how="Restart the shell to load it:
press Alt+F2, type r, press Enter. Your windows stay open."
    local until="Until then workspaces snap across, and glides are held back only in
the overview."
    if [ "$session" = "wayland" ]; then
        how="Log out and log back in to load it."
        until="Until then there is no three-finger drag."
    fi
    if [ -z "$loaded" ]; then
        echo "The shell has not loaded the extension. $how"
        echo "$until"
    elif [ "$loaded" != "$version" ]; then
        echo "The shell still runs version $loaded of the extension, and this is"
        echo "$version. $how"
    elif [ "$changed" = "yes" ]; then
        echo "The extension has changed, and the shell still runs it as it was."
        echo "$how"
    else
        echo "The shell has the extension loaded, version $loaded. No restart is needed."
    fi
}

# Whether the service starts in this kind of session.
session_is_supported() {
    case "${XDG_SESSION_TYPE:-}" in
        x11|wayland) return 0 ;;
        *) return 1 ;;
    esac
}

install_extension() {
    # Named one by one: whatever else lies in that directory, an editor's
    # backup or a note, is no part of the extension. The copy is made ready
    # beside the program, not among the extensions, where the shell would
    # take what a failed copy left behind for an extension.
    local from="$repo/extension/$extension"
    extension_changed="yes"
    if extension_is_installed_from "$from"; then
        extension_changed="no"
    fi
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
    local former
    for former in "${formers[@]}"; do
        local former_unit="$unit_dir/$former.service"
        local former_program="$HOME/.local/share/$former"
        if [ -e "$former_unit" ] || [ -e "$former_program" ]; then
            echo "Removing the version installed as $former..."
            systemctl --user disable --now "$former.service" 2>/dev/null || true
            rm -f "$former_unit"
            rm -rf "$former_program"
        fi
    done
    if [ -e "$extensions_dir/$former_extension" ]; then
        # The functions below work on $extension, which for their duration
        # is the former one.
        local extension="$former_extension"
        gnome-extensions disable "$extension" 2>/dev/null || true
        list_extension enabled-extensions without
        list_extension disabled-extensions without
        rm -rf "${extensions_dir:?}/$extension"
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
    if ! session_is_supported; then
        echo "Warning: this session is '${XDG_SESSION_TYPE:-unknown}'." >&2
        echo "The service only starts in an X11 or a Wayland session." >&2
    fi

    if needs_sudo; then
        echo "Installing udev rule (needs sudo)..."
        if ! cmp -s "$here/$rule" "$rules_dir/$rule"; then
            sudo install -m 0644 "$here/$rule" "$rules_dir/$rule"
        fi
        local former
        for former in "${formers[@]}"; do
            if [ -e "$rules_dir/71-$former.rules" ]; then
                sudo rm -f "$rules_dir/71-$former.rules"
            fi
        done
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
    if ! session_is_supported; then
        echo "This is not an X11 or Wayland session, so the service has not started."
        echo "It starts by itself at your next login to one."
    fi
    echo "Installed to $program_dir."
    echo "The service runs that copy, so this directory can be moved or removed."
    echo "After changing the code here, run this script again to install it."
    echo
    # Asked now, not before: switching the extension on may be what made
    # the shell load it.
    explain_extension "$(loaded_extension_version)" "$(this_version)" \
        "$extension_changed" "${XDG_SESSION_TYPE:-}"
}

main() {
    main_as "$(id -u)"
}

# Sourcing this file defines the functions without running anything.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
