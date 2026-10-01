// Tests for the part of the shell extension that needs no shell.
// Run with: gjs -m tests/js/test_extension.js
import System from 'system';

import {
    BASE_DISTANCE, DRAG_FINGERS, SwipeFilter, WATCHDOG_MS, WorkspaceSwipe,
    freesThreeFingersOn, isOverWindow, isWaylandShell,
} from '../../extension/gnome-x11-touchpad-gestures@asifmohtesham.github.io/gestures.js';

let run = 0;
let failed = 0;

function test(name, body) {
    run++;
    try {
        body();
    } catch (error) {
        failed++;
        print(`FAIL: ${name}\n    ${error.message}`);
    }
}

function same(actual, expected, what = 'value') {
    const a = JSON.stringify(actual);
    const b = JSON.stringify(expected);
    if (a !== b)
        throw new Error(`${what}: expected ${b}, got ${a}`);
}

// Shell.ActionMode, as far as it matters here.
const NORMAL = 1;
const OVERVIEW_MODE = 2;
const POPUP = 128;

class FakeTracker {
    constructor({confirms = true, enabled = true, allowedModes = NORMAL | OVERVIEW_MODE} = {}) {
        this.calls = [];
        this._state = 0;
        this._confirms = confirms;
        this.enabled = enabled;
        this._allowedModes = allowedModes;
    }

    _beginGesture(gesture, time, x, y) {
        this.calls.push(['begin', time, x, y]);
        if (this._confirms)
            this._state = 1;
    }

    _updateGesture(gesture, time, delta, distance) {
        this.calls.push(['update', time, delta, distance]);
    }

    _endTouchpadGesture(gesture, time, distance) {
        this.calls.push(['end', time, distance]);
        this._state = 0;
    }

    _interrupt() {
        this.calls.push(['interrupt']);
        this._state = 0;
    }
}

class FakeTimers {
    constructor() {
        this.armed = new Map();
        this.next = 1;
        this.cancelled = [];
    }

    schedule(ms, fire) {
        const id = this.next++;
        this.armed.set(id, {ms, fire});
        return id;
    }

    cancel(id) {
        this.cancelled.push(id);
        this.armed.delete(id);
    }

    fire() {
        const [[id, timer]] = [...this.armed];
        this.armed.delete(id);
        timer.fire();
    }
}

function swipeWith(tracker, timers = new FakeTimers(), mode = () => NORMAL) {
    const swipe = new WorkspaceSwipe({
        tracker: () => tracker,
        mode,
        pointer: () => [640, 360],
        schedule: (ms, fire) => timers.schedule(ms, fire),
        cancel: id => timers.cancel(id),
    });
    return {swipe, timers};
}

test('begin hands the shell the time and where the pointer is', () => {
    const tracker = new FakeTracker();
    const {swipe} = swipeWith(tracker);
    same(swipe.begin(1000), true, 'begin');
    same(tracker.calls, [['begin', 1000, 640, 360]]);
});

test('begin says no when the shell does not take the swipe up', () => {
    const tracker = new FakeTracker({confirms: false});
    const {swipe, timers} = swipeWith(tracker);
    same(swipe.begin(1000), false, 'begin');
    swipe.update(1010, 0.1);
    swipe.end(1020);
    same(tracker.calls, [['begin', 1000, 640, 360]]);
    same(timers.armed.size, 0, 'timers armed');
});

test('update turns a share of a workspace into the shell\'s own units', () => {
    const tracker = new FakeTracker();
    const {swipe} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.update(1010, 0.25);
    swipe.update(1020, -0.5);
    same(tracker.calls.slice(1), [
        ['update', 1010, 0.25 * BASE_DISTANCE, BASE_DISTANCE],
        ['update', 1020, -0.5 * BASE_DISTANCE, BASE_DISTANCE],
    ]);
});

test('update and end before any begin do nothing', () => {
    const tracker = new FakeTracker();
    const {swipe} = swipeWith(tracker);
    swipe.update(1010, 0.25);
    swipe.end(1020);
    swipe.cancel();
    same(tracker.calls, []);
});

test('end lets the shell decide, and the swipe is then over', () => {
    const tracker = new FakeTracker();
    const {swipe} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.end(1200);
    swipe.update(1210, 0.3);
    swipe.end(1220);
    same(tracker.calls.slice(1), [['end', 1200, BASE_DISTANCE]]);
});

test('cancel puts the workspace back', () => {
    const tracker = new FakeTracker();
    const {swipe} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.cancel();
    same(tracker.calls.slice(1), [['interrupt']]);
    swipe.update(1210, 0.3);
    same(tracker.calls.length, 2, 'calls');
});

test('a second begin puts the first swipe back before it starts', () => {
    const tracker = new FakeTracker();
    const {swipe} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.begin(2000);
    same(tracker.calls, [
        ['begin', 1000, 640, 360], ['interrupt'], ['begin', 2000, 640, 360],
    ]);
});

test('a swipe left without word for a second is put back', () => {
    const tracker = new FakeTracker();
    const {swipe, timers} = swipeWith(tracker);
    swipe.begin(1000);
    same([...timers.armed.values()].map(t => t.ms), [WATCHDOG_MS], 'armed for');
    timers.fire();
    same(tracker.calls.slice(1), [['interrupt']]);
    swipe.update(1500, 0.2);
    same(tracker.calls.length, 2, 'calls after the watchdog');
});

test('every update gives the swipe another second', () => {
    const tracker = new FakeTracker();
    const {swipe, timers} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.update(1010, 0.1);
    swipe.update(1020, 0.1);
    same(timers.armed.size, 1, 'timers armed');
    same(timers.cancelled.length, 2, 'timers replaced');
});

test('no timer is left behind when a swipe ends or is cancelled', () => {
    const tracker = new FakeTracker();
    const {swipe, timers} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.end(1100);
    same(timers.armed.size, 0, 'after end');
    swipe.begin(2000);
    swipe.cancel();
    same(timers.armed.size, 0, 'after cancel');
});

test('a timer that has fired is not cancelled a second time', () => {
    const tracker = new FakeTracker();
    const {swipe, timers} = swipeWith(tracker);
    swipe.begin(1000);
    const before = timers.cancelled.length;
    timers.fire();
    swipe.cancel();
    swipe.begin(2000);
    same(timers.cancelled.length, before, 'cancellations');
});

// What the shell's own gesture would refuse, this must refuse too. Its
// checks are made before the tracker is reached, so they have to be made
// again here.

test('a swipe is declined while the tracker is switched off', () => {
    // As it is while a switch of workspace made by keyboard is animating.
    const tracker = new FakeTracker({enabled: false});
    const {swipe, timers} = swipeWith(tracker);
    same(swipe.begin(1000), false, 'begin');
    same(tracker.calls, [], 'calls to the tracker');
    same(timers.armed.size, 0, 'timers armed');
});

test('a swipe is declined where the tracker does not work', () => {
    const tracker = new FakeTracker({allowedModes: NORMAL});
    same(swipeWith(tracker, new FakeTimers(), () => OVERVIEW_MODE).swipe.begin(1000), false, 'in the overview');
    same(swipeWith(tracker, new FakeTimers(), () => POPUP).swipe.begin(1000), false, 'with a menu open');
    same(tracker.calls, [], 'calls to the tracker');
});

test('a swipe is taken up where the tracker does work', () => {
    const tracker = new FakeTracker({allowedModes: NORMAL | OVERVIEW_MODE});
    same(swipeWith(tracker, new FakeTimers(), () => OVERVIEW_MODE).swipe.begin(1000), true, 'begin');
});

test('a shell that cannot say what mode it is in gets no swipe', () => {
    const tracker = new FakeTracker();
    const broken = () => {
        throw new Error('changed in a later version');
    };
    same(swipeWith(tracker, new FakeTimers(), broken).swipe.begin(1000), false, 'begin');
    same(tracker.calls, [], 'calls to the tracker');
});

test('a tracker that fails part way has its swipe put back, not left hanging', () => {
    const tracker = new FakeTracker();
    tracker._updateGesture = () => {
        throw new Error('changed in a later version');
    };
    const {swipe} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.update(1010, 0.1);
    same(tracker.calls, [['begin', 1000, 640, 360], ['interrupt']]);
    same(tracker._state, 0, 'state of the tracker');
});

test('a tracker that fails at the end has its swipe put back', () => {
    const tracker = new FakeTracker();
    tracker._endTouchpadGesture = () => {
        throw new Error('changed in a later version');
    };
    const {swipe} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.end(1100);
    same(tracker.calls, [['begin', 1000, 640, 360], ['interrupt']]);
});

test('a share of a workspace that is not a number is not passed on', () => {
    const tracker = new FakeTracker();
    const {swipe} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.update(1010, NaN);
    swipe.update(1020, Infinity);
    swipe.update(1030, 'a quarter');
    swipe.update(1040, 0.25);
    same(tracker.calls.slice(1), [['update', 1040, 100, 400]]);
});

test('a move of nothing still tells the shell the fingers are there', () => {
    const tracker = new FakeTracker();
    const {swipe, timers} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.update(1300, 0);
    same(tracker.calls.slice(1), [['update', 1300, 0, 400]]);
    same(timers.cancelled.length, 1, 'watchdog put off');
});

test('the figures the shell is given are the shell\'s own', () => {
    same(BASE_DISTANCE, 400, 'distance for one workspace');
    same(WATCHDOG_MS, 1000, 'watchdog');
});

test('a swipe the shell ended by itself is not interrupted afterwards', () => {
    const tracker = new FakeTracker();
    const {swipe, timers} = swipeWith(tracker);
    swipe.begin(1000);
    tracker._state = 0;
    timers.fire();
    same(tracker.calls, [['begin', 1000, 640, 360]]);
});

test('the tracker is looked up afresh at every swipe', () => {
    const first = new FakeTracker();
    const second = new FakeTracker();
    let current = first;
    const timers = new FakeTimers();
    const swipe = new WorkspaceSwipe({
        tracker: () => current,
        mode: () => NORMAL,
        pointer: () => [1, 2],
        schedule: (ms, fire) => timers.schedule(ms, fire),
        cancel: id => timers.cancel(id),
    });
    swipe.begin(1000);
    swipe.end(1100);
    current = second;
    swipe.begin(2000);
    same(second.calls, [['begin', 2000, 1, 2]]);
});

test('a shell whose insides have changed gets no for an answer, not a crash', () => {
    const {swipe, timers} = swipeWith({});
    same(swipe.begin(1000), false, 'begin');
    swipe.update(1010, 0.1);
    swipe.end(1020);
    swipe.cancel();
    same(timers.armed.size, 0, 'timers armed');
});

test('a shell with no tracker at all gets no for an answer', () => {
    const {swipe} = swipeWith(undefined);
    same(swipe.begin(1000), false, 'begin');
});

test('a tracker that fails part way through ends the swipe', () => {
    const tracker = new FakeTracker();
    tracker._updateGesture = () => {
        throw new Error('changed in a later version');
    };
    const {swipe, timers} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.update(1010, 0.1);
    same(timers.armed.size, 0, 'timers armed');
    const calls = tracker.calls.length;
    swipe.end(1020);
    same(tracker.calls.length, calls, 'calls after the failure');
});

test('destroy puts a swipe in progress back', () => {
    const tracker = new FakeTracker();
    const {swipe, timers} = swipeWith(tracker);
    swipe.begin(1000);
    swipe.destroy();
    same(tracker.calls.slice(1), [['interrupt']]);
    same(timers.armed.size, 0, 'timers armed');
});

// What is under the pointer.

function actor(name, parent = null) {
    return {name, get_parent: () => parent};
}

const stage = actor('stage');
const uiGroup = actor('uiGroup', stage);
const windowGroup = actor('window_group', uiGroup);
const topWindowGroup = actor('top_window_group', uiGroup);
const groups = [windowGroup, topWindowGroup];

test('a window is over the window group', () => {
    const surface = actor('surface', actor('window', windowGroup));
    same(isOverWindow(surface, groups, false, stage), true);
});

test('where nothing of the shell\'s reacts to the pointer, it is not in the way', () => {
    // The pick is made among what reacts to the pointer. The stage itself
    // coming back means nothing of the shell's is there, only something it
    // drew over the top without taking the pointer, such as the space
    // around a notification.
    same(isOverWindow(stage, groups, false, stage), true);
});

test('the stage counts for nothing while the overview is open', () => {
    same(isOverWindow(stage, groups, true, stage), false);
});

test('a window kept above the others counts too', () => {
    const surface = actor('surface', actor('window', topWindowGroup));
    same(isOverWindow(surface, groups, false, stage), true);
});

test('the desktop background counts as a window', () => {
    same(isOverWindow(actor('background', actor('backgrounds', windowGroup)), groups, false, stage), true);
});

test('the top bar and the dock do not', () => {
    const button = actor('button', actor('panel', actor('panelBox', uiGroup)));
    same(isOverWindow(button, groups, false, stage), false);
    same(isOverWindow(actor('dash', actor('dock', uiGroup)), groups, false, stage), false);
});

test('nothing under the pointer is not a window', () => {
    same(isOverWindow(null, groups, false, stage), false);
    same(isOverWindow(undefined, groups, false, stage), false);
});

test('nothing is a window while the overview is open', () => {
    const surface = actor('surface', actor('window', windowGroup));
    same(isOverWindow(surface, groups, true, stage), false);
});

test('an endless chain of parents does not hang the shell', () => {
    const loop = {name: 'loop'};
    loop.get_parent = () => loop;
    same(isOverWindow(loop, groups, false, stage), false);
});

// Which swipes the shell is kept from seeing, where the daemon drags.

function whole(filter, fingers, daemonPresent) {
    return ['begin', 'update', 'update', 'end'].map(
        phase => filter.handle(phase, fingers, daemonPresent));
}

test('three fingers are the ones that drag', () => {
    same(DRAG_FINGERS, 3);
});

test('a swipe of three fingers is swallowed whole while the daemon is there', () => {
    same(whole(new SwipeFilter(), 3, true), [true, true, true, true]);
});

test('it is left alone while the daemon is not', () => {
    same(whole(new SwipeFilter(), 3, false), [false, false, false, false]);
});

test('a swipe of four fingers, or two, or five, is never swallowed', () => {
    for (const fingers of [2, 4, 5])
        same(whole(new SwipeFilter(), fingers, true), [false, false, false, false]);
});

test('a daemon that goes in the middle of a swipe does not leave half of one', () => {
    const filter = new SwipeFilter();
    same(filter.handle('begin', 3, true), true);
    same(filter.handle('update', 3, false), true);
    same(filter.handle('end', 3, false), true);
});

test('a daemon that comes in the middle of a swipe does not cut it short', () => {
    const filter = new SwipeFilter();
    same(filter.handle('begin', 3, false), false);
    same(filter.handle('update', 3, true), false);
    same(filter.handle('end', 3, true), false);
});

test('each swipe is decided afresh at its begin', () => {
    const filter = new SwipeFilter();
    whole(filter, 3, true);
    same(whole(filter, 4, true), [false, false, false, false]);
    same(whole(filter, 3, true), [true, true, true, true]);
    same(whole(filter, 3, false), [false, false, false, false]);
});

test('nothing is swallowed once a swipe has ended', () => {
    const filter = new SwipeFilter();
    whole(filter, 3, true);
    same(filter.handle('update', 3, true), false);
    same(filter.handle('end', 3, true), false);
});

test('a swipe whose begin was never seen is not swallowed', () => {
    const filter = new SwipeFilter();
    same(filter.handle('update', 3, true), false);
    same(filter.handle('end', 3, true), false);
});

test('a begin on top of a swipe that never ended starts over', () => {
    const filter = new SwipeFilter();
    filter.handle('begin', 3, true);
    same(filter.handle('begin', 4, true), false);
    same(filter.handle('update', 4, true), false);
});

test('a phase the filter does not know changes nothing and is never a begin', () => {
    const filter = new SwipeFilter();
    same(filter.handle(undefined, 3, true), false);
    same(filter.handle('begin', 3, true), true);
    same(filter.handle(undefined, 3, true), true);
    same(filter.handle('later', 3, true), true);
    same(filter.handle('end', 3, true), true);
});

test('only a plain yes counts as the daemon being there', () => {
    for (const present of [undefined, null, 1, 'yes'])
        same(new SwipeFilter().handle('begin', 3, present), false);
});

test('a count of fingers that is not three in every way is not three', () => {
    for (const fingers of ['3', 3.5, undefined, null, NaN])
        same(new SwipeFilter().handle('begin', fingers, true), false);
});

// Which shells the swallowing has been seen to work in.

test('three fingers are freed on the shell it was seen to work in', () => {
    for (const version of ['50.1', '50', '50.0', '50.beta', '50.rc'])
        same(freesThreeFingersOn(version), true, version);
});

test('they are not freed on a shell it was never tried in', () => {
    // An older shell handles a swipe before an extension can see it, so
    // the swallow would come too late and land on a held drag.
    for (const version of ['46.0', '46', '47.2', '49.9', '51.0', '5', '500'])
        same(freesThreeFingersOn(version), false, version);
});

test('a version that cannot be read frees nothing', () => {
    for (const version of ['', 'unknown', undefined, null, 50, {}])
        same(freesThreeFingersOn(version), false, String(version));
});

// Which kind of session the shell runs.

test('a shell that can say which session it runs is taken at its word', () => {
    same(isWaylandShell({is_wayland_compositor: () => true}), true);
    same(isWaylandShell({is_wayland_compositor: () => false}), false);
});

test('a shell that can no longer be asked has no X11 session to run', () => {
    // GNOME 50 dropped the X11 session and the call with it.
    same(isWaylandShell({}), true);
    same(isWaylandShell({is_wayland_compositor: undefined}), true);
});

print(`${run} tests, ${failed} failed`);
System.exit(failed ? 1 : 0);
