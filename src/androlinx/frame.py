"""The Pixel 9 device frame, and the live preview drawn inside it.

Drawn rather than shipped as an image: the proportions are all expressed as
fractions of the widget's width, so the frame is crisp at any size and on any
scale factor, it follows the light and dark themes, and there is no SVG loader
in the dependency list.

What it is *not* is the mirror. scrcpy renders into its own window and cannot be
embedded -- GTK4 has no XEmbed and Wayland does not permit reparenting another
application's surface. The preview here is a periodic screenshot, which is
honest about being a preview: it is what the phone looks like right now, so you
can see which device you are about to mirror.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")

from gi.repository import Adw, Gdk, GObject, Graphene, Gsk, Gtk  # noqa: E402

#: Pixel 9's display is 20:9. The body is that plus a bezel all round.
SCREEN_ASPECT = 20 / 9
#: Bezel width, as a fraction of the body's shorter edge.
BEZEL = 0.035
#: Corner radius of the body, as a fraction of its shorter edge.
RADIUS = 0.105
#: Punch-hole camera diameter, as a fraction of the screen's shorter edge.
CAMERA = 0.052

#: Body aspect ratio, derived so the screen inside comes out at SCREEN_ASPECT.
BODY_ASPECT = (1 - 2 * BEZEL) * SCREEN_ASPECT + 2 * BEZEL

_DARK_BODY = "#17181a"
_DARK_RAIL = "#3f4247"
_LIGHT_BODY = "#dedbd6"
_LIGHT_RAIL = "#b4b0aa"


def _rgba(spec: str) -> Gdk.RGBA:
    colour = Gdk.RGBA()
    colour.parse(spec)
    return colour


class DeviceFrame(Gtk.Widget):
    """A phone-shaped frame with a screen you can draw into.

    Its single child is laid out inside the screen area, which is where the
    status messages and the spinner go when there is no picture to show.
    """

    __gtype_name__ = "AndroLinxDeviceFrame"

    __gsignals__ = {
        # Emitted when the screen is clicked, which starts mirroring.
        "activated": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, child: Gtk.Widget | None = None) -> None:
        super().__init__()
        self._texture: Gdk.Texture | None = None
        self._child = child
        self._dim = False
        if child is not None:
            child.set_parent(self)

        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.set_cursor_from_name("pointer"))
        motion.connect("leave", lambda *_: self.set_cursor_from_name(None))
        self.add_controller(motion)

    def do_dispose(self) -> None:
        if self._child is not None:
            self._child.unparent()
            self._child = None
        Gtk.Widget.do_dispose(self)

    # -- content ----------------------------------------------------------- #

    def set_texture(self, texture: Gdk.Texture | None) -> None:
        self._texture = texture
        self.queue_draw()

    def set_dim(self, dim: bool) -> None:
        """Fade the last frame, to show the preview is no longer updating."""
        if dim != self._dim:
            self._dim = dim
            self.queue_draw()

    @property
    def has_texture(self) -> bool:
        return self._texture is not None

    @property
    def landscape(self) -> bool:
        texture = self._texture
        return texture is not None and texture.get_width() > texture.get_height()

    def _on_click(self, gesture, n_press, x, y) -> None:
        self.emit("activated")

    # -- geometry ---------------------------------------------------------- #

    def _body(self, width: int, height: int) -> Graphene.Rect:
        """The phone's outline, centred and as large as will fit."""
        aspect = 1 / BODY_ASPECT if self.landscape else BODY_ASPECT
        if width * aspect <= height:
            w = float(width)
            h = w * aspect
        else:
            h = float(height)
            w = h / aspect
        return Graphene.Rect().init((width - w) / 2, (height - h) / 2, w, h)

    def _screen(self, body: Graphene.Rect) -> Graphene.Rect:
        bezel = min(body.get_width(), body.get_height()) * BEZEL
        return Graphene.Rect().init(
            body.get_x() + bezel,
            body.get_y() + bezel,
            body.get_width() - 2 * bezel,
            body.get_height() - 2 * bezel,
        )

    # -- layout ------------------------------------------------------------ #

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (180, 300, -1, -1)
        # Height follows from width, so the frame keeps its proportions rather
        # than stretching to fill whatever box it is put in.
        width = for_size if for_size > 0 else 300
        natural = int(width * (1 / BODY_ASPECT if self.landscape else BODY_ASPECT))
        return (int(160 * BODY_ASPECT), natural, -1, -1)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        if self._child is None:
            return
        screen = self._screen(self._body(width, height))
        self._child.allocate(
            int(screen.get_width()),
            int(screen.get_height()),
            baseline,
            Gsk.Transform().translate(
                Graphene.Point().init(screen.get_x(), screen.get_y())
            ),
        )

    # -- drawing ----------------------------------------------------------- #

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return

        dark = Adw.StyleManager.get_default().get_dark()
        body_colour = _rgba(_DARK_BODY if dark else _LIGHT_BODY)
        rail_colour = _rgba(_DARK_RAIL if dark else _LIGHT_RAIL)
        black = _rgba("#000000")

        body = self._body(width, height)
        short = min(body.get_width(), body.get_height())
        radius = short * RADIUS

        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(body, radius)

        # Body, then the aluminium rail as a hairline border on top of it.
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(body_colour, body)
        snapshot.pop()
        snapshot.append_border(rounded, [1.0] * 4, [rail_colour] * 4)

        # Screen.
        screen = self._screen(body)
        screen_rounded = Gsk.RoundedRect()
        screen_rounded.init_from_rect(screen, max(radius - short * BEZEL, 0.0))
        snapshot.push_rounded_clip(screen_rounded)
        snapshot.append_color(black, screen)
        if self._texture is not None:
            if self._dim:
                snapshot.push_opacity(0.35)
            snapshot.append_texture(self._texture, self._fit(screen))
            if self._dim:
                snapshot.pop()
        snapshot.pop()

        self._draw_camera(snapshot, screen, black)
        self._draw_buttons(snapshot, body, rail_colour)

        if self._child is not None:
            self.snapshot_child(self._child, snapshot)

    def _fit(self, screen: Graphene.Rect) -> Graphene.Rect:
        """Place the texture inside the screen without distorting it.

        Phones are not all 20:9, and tablets are nothing like it, so the picture
        is letterboxed against the black screen rather than stretched to fill a
        Pixel-shaped hole.
        """
        texture = self._texture
        assert texture is not None
        tw, th = texture.get_width(), texture.get_height()
        if tw <= 0 or th <= 0:
            return screen
        scale = min(screen.get_width() / tw, screen.get_height() / th)
        w, h = tw * scale, th * scale
        return Graphene.Rect().init(
            screen.get_x() + (screen.get_width() - w) / 2,
            screen.get_y() + (screen.get_height() - h) / 2,
            w,
            h,
        )

    def _draw_camera(self, snapshot, screen: Graphene.Rect, black: Gdk.RGBA) -> None:
        """The centred punch-hole, on whichever edge is currently the top."""
        short = min(screen.get_width(), screen.get_height())
        size = short * CAMERA
        inset = short * 0.022
        if self.landscape:
            x = screen.get_x() + inset
            y = screen.get_y() + (screen.get_height() - size) / 2
        else:
            x = screen.get_x() + (screen.get_width() - size) / 2
            y = screen.get_y() + inset
        rect = Graphene.Rect().init(x, y, size, size)
        hole = Gsk.RoundedRect()
        hole.init_from_rect(rect, size / 2)
        snapshot.push_rounded_clip(hole)
        snapshot.append_color(black, rect)
        snapshot.pop()

    def _draw_buttons(self, snapshot, body: Graphene.Rect, rail: Gdk.RGBA) -> None:
        """Power button and volume rocker, on the right edge as on the real phone.

        They sit half in and half out of the body outline, which is how they
        look from the front: a sliver of the rail standing proud of the glass.
        """
        short = min(body.get_width(), body.get_height())
        thickness = max(short * 0.018, 2.5)
        #: (start, length) as fractions of the edge: power button, then the
        #: longer volume rocker below it.
        spans = ((0.19, 0.085), (0.30, 0.145))
        for start, length in spans:
            if self.landscape:
                rect = Graphene.Rect().init(
                    body.get_x() + body.get_width() * (1 - start - length),
                    body.get_y() - thickness / 2,
                    body.get_width() * length,
                    thickness,
                )
            else:
                rect = Graphene.Rect().init(
                    body.get_x() + body.get_width() - thickness / 2,
                    body.get_y() + body.get_height() * start,
                    thickness,
                    body.get_height() * length,
                )
            self._rail(snapshot, rect, thickness, rail)

    @staticmethod
    def _rail(snapshot, rect: Graphene.Rect, thickness: float, colour: Gdk.RGBA) -> None:
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, thickness / 2)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(colour, rect)
        snapshot.pop()
