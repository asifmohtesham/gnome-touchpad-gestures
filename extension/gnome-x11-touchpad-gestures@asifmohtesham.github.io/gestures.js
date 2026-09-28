// The part of the extension that needs no shell, so it can be tested
// outside one. extension.js hands it the pieces of the shell it works on.

// The shell measures a touchpad swipe against a distance of its own choosing
// and calls that distance one workspace. This is the figure its own touchpad
// gesture uses for a sideways swipe (TOUCHPAD_BASE_WIDTH in swipeTracker.js).
export const BASE_DISTANCE = 400;

// A swipe that hears nothing for this long is put back. The daemon sends
// word at every move of the fingers, and a few times a second while they
// are held still, so silence means it has gone.
export const WATCHDOG_MS = 1000;

// How far up from an actor to look for a window group before giving up.
const DEEPEST = 64;

// swipeTracker.js keeps this to itself: State.SCROLLING.
const SCROLLING = 1;

// Feeds a swipe made on the touchpad into the shell's workspace animation,
// through the same three calls the shell's own touchpad gesture makes. They
// are the shell's private methods and may change between versions, so every
// call is guarded: a shell that has changed gets no swipe, not a fault.
export class WorkspaceSwipe {
    constructor({tracker, mode, pointer, schedule, cancel}) {
        this._lookUpTracker = tracker;
        this._mode = mode;
        this._pointer = pointer;
        this._schedule = schedule;
        this._cancel = cancel;
        this._tracker = null;
        this._timer = null;
    }

    begin(time) {
        this.cancel();
        try {
            const tracker = this._lookUpTracker();
            // The shell's own gesture refuses a swipe while the tracker is
            // switched off, as it is while a switch of workspace made by
            // keyboard is animating, and where the tracker does not work,
            // as with a menu open. It makes those checks before it reaches
            // the tracker, so calling the tracker directly skips them. A
            // swipe begun regardless is dropped at its first move, or worse,
            // cuts the animation short and leaves the tracker switched off.
            if (!tracker.enabled)
                return false;
            if ((tracker._allowedModes & this._mode()) === 0)
                return false;
            const [x, y] = this._pointer();
            tracker._beginGesture(null, time, x, y);
            // The shell takes a swipe up by confirming it, which it may
            // decline to do, on a monitor without workspaces for one.
            if (tracker._state !== SCROLLING)
                return false;
            this._tracker = tracker;
        } catch (_error) {
            return false;
        }
        this._watch();
        return true;
    }

    update(time, fraction) {
        if (!this._tracker)
            return;
        // Anything on the session bus can call this. A share that is not a
        // number would poison the tracker's idea of where the workspace is.
        if (typeof fraction !== 'number' || !Number.isFinite(fraction))
            return;
        try {
            this._tracker._updateGesture(
                null, time, fraction * BASE_DISTANCE, BASE_DISTANCE);
        } catch (_error) {
            // Forgetting the swipe would leave the workspace half way
            // across with nothing left to move it.
            this.cancel();
            return;
        }
        this._watch();
    }

    end(time) {
        if (!this._tracker)
            return;
        try {
            this._tracker._endTouchpadGesture(null, time, BASE_DISTANCE);
        } catch (_error) {
            this.cancel();
            return;
        }
        this._forget();
    }

    cancel() {
        if (!this._tracker)
            return;
        const tracker = this._tracker;
        this._forget();
        try {
            // The shell may have ended the swipe itself in the meantime.
            if (tracker._state === SCROLLING)
                tracker._interrupt();
        } catch (_error) {
            // Nothing more can be done for it.
        }
    }

    destroy() {
        this.cancel();
    }

    _watch() {
        this._unwatch();
        this._timer = this._schedule(WATCHDOG_MS, () => {
            // It has fired, so there is nothing left to cancel.
            this._timer = null;
            this.cancel();
        });
    }

    _unwatch() {
        if (this._timer !== null)
            this._cancel(this._timer);
        this._timer = null;
    }

    _forget() {
        this._unwatch();
        this._tracker = null;
    }
}

// Whether what the pointer is on belongs to a window, as opposed to
// something the shell itself has drawn: the top bar, the dock, a menu.
// There a turn of the wheel usually steps through something, and momentum
// would race through it.
//
// `actor` is what was picked among the things that react to the pointer.
// The stage itself coming back means nothing of the shell's is there. That
// matters because the shell lays some things over the whole monitor without
// taking the pointer, the space around a notification for one.
export function isOverWindow(actor, windowGroups, overviewVisible, stage) {
    if (overviewVisible)
        return false;
    if (actor && actor === stage)
        return true;
    let depth = 0;
    for (let at = actor; at && depth < DEEPEST; at = at.get_parent(), depth++) {
        if (windowGroups.includes(at))
            return true;
    }
    return false;
}
