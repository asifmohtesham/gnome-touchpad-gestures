// Stands in for the shell's resource:///org/gnome/shell/misc/config.js.
// The runner says which version of the shell this is.
export let PACKAGE_VERSION = '50.1';

export function setVersion(version) {
    PACKAGE_VERSION = version;
}
