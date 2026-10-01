// Stands in for gi://Clutter, which only the shell's own process can load.
// The figures are Clutter's own.
export default {
    PickMode: {NONE: 0, REACTIVE: 1, ALL: 2},
    EventType: {TOUCHPAD_SWIPE: 20, TOUCHPAD_PINCH: 21, TOUCHPAD_HOLD: 22},
    TouchpadGesturePhase: {BEGIN: 0, UPDATE: 1, END: 2, CANCEL: 3},
    EVENT_PROPAGATE: false,
    EVENT_STOP: true,
};
