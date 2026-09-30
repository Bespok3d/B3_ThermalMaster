# Attributions, thermal-master

**Plugin author:** Mauker, vendoring the P1/P3 USB protocol driver from jvdillon/p3-ir-camera

libusb-driven capture and MJPEG streaming for the Thermal Master P1.

| Upstream project | Author | Licence | Needed at runtime | Code ships in this package |
| --- | --- | --- | --- | --- |
| p3-ir-camera | Joshua V. Dillon (@jvdillon) | Apache-2.0 | yes | yes |
| numpy | NumPy Developers | BSD-3-Clause | yes | yes, as a wheel |
| Pillow | Jeffrey A. Clark and contributors | MIT-CMU | yes | yes, as a wheel |
| pyusb | PyUSB contributors | BSD-3-Clause | yes | yes, as a wheel |
| Material Icons | Google | Apache-2.0 | yes | the "info" icon, inline in the settings page |

The USB protocol layer is not reimplemented here. `files/vendor/p3_camera.py` is verbatim from
[jvdillon/p3-ir-camera](https://github.com/jvdillon/p3-ir-camera) at pinned commit
`e3205dca5727682ff2d903585d1dce5a1d19f1f6`, Apache-2.0, whose licence ships beside it as
`LICENSE.p3-ir-camera`. The repository's `VENDORING.md` records the pin and how to verify it.
Protocol reverse-engineering credit belongs to @aeternium.

This plugin adds the colormap, the auto-ranging and the MJPEG serving layer on top, in
`files/bin/thermal-master-stream.py`.

numpy, Pillow and pyusb are declared in `requirements.txt` and ship as wheels built for the printer's
architecture, installed into a virtual environment belonging to this plugin alone. They are separate
works under their own licences, aggregated with this plugin's code rather than relicensed by it.
