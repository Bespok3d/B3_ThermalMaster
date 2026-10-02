# Changelog

## 0.29.0

- **The timelapse switches to wide range by itself when the nozzle is in view.** High
  sensitivity reads nothing above about 150 C, so a nozzle was a flat 150 C in every layer. While a
  print is recorded, the first frame with something past 145 C, or a temperature of your own,
  switches the camera to wide range for the rest of the print, and the gain you chose comes back
  when it ends. No frame is taken for five seconds after the switch, while the camera
  recalibrates. On by default; untick it in the Timelapse panel to keep the gain as it is.

## 0.28.6

- **Four colour scales**, under Range on the settings page: the stretch the picture has always
  used, a knee that squeezes everything hotter than the range into the top of the palette so a
  nozzle keeps its shape, and two logs that spread the whole scene with most of the colours at its
  cool end. They work with a followed range and a held one, live and in timelapse clips, and they
  change the picture, never the readings.
- **The ruler spans the colours.** Following the scene, it used to run from the coldest to the
  hottest thing in view, so a nozzle re-labelled its top every frame and most of it was one flat
  colour. It now runs over the temperatures the colours are spread over, as it already did with a
  held range, and a triangle at an end says the scene goes past it.
- **Clips have their own readout**: the ruler, the crosshair, the markers and the spots can be on in
  the camera tile and off in the clips, or the other way round, with a button to copy the live
  picture's. Two more boxes write the colour scale in the corner of each clip and in its name.
- **Make the clip again with the current colours.** A print that still has its temperatures, the
  two newest, can be drawn again with the palette, colour scale and readout chosen now, and goes
  back on the Timelapse page in place of the old copy.
- **No ten minute wait on a U1 print without the printer's own timelapse.** The plugin asks during
  the print whether the firmware is recording one, and only waits for its clip when it is.
- **The explanations are beside the options they explain**, behind an (i) you hover over or click,
  instead of in a long list at the foot of the settings page. The plugin's version is at the foot
  instead.
- **The settings page fits a phone.** The Timelapse panel no longer runs off the right of the
  screen, the (i)s stay beside their options, and a second tap on one closes it.

## 0.28.0

- **A thermal timelapse of every print.** Switch it on in the new Timelapse panel of the settings
  page, and the plugin takes one frame each time the layer changes and makes a clip once the print
  ends. The clips are listed at the bottom of the settings page, to play, download or delete. It
  needs the slicer to send layer numbers; the documentation has the two lines to add.
- **The colours are chosen once for the whole clip**: fixed once the print has started, the whole
  print's coldest to hottest, two temperatures of your own, or the same as the live picture.
- **It keeps the last 10 prints**, or the number you choose, and makes room sooner if the
  printer's disk runs short.
- **Nothing gathered is lost.** Cancelled prints, prints cut short by a power cut, and layers the
  camera missed all end up in the clip.
- **On the Timelapse page in Fluidd and Mainsail** as well, wherever Moonraker has one: on the U1
  beside the printer's own clip of the same print, and on mainline Klipper with
  `moonraker-timelapse` installed.
- **Moonraker logins are handled.** If Moonraker asks for a login, the panel says so and takes
  Moonraker's API key, which is never shown again.

## 0.27.2

- **The documentation has pictures.** A picture of the viewer and three scenes at the top, the
  camera in Fluidd, the settings page, and one captured frame in each of the six palettes.
- **A section on the palettes**, and a table of every setting on the settings page with the JSON
  field that changes it at `/thermal/settings`, and an example of changing one.
- **It has run on a second printer**, a Raspberry Pi 4 Klipper printer, started by hand. The hardware
  status section says how that went and what it cost.
- Nothing about the plugin itself changes.

## 0.27.1

- **The plugin's manifest carries its publication dates**, when it was first published and when it
  was last updated, which 0.27.0 left out. Nothing about the plugin itself changes.

## 0.27.0

The first public release, signed by Bespok3d and published as a release candidate: it has run
daily on its maintainer's printer, with every version tested on the hardware, and moves to `stable`
once somebody else has run it too.

- **A thermal camera in Fluidd and Mainsail.** Plug in a Thermal Master P1 or P3 and it appears
  alongside your other cameras, under the name you choose when you install it. The plugin works
  out which model it is.
- **Temperatures in the picture.** A colour bar labelled with the range on screen, the temperature
  at the centre, and markers on the hottest and coldest points, in Celsius or Fahrenheit. Emissivity
  is applied to the readings, the gain switches between high sensitivity and a wide 0 to 550 C
  range, and the sensor can be recalibrated from the settings page.
- **An interactive viewer** at `/thermal/view`: point at the picture to read the temperature there,
  drag a box to measure a region, place up to four spots, zoom and pan, save a still or record a
  clip.
- **A display range you can hold**, so a colour means the same temperature in every frame.
- **Light on the printer.** It stops rendering when nobody is watching, can be switched off
  entirely, and its settings page says what it is costing the printer's processor.
- **It notices a dead picture.** If the connection drops, the viewer says what is wrong in a line
  across the picture, and reconnects by itself when it can.

The P1 is what this has been developed and run against. The P3 is implemented from the same driver
and has not been run on a printer yet: if you have one, a report either way is welcome.

The history before this release, 0.1.0 to 0.26.1, is in the repository as
[`CHANGELOG_DEV.md`](https://github.com/Bespok3d/B3_ThermalMaster/blob/main/CHANGELOG_DEV.md).
