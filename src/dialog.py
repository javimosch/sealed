# sealed's desktop window (GTK 3). Embedded in the binary at build time.
# argv: title heading body confirm(0|1)
# stdout: what was typed. exit 0 entered, 1 cancelled/timed out, 3 no GTK or
# display, 4 multi-line input.
import ctypes, sys

try:
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE=0
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk, Pango
    if not Gtk.init_check([])[0]:
        raise RuntimeError("no display")
except Exception:
    sys.exit(3)


def build(title, heading, body, confirm):
    d = Gtk.Dialog(title=title)
    d.set_default_size(560, -1)
    d.set_keep_above(True)
    d.set_position(Gtk.WindowPosition.CENTER)
    d.add_button("Cancel", Gtk.ResponseType.CANCEL)
    ok = d.add_button("OK", Gtk.ResponseType.OK)
    ok.get_style_context().add_class("suggested-action")
    d.set_default_response(Gtk.ResponseType.OK)
    box = d.get_content_area()
    box.set_spacing(8)
    box.set_border_width(14)

    def label(text, bold=False):
        w = Gtk.Label(label=text, xalign=0)  # plain text, never markup
        w.set_line_wrap(True)
        w.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        w.set_max_width_chars(64)
        if bold:
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
            attrs.insert(Pango.attr_scale_new(1.15))
            w.set_attributes(attrs)
        box.pack_start(w, False, False, 0)
        return w

    def field(placeholder):
        e = Gtk.Entry()
        e.set_visibility(False)
        e.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        e.set_placeholder_text(placeholder)
        e.set_activates_default(True)
        box.pack_start(e, False, False, 0)
        return e

    label(heading, bold=True)
    label(body)
    first = field("type or paste here")
    again = field("repeat to confirm") if confirm else None
    show = Gtk.CheckButton(label="Show what I type")  # unchecked: hidden
    box.pack_start(show, False, False, 0)
    note = label("")

    def refresh(*_):
        visible = show.get_active()
        first.set_visibility(visible)
        ready = first.get_text() != ""
        message = ""
        if again is not None:
            again.set_visibility(visible)
            again.set_visible(not visible)  # a value you can see needs no repeat
            if not visible:
                if again.get_text() and again.get_text() != first.get_text():
                    message = "The two entries do not match."
                ready = ready and again.get_text() == first.get_text()
        note.set_text(message)
        ok.set_sensitive(ready)

    first.connect("changed", refresh)
    if again is not None:
        again.connect("changed", refresh)
    show.connect("toggled", refresh)
    d.show_all()
    refresh()
    return d, first, again, show, ok, note


def main(argv):
    d, first, again, show, ok, note = build(argv[0], argv[1], argv[2], argv[3] == "1")
    GLib.timeout_add_seconds(180, lambda: d.response(Gtk.ResponseType.CANCEL))
    d.present()
    while True:
        if d.run() != Gtk.ResponseType.OK:
            d.destroy()
            return 1
        if ok.get_sensitive():  # Enter must not bypass the disabled button
            break
    value = first.get_text()
    d.destroy()
    if "\n" in value or "\r" in value:
        return 4
    sys.stdout.buffer.write(value.encode())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
