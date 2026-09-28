// Answers the gnome-x11-touchpad-gestures daemon over D-Bus. Everything that
// can be worked out without the shell is in gestures.js; this file only
// hands it the pieces of the shell.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {WorkspaceSwipe, isOverWindow} from './gestures.js';

const OBJECT_PATH = '/io/github/asifmohtesham/Gestures';

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
  </interface>
</node>`;

export default class GesturesExtension extends Extension {
    enable() {
        this._swipe = new WorkspaceSwipe({
            tracker: () => Main.wm._workspaceAnimation._swipeTracker,
            pointer: () => global.get_pointer(),
            schedule: (ms, fire) => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
                fire();
                return GLib.SOURCE_REMOVE;
            }),
            cancel: id => GLib.source_remove(id),
        });
        this._exported = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._exported.export(Gio.DBus.session, OBJECT_PATH);
    }

    disable() {
        this._exported?.unexport();
        this._exported = null;
        this._swipe?.destroy();
        this._swipe = null;
    }

    get Version() {
        return this.metadata['version-name'] ?? '';
    }

    PointerOverWindow() {
        const [x, y] = global.get_pointer();
        const actor = global.stage.get_actor_at_pos(Clutter.PickMode.ALL, x, y);
        return isOverWindow(
            actor, [global.window_group, global.top_window_group],
            Main.overview.visible);
    }

    SwipeBegin(time) {
        return this._swipe.begin(time);
    }

    SwipeUpdate(time, fraction) {
        this._swipe.update(time, fraction);
    }

    SwipeEnd(time) {
        this._swipe.end(time);
    }

    SwipeCancel() {
        this._swipe.cancel();
    }
}
