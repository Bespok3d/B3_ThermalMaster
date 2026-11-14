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

`files/vendor/p3_camera.py` is the USB protocol driver, verbatim from
[jvdillon/p3-ir-camera](https://github.com/jvdillon/p3-ir-camera) at pinned commit
`e3205dca5727682ff2d903585d1dce5a1d19f1f6`, Apache-2.0, whose licence ships beside it as
`LICENSE.p3-ir-camera`. The repository's `VENDORING.md` records the pin and how to verify it.
Protocol reverse-engineering credit belongs to @aeternium.

**What is used from it:** the camera models with their USB identifiers and geometry, opening
and initialising the device, the commands and their acknowledgements, starting and stopping the
stream, the gain, the size of a frame and where the temperatures sit in it, the conversion of
raw values to temperatures with the emissivity correction, and its frame reader as the fallback.

**What this plugin implements itself, beside the driver and without changing it:** reading a
frame, in two bulk reads through the device the driver opened, in
`files/lib/thermal_master/frame_reader.py`; firing the calibration shutter, with the driver's own
command instead of through its `trigger_shutter`; and on top of both, the colour mapping, the
ranging, the temperature readout, the viewer, the timelapse and the MJPEG serving, in
`files/lib/thermal_master/`.

numpy, Pillow and pyusb are declared in `requirements.txt` and ship as wheels built for the printer's
architecture, installed into a virtual environment belonging to this plugin alone. They are separate
works under their own licences, aggregated with this plugin's code rather than relicensed by it.
