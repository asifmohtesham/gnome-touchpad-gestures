// Stands in for gi://Meta. The runner says which kind of session it is.
let wayland = false;

export function setWayland(is) {
    wayland = is;
}

export default {is_wayland_compositor: () => wayland};
