// Tests for the part of the shell extension that needs no shell.
// Run with: gjs -m tests/js/test_extension.js
import System from 'system';

import {
    BASE_DISTANCE, WATCHDOG_MS, WorkspaceSwipe, isOverWindow,
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

class FakeTracker {
    constructor({confirms = true} = {}) {
        this.calls = [];
        this._state = 0;
        this._confirms = confirms;
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

function swipeWith(tracker, timers = new FakeTimers()) {
    const swipe = new WorkspaceSwipe({
        tracker: () => tracker,
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
    timers.fire();
    const before = timers.cancelled.length;
    swipe.cancel();
    swipe.begin(2000);
    same(timers.cancelled.length, before, 'cancellations');
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
    swipe.end(1020);
    same(tracker.calls, [['begin', 1000, 640, 360]]);
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
    same(isOverWindow(surface, groups, false), true);
});

test('a window kept above the others counts too', () => {
    const surface = actor('surface', actor('window', topWindowGroup));
    same(isOverWindow(surface, groups, false), true);
});

test('the desktop background counts as a window', () => {
    same(isOverWindow(actor('background', actor('backgrounds', windowGroup)), groups, false), true);
});

test('the top bar and the dock do not', () => {
    const button = actor('button', actor('panel', actor('panelBox', uiGroup)));
    same(isOverWindow(button, groups, false), false);
    same(isOverWindow(actor('dash', actor('dock', uiGroup)), groups, false), false);
});

test('nothing under the pointer is not a window', () => {
    same(isOverWindow(null, groups, false), false);
    same(isOverWindow(undefined, groups, false), false);
});

test('nothing is a window while the overview is open', () => {
    const surface = actor('surface', actor('window', windowGroup));
    same(isOverWindow(surface, groups, true), false);
});

test('an endless chain of parents does not hang the shell', () => {
    const loop = {name: 'loop'};
    loop.get_parent = () => loop;
    same(isOverWindow(loop, groups, false), false);
});

print(`${run} tests, ${failed} failed`);
System.exit(failed ? 1 : 0);
