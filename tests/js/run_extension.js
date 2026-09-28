// Runs the real extension.js outside a shell, for tests.
//
// Usage: gjs -m run_extension.js <directory holding a copy of the extension
// whose imports from the shell point at the stand-ins>
//
// It must only ever run on a bus of its own: it takes the shell's name.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import System from 'system';

const directory = ARGV[0];
const Main = await import(`file://${directory}/stand_in_main.js`);

// What the pointer can be over, by name.
const stage = {name: 'stage', get_parent: () => null};
const uiGroup = {name: 'uiGroup', get_parent: () => stage};
const windowGroup = {name: 'window_group', get_parent: () => uiGroup};
const topWindowGroup = {name: 'top_window_group', get_parent: () => uiGroup};
const under = {
    window: {get_parent: () => ({get_parent: () => windowGroup})},
    popup: {get_parent: () => topWindowGroup},
    panel: {get_parent: () => ({get_parent: () => uiGroup})},
    stage,
    nothing: null,
};
let picked = 'window';
const picks = [];

globalThis.global = {
    stage: Object.assign(stage, {
        get_actor_at_pos(mode, x, y) {
            picks.push([mode, x, y]);
            return under[picked];
        },
    }),
    window_group: windowGroup,
    top_window_group: topWindowGroup,
    get_pointer: () => [640, 360, 0],
};

const {default: GesturesExtension} = await import(`file://${directory}/extension.js`);
const [, bytes] = GLib.file_get_contents(`${directory}/metadata.json`);
const extension = new GesturesExtension(JSON.parse(new TextDecoder().decode(bytes)));

const CONTROL = `
<node>
  <interface name="test.Control">
    <method name="Pick"><arg type="s" direction="in"/></method>
    <method name="Overview"><arg type="b" direction="in"/></method>
    <method name="Mode"><arg type="u" direction="in"/></method>
    <method name="TrackerEnabled"><arg type="b" direction="in"/></method>
    <method name="Disable"/>
    <method name="Enable"/>
    <method name="Seen"><arg type="s" direction="out"/></method>
    <method name="Quit"/>
  </interface>
</node>`;

const loop = new GLib.MainLoop(null, false);
const control = Gio.DBusExportedObject.wrapJSObject(CONTROL, {
    Pick(what) {
        picked = what;
    },
    Overview(visible) {
        Main.overview.visible = visible;
    },
    Mode(mode) {
        Main.setActionMode(mode);
    },
    TrackerEnabled(enabled) {
        Main.tracker.enabled = enabled;
    },
    Disable() {
        extension.disable();
    },
    Enable() {
        extension.enable();
    },
    Seen() {
        return JSON.stringify({calls: Main.calls, picks});
    },
    Quit() {
        loop.quit();
    },
});

control.export(Gio.DBus.session, '/test/Control');
extension.enable();
Gio.bus_own_name_on_connection(
    Gio.DBus.session, 'org.gnome.Shell', Gio.BusNameOwnerFlags.NONE,
    () => print('READY'), () => {
        print('NAME TAKEN');
        System.exit(3);
    });
GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 30, () => {
    loop.quit();
    return GLib.SOURCE_REMOVE;
});
loop.run();
