// Stands in for the shell's resource:///org/gnome/shell/ui/main.js, with a
// tracker that records what it is asked to do.
export const calls = [];

export const tracker = {
    enabled: true,
    _allowedModes: 1 | 2,
    _state: 0,
    _beginGesture(gesture, time, x, y) {
        calls.push(['begin', time, x, y]);
        this._state = 1;
    },
    _updateGesture(gesture, time, delta, distance) {
        calls.push(['update', time, delta, distance]);
    },
    _endTouchpadGesture(gesture, time, distance) {
        calls.push(['end', time, distance]);
        this._state = 0;
    },
    _interrupt() {
        calls.push(['interrupt']);
        this._state = 0;
    },
};

export const wm = {_workspaceAnimation: {_swipeTracker: tracker}};
export const overview = {visible: false};
export let actionMode = 1;

export function setActionMode(mode) {
    actionMode = mode;
}
