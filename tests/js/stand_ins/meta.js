// Stands in for gi://Meta. The runner says which kind of session it is.
//
// A shell that still has an X11 session can say which it is running. One
// that has none, as from GNOME 50, no longer has the call at all, and the
// stand-in is true to that: asked there, it fails as the real one does.
const Meta = {};

export function setSession(wayland, canSay) {
    delete Meta.is_wayland_compositor;
    if (canSay)
        Meta.is_wayland_compositor = () => wayland;
}

export default Meta;
