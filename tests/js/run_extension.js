// Runs the real extension.js outside a shell, for tests.
//
// Usage: gjs -m run_extension.js <directory holding a copy of the extension
// whose imports from the shell point at the stand-ins> <x11|wayland>
// <version of the shell>
//
// It must only ever run on a bus of its own: it takes the shell's name.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import System from 'system';

const directory = ARGV[0];
const Main = await import(`file://${directory}/stand_in_main.js`);
// The real one, so that what the extension builds on it is really built.
const {default: Clutter} = await import('gi://Clutter');
const Meta = await import(`file://${directory}/stand_in_meta.js`);
const Config = await import(`file://${directory}/stand_in_config.js`);
Config.setVersion(ARGV[2]);
// Shells from 50 on have no X11 session and cannot be asked which they run.
Meta.setSession(ARGV[1] === 'wayland', parseInt(ARGV[2]) < 50);

// The actions put on the stage, so that events can be put through them as
// the shell puts them: those of the capture phase first, and an action
// that handles an event ends the matter.
const actions = [];

function through(event) {
    for (const {phase, action} of actions) {
        if (phase === Clutter.EventPhase.CAPTURE &&
            action.vfunc_handle_event(event) === true)
            return true;
    }
    return false;
}

const PHASES = {
    begin: Clutter.TouchpadGesturePhase.BEGIN,
    update: Clutter.TouchpadGesturePhase.UPDATE,
    end: Clutter.TouchpadGesturePhase.END,
    cancel: Clutter.TouchpadGesturePhase.CANCEL,
    unknown: 99,
};

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
        add_action_full(name, phase, action) {
            if (!(action instanceof Clutter.Action))
                throw new Error('not an action');
            actions.push({name, phase, action});
        },
        remove_action(action) {
            const at = actions.findIndex(entry => entry.action === action);
            if (at < 0)
                throw new Error('no such action to remove');
            actions.splice(at, 1);
        },
        // A handler on the stage that stops an event while a button is held
        // makes the shell cancel every gesture under way, a window being
        // dragged in the overview for one. Nothing is to be connected here.
        connect(signal) {
            throw new Error(`a handler for ${signal} was connected to the stage`);
        },
    }),
    window_group: windowGroup,
    top_window_group: topWindowGroup,
    get_pointer: () => [640, 360, 0],
};

// Watches on the bus that are still set, so that one left behind shows.
let watches = 0;
const watchName = Gio.bus_watch_name_on_connection;
const unwatchName = Gio.bus_unwatch_name;
Gio.bus_watch_name_on_connection = (...args) => {
    watches++;
    return watchName(...args);
};
Gio.bus_unwatch_name = id => {
    watches--;
    return unwatchName(id);
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
    <method name="Swipe">
      <arg type="s" direction="in"/><arg type="u" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="Pinch"><arg type="b" direction="out"/></method>
    <method name="Broken"><arg type="b" direction="out"/></method>
    <method name="Handlers"><arg type="u" direction="out"/></method>
    <method name="Watches"><arg type="u" direction="out"/></method>
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
    Swipe(phase, fingers) {
        return through({
            type: () => Clutter.EventType.TOUCHPAD_SWIPE,
            get_gesture_phase: () => PHASES[phase],
            get_touchpad_gesture_finger_count: () => fingers,
        });
    },
    Pinch() {
        return through({
            type: () => Clutter.EventType.TOUCHPAD_PINCH,
            get_gesture_phase: () => Clutter.TouchpadGesturePhase.BEGIN,
            get_touchpad_gesture_finger_count: () => 3,
        });
    },
    // An event the shell has changed under the extension's feet.
    Broken() {
        return through({
            type: () => Clutter.EventType.TOUCHPAD_SWIPE,
            get_gesture_phase() {
                throw new Error('no such call any more');
            },
            get_touchpad_gesture_finger_count: () => 3,
        });
    },
    Handlers() {
        return actions.length;
    },
    Watches() {
        return watches;
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
