// Works with the gnome-x11-touchpad-gestures daemon. On X11 it answers the
// daemon over D-Bus; on Wayland it keeps the shell's own swipes off three
// fingers while the daemon is there. Everything that can be worked out
// without the shell is in gestures.js; this file only hands it the pieces
// of the shell.
//
// One rule holds here whatever else changes: nothing in this file presses
// a button or moves the pointer. That is the daemon's, whose pointer is an
// input device like any other and does not depend on how the shell hands
// events round.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Config from 'resource:///org/gnome/shell/misc/config.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {
    SwipeFilter, WorkspaceSwipe, freesThreeFingersOn, isOverWindow,
    isWaylandShell,
} from './gestures.js';

const OBJECT_PATH = '/io/github/asifmohtesham/Gestures';
// The name the daemon takes on the session bus where it only drags.
const DAEMON_NAME = 'io.github.asifmohtesham.Gestures.Daemon';

const INTERFACE = `
<node>
  <interface name="io.github.asifmohtesham.Gestures">
    <property name="Version" type="s" access="read"/>
    <method name="PointerOverWindow">
      <arg type="b" direction="out" name="over"/>
    </method>
    <method name="SwipeBegin">
      <arg type="u" direction="in" name="time"/>
      <arg type="b" direction="out" name="taken"/>
    </method>
    <method name="SwipeUpdate">
      <arg type="u" direction="in" name="time"/>
      <arg type="d" direction="in" name="fraction"/>
    </method>
    <method name="SwipeEnd">
      <arg type="u" direction="in" name="time"/>
    </method>
    <method name="SwipeCancel"/>
    <method name="ThreeFingersFree">
      <arg type="b" direction="out" name="free"/>
    </method>
  </interface>
</node>`;

// The filter's names for the phases of a swipe.
const PHASES = new Map([
    [Clutter.TouchpadGesturePhase.BEGIN, 'begin'],
    [Clutter.TouchpadGesturePhase.UPDATE, 'update'],
    [Clutter.TouchpadGesturePhase.END, 'end'],
    [Clutter.TouchpadGesturePhase.CANCEL, 'end'],
]);

export default class GesturesExtension extends Extension {
    enable() {
        this._wayland = isWaylandShell(Meta);
        this._daemonPresent = false;
        if (!this._wayland)
            this._followSwipes();
        else if (freesThreeFingersOn(Config.PACKAGE_VERSION))
            this._freeThreeFingers();
        this._exported = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._exported.export(Gio.DBus.session, OBJECT_PATH);
    }

    disable() {
        this._exported?.unexport();
        this._exported = null;
        this._swipe?.destroy();
        this._swipe = null;
        if (this._watch)
            Gio.bus_unwatch_name(this._watch);
        this._watch = null;
        if (this._captured)
            global.stage.disconnect(this._captured);
        this._captured = null;
        this._filter = null;
    }

    // On X11: the shell's own workspace animation, fed by the daemon.
    _followSwipes() {
        this._swipe = new WorkspaceSwipe({
            tracker: () => Main.wm._workspaceAnimation._swipeTracker,
            mode: () => Main.actionMode,
            pointer: () => global.get_pointer(),
            schedule: (ms, fire) => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
                fire();
                return GLib.SOURCE_REMOVE;
            }),
            cancel: id => GLib.source_remove(id),
        });
    }

    // On Wayland: the shell makes a swipe of three fingers or more, which
    // would land on top of the daemon's drag. Its swipes of exactly three
    // are kept from it while the daemon is there, and no longer.
    _freeThreeFingers() {
        this._filter = new SwipeFilter();
        this._watch = Gio.bus_watch_name_on_connection(
            Gio.DBus.session, DAEMON_NAME, Gio.BusNameWatcherFlags.NONE,
            () => {
                this._daemonPresent = true;
            },
            () => {
                this._daemonPresent = false;
            });
        // Captured events reach this before the shell's own swipe handling.
        this._captured = global.stage.connect(
            'captured-event::touchpad', (_actor, event) => this._onTouchpad(event));
    }

    _onTouchpad(event) {
        try {
            if (event.type() !== Clutter.EventType.TOUCHPAD_SWIPE)
                return Clutter.EVENT_PROPAGATE;
            const swallow = this._filter.handle(
                PHASES.get(event.get_gesture_phase()),
                event.get_touchpad_gesture_finger_count(),
                this._daemonPresent);
            return swallow ? Clutter.EVENT_STOP : Clutter.EVENT_PROPAGATE;
        } catch (_error) {
            // A shell that has changed keeps its swipes.
            return Clutter.EVENT_PROPAGATE;
        }
    }

    get Version() {
        return this.metadata['version-name'] ?? '';
    }

    PointerOverWindow() {
        if (this._wayland)
            return false;
        const [x, y] = global.get_pointer();
        const actor = global.stage.get_actor_at_pos(
            Clutter.PickMode.REACTIVE, x, y);
        return isOverWindow(
            actor, [global.window_group, global.top_window_group],
            Main.overview.visible, global.stage);
    }

    SwipeBegin(time) {
        return this._swipe ? this._swipe.begin(time) : false;
    }

    SwipeUpdate(time, fraction) {
        this._swipe?.update(time, fraction);
    }

    SwipeEnd(time) {
        this._swipe?.end(time);
    }

    SwipeCancel() {
        this._swipe?.cancel();
    }

    ThreeFingersFree() {
        return this._wayland === true && this._daemonPresent === true;
    }
}
